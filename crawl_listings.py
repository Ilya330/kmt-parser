# -*- coding: utf-8 -*-
"""Обход листингов категорий → listings.json.

Для каждого товара из страниц каталога (limit=100):
url, name, sku (Ц-код), product_id, label (много/в наличии/...),
price_usd/price_uah (Ваша цена), rrc_usd/rrc_uah, image, category (верхнего уровня).
"""
import html as ihtml
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import os

from kmt import BASE, Client, save_json

# публичный YML-фид сайта (без логина; цены только опт-грн) — источник
# offer id (ts…) и товаров из скрытых категорий («Все для дому» и т.п.)
THEIR_FEED_URL = os.environ.get(
    "THEIR_FEED_URL", BASE + "/feed/ahr3v3xs1uplbkssusncsrcxqsntzhpt")

# Категории обнаруживаются динамически из меню главной: сайт переименовывает
# слаги и переносит разделы (в авг.2026 «Чехлы»/chehly исчезли, разъехавшись
# на «Чохли для телефонів/планшетів/навушників» доступные только по path).
# Служебные ссылки, которые не являются категориями:
NON_CATEGORY = {
    "my-account", "shopping-cart", "commercial-proposal", "login", "logout",
    "create-account", "forgot-password", "wishlist", "brands", "contact-us",
    "about-us", "garantija", "search", "blog", "news", "feed", "price_list",
    "access-denied", "checkout", "compare-products", "specials", "sitemap",
}
# в конец очереди — сборные разделы (дедуп по product_id оставит первый источник)
LAST = ("akcii", "novoe-postuplenie")

# --- новая вёрстка (редизайн 2026-09-23) ---
# карточка листинга: <article class="pc" data-card="ID"> … </article>
RE_ITEM = re.compile(r'<article class="pc" data-card="(\d+)">(.*?)</article>', re.S)
RE_HREF = re.compile(r'<a class="pc-th" href="([^"]+)"')
RE_TITLE = re.compile(r'<div class="pc-name"><a[^>]*>(.*?)</a>', re.S)
RE_SKU = re.compile(r'<div class="pc-brand"><b>([^<]*)</b><span>([^<]+)</span>')
RE_IMG = re.compile(r'<img class="ph a" src="([^"]+)"')
RE_LABEL = re.compile(r'<span class="bg (ok|low|no)[^"]*">([^<]*)</span>')
RE_STOCK = re.compile(r'<div class="qty" data-q="\d+" data-max="(\d+)"')
# «Ваша цена»: <span class="opt" data-pdprice data-baseprice="4.00">$3.78</span>
RE_PRICE = re.compile(r'class="opt" data-pdprice data-baseprice="([\d.]+)">\$?([\d.]+)<')
# ступенчатые опт-цены: data-qty="5" data-price="3.52"
RE_LADDER = re.compile(r'data-qty="(\d+)" data-price="([\d.]+)"')
# «Показано с 1 по 100 из 3398 (всего 34 страниц)»
RE_TOTAL = re.compile(r'Показано\s+с\s+\d+\s+по\s+\d+\s+из\s+(\d+)')
RE_H1 = re.compile(r'<h1[^>]*>(.*?)</h1>', re.S)
RE_CAT_LINK = re.compile(
    r'href="(https://kmt5\.com\.ua/(?:[a-z0-9\-]+/|index\.php\?route=product/category&amp;path=\d+))"')


def _num(s):
    try:
        return float(str(s).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def parse_page(html_text, category):
    """Товары со страницы листинга. Остаток берём прямо из data-max —
    после редизайна сайт отдаёт точное число (сверено с корзиной)."""
    items = []
    for m in RE_ITEM.finditer(html_text):
        pid, b = m.group(1), m.group(2)
        href, sku = RE_HREF.search(b), RE_SKU.search(b)
        if not href or not sku:
            continue
        it = {"url": href.group(1), "sku": sku.group(2).strip(),
              "product_id": pid, "category": category}
        brand = sku.group(1).strip()
        if brand:
            it["brand"] = ihtml.unescape(brand)
        t = RE_TITLE.search(b)
        if t:
            it["name"] = ihtml.unescape(re.sub(r'<[^>]+>', '', t.group(1))).strip()
        lab = RE_LABEL.search(b)
        if lab:
            it["label"] = lab.group(2).strip()
        img = RE_IMG.search(b)
        if img:
            it["image"] = img.group(1)
        st = RE_STOCK.search(b)
        if st:
            it["qty"] = int(st.group(1))
        p = RE_PRICE.search(b)
        if p:
            it["price_usd"] = _num(p.group(2))
            base = _num(p.group(1))
            if base and base != it["price_usd"]:
                it["price_usd_base"] = base
        ladder = [(int(q), _num(pr)) for q, pr in RE_LADDER.findall(b)]
        if ladder:
            it["ladder"] = ladder
        items.append(it)
    return items


def discover_categories(cli):
    """Корневые категории из меню главной: [(url, ключ)].
    Слаги и path-категории вперемешку — сайт использует оба вида."""
    home = cli.get(BASE + "/")
    urls = []
    for m in RE_CAT_LINK.finditer(home):
        u = m.group(1).replace("&amp;", "&")
        tail = u.rstrip("/").rsplit("/", 1)[-1]
        if "path=" not in u and tail in NON_CATEGORY:
            continue
        if u not in urls:
            urls.append(u)
    urls.sort(key=lambda u: 1 if any(s in u for s in LAST) else 0)
    return urls


def page_url(cat_url, limit, page=1):
    sep = "&" if "?" in cat_url else "?"
    u = "%s%slimit=%d" % (cat_url, sep, limit)
    return u if page == 1 else u + "&page=%d" % page


def crawl_category(cli, cat_url, limit=100):
    html = cli.get(page_url(cat_url, limit))
    mt = RE_TOTAL.search(html)
    total = int(mt.group(1)) if mt else 0
    mh = RE_H1.search(html)
    title = ihtml.unescape(re.sub(r'<[^>]+>', '', mh.group(1))).strip() if mh else cat_url
    pages = max(1, -(-total // limit))
    items = parse_page(html, title)

    def fetch(p):
        for attempt in range(3):
            got = parse_page(cli.get(page_url(cat_url, limit, p)), title)
            if got:
                return got
            time.sleep(1 + attempt)
        return []

    if pages > 1:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for got in ex.map(fetch, range(2, pages + 1)):
                items.extend(got)
    print("  %s [%s]: total=%d, собрано=%d"
          % (title, cat_url.rsplit("/", 1)[-1][:28], total, len(items)))
    if total and len(items) < total * 0.9:
        print("    ВНИМАНИЕ: собрано меньше 90%% от заявленного")
    return items


# карточка товара (новая вёрстка): «Арт. Ц-…», h1.p-title, цена в buybox
RE_CARD_SKU = re.compile(r'<span class="art">\s*Арт\.\s*([^\s<]+)\s*</span>')
RE_CARD_PID = re.compile(r'<button class="add" type="button" data-add="(\d+)"')
RE_CARD_H1 = re.compile(r'<h1[^>]*class="p-title"[^>]*>(.*?)</h1>', re.S)
RE_CARD_STOCK = re.compile(r'<div class="qty" data-q="\d+" data-max="(\d+)"')
RE_CARD_BRAND = re.compile(r'<div class="p-sub">\s*<b>([^<]*)</b>')


def parse_card_listing(html_text, url, category):
    """Собрать из карточки товара запись формата листинга (для товаров,
    которых нет в обходимых категориях)."""
    m = RE_CARD_SKU.search(html_text)
    if not m:
        return None
    it = {"url": url, "category": category, "sku": m.group(1).strip()}
    m = RE_CARD_PID.search(html_text)
    if m:
        it["product_id"] = m.group(1)
    m = RE_CARD_H1.search(html_text)
    if m:
        it["name"] = ihtml.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
    m = RE_CARD_BRAND.search(html_text)
    if m and m.group(1).strip():
        it["brand"] = ihtml.unescape(m.group(1).strip())
    m = RE_CARD_STOCK.search(html_text)
    if m:
        it["qty"] = int(m.group(1))
    i = html_text.find("bb-price")
    zone = html_text[i:i + 3000] if i > 0 else html_text
    p = RE_PRICE.search(zone)
    if p:
        it["price_usd"] = _num(p.group(2))
        base = _num(p.group(1))
        if base and base != it["price_usd"]:
            it["price_usd_base"] = base
    ladder = [(int(q), _num(pr)) for q, pr in RE_LADDER.findall(zone)]
    if ladder:
        it["ladder"] = ladder
    lab = RE_LABEL.search(html_text)
    if lab:
        it["label"] = lab.group(2).strip()
    return it


def fetch_their_feed(cli):
    """Скачать публичный фид сайта → ({url: offer_id}, {url: числовой код}).
    Числовой «Код» после редизайна пропал с карточек, но остался в их фиде."""
    xml = cli.get(THEIR_FEED_URL, timeout=120)
    ids, codes = {}, {}
    for m in re.finditer(
            r'<offer id="([^"]+)"[^>]*>.*?<url>([^<]+)</url>(?:.*?<name>([^<]*)</name>)?.*?</offer>',
            xml, re.S):
        ids[m.group(2)] = m.group(1)
        mc = re.search(r'<param name="Код">([^<]+)</param>', m.group(0))
        if mc:
            codes[m.group(2)] = mc.group(1).strip()
    return ids, codes


def main():
    cli = Client()
    cli.ensure_login()
    # дедуп по product_id: один Ц-код покрывает цветовые варианты,
    # у каждого варианта свой product_id/URL/имя
    cat_urls = discover_categories(cli)
    print("Категорий в меню: %d" % len(cat_urls))
    if len(cat_urls) < 8:
        print("Меню отдало подозрительно мало категорий", file=sys.stderr)
        sys.exit(1)
    seen = {}
    for cat_url in cat_urls:
        try:
            found = crawl_category(cli, cat_url)
        except Exception as e:
            print("  ERR категория %s: %s" % (cat_url, e))
            continue
        for it in found:
            key = it.get("product_id") or it["url"]
            if key not in seen:
                seen[key] = it

    # их публичный фид: offer id (ts…) + товары из скрытых категорий
    try:
        their_ids, their_codes = fetch_their_feed(cli)
    except Exception as e:
        print("Их фид не скачался (%s) — offer id будут фолбэчные" % e)
        their_ids, their_codes = {}, {}
    save_json("their_ids.json", their_ids)
    if their_codes:
        save_json("their_codes.json", their_codes)
    have_urls = set(it["url"] for it in seen.values())
    extras = [u for u in their_ids if u not in have_urls]
    print("Их фид: %d офферов, вне наших категорий: %d" % (len(their_ids), len(extras)))

    def fetch_extra(u):
        try:
            return parse_card_listing(cli.get(u), u, "Все для дому")
        except Exception as e:
            print("  ERR extra %s: %s" % (u, e))
            return None

    added = 0
    skipped_extra = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for u, it in zip(extras, ex.map(fetch_extra, extras)):
            if it and it.get("price_usd"):
                key = it.get("product_id") or it["url"]
                if key not in seen:
                    seen[key] = it
                    added += 1
            else:
                skipped_extra += 1
    if skipped_extra:
        print("  extras без цены/непарсящихся: %d (их фид отстаёт от каталога)"
              % skipped_extra)
    print("Добрано из их фида: %d" % added)

    out = list(seen.values())
    with_price = sum(1 for x in out if x.get("price_usd"))
    print("Уникальных SKU: %d, с ценой: %d" % (len(out), with_price))
    if len(out) < 3000 or with_price < len(out) * 0.5:
        print("ПОДОЗРИТЕЛЬНО МАЛО ДАННЫХ — не сохраняю", file=sys.stderr)
        sys.exit(1)
    save_json("listings.json", out)
    # курс доллара: цены на сайте теперь только в $, грн считаем сами
    try:
        save_json("meta.json", {"rate": cli.rate(),
                                "updated": time.strftime("%Y-%m-%d %H:%M")})
    except Exception as e:
        print("Курс не сохранён: %s" % e)
    cli.save_cookies()


if __name__ == "__main__":
    main()
