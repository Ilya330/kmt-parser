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

# порядок важен: реальные категории раньше, акции/новинки в конце (дедуп по sku)
CATEGORIES = [
    ("chehly", "Чехлы"),
    ("zashchitnye-stekla-i-plenki", "Защитные стекла и пленки"),
    ("zaryadnye-ustroystva", "Зарядные устройства"),
    ("kabeli-i-perehodniki-1", "Кабели и переходники"),
    ("power-bank", "Power Bank"),
    ("akkumulyatory-1", "Аккумуляторы"),
    ("audio-video-foto", "Аудио-Видео-Фото"),
    ("kompyuternaya-periferiya", "Компьютерная периферия"),
    ("smart-chasy-i-aksessuary", "Смарт-часы и аксессуары"),
    ("avtoaksessuary", "Автоаксессуары"),
    ("gadzhety", "Гаджеты"),
    ("ukrasheniya-dlya-telefonov", "Украшения для телефонов"),
    ("novoe-postuplenie", "Новое поступление"),
    ("akcii", "Акции"),
]

RE_ITEM = re.compile(r'<div class="list-catalog_item">(.*?)</li>', re.S)
RE_HREF = re.compile(r'<a href="(https://kmt5\.com\.ua/[^"]+)" class="list-catalog_thumb')
RE_TITLE = re.compile(r'list-catalog_title">\s*<a href="[^"]+">([^<]+)</a>')
RE_LABEL = re.compile(r'product__label[^"]*">([^<]*)<')
RE_SKU = re.compile(r'box-code">([^<]+)<')
RE_PID = re.compile(r'data-id="(\d+)"')
RE_IMG = re.compile(r'data-src="(https://kmt5\.com\.ua/images/[^"]+)"')
RE_TOTAL = re.compile(r'total__text">(\d+)')
RE_OLD_PRICE = re.compile(r'<span class="price__old">.*?</span>', re.S)


def _num(s):
    s = s.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def price_pair(zone):
    """Из фрагмента цены достать ($, грн). У акционных товаров внутри
    price__old (зачёркнутая) и price__new — старую вырезаем."""
    zone = RE_OLD_PRICE.sub("", zone)
    mu = re.search(r'\$\s*([\d.,]+)', zone)
    mh = re.search(r'([\d][\d\s.,]*)\s*грн', zone)
    return (_num(mu.group(1)) if mu else None,
            _num(mh.group(1)) if mh else None)


def parse_prices(block):
    """(«Ваша цена» $, грн, РРЦ $, грн) из блока листинга или карточки."""
    out = [None, None, None, None]
    my = re.search(r'box-price__name">Ваша цена</span>(.*?)'
                   r'(?:box-price__name|$)', block, re.S)
    if my:
        out[0], out[1] = price_pair(my.group(1))
    mr = re.search(r'box-price__name">РРЦ</span>(.*?)'
                   r'(?:box-price__name|all-quantity-buy|collapse-card|$)', block, re.S)
    if mr:
        out[2], out[3] = price_pair(mr.group(1))
    return out


def parse_page(html, category):
    items = []
    for m in RE_ITEM.finditer(html):
        b = m.group(1)
        href = RE_HREF.search(b)
        sku = RE_SKU.search(b)
        if not href or not sku:
            continue
        it = {
            "url": href.group(1),
            "sku": sku.group(1).strip(),
            "category": category,
        }
        t = RE_TITLE.search(b)
        it["name"] = ihtml.unescape(t.group(1)).strip() if t else ""
        lab = RE_LABEL.search(b)
        it["label"] = lab.group(1).strip() if lab else ""
        pid = RE_PID.search(b)
        if pid:
            it["product_id"] = pid.group(1)
        img = RE_IMG.search(b)
        if img:
            it["image"] = img.group(1)
        pu, ph, ru, rh = parse_prices(b)
        if pu:
            it["price_usd"] = pu
        if ph:
            it["price_uah"] = ph
        if ru:
            it["rrc_usd"] = ru
        if rh:
            it["rrc_uah"] = rh
        items.append(it)
    return items


def crawl_category(cli, slug, title, limit=100, delay=0.15):
    url1 = "%s/%s/?limit=%d" % (BASE, slug, limit)
    html = cli.get(url1)
    mt = RE_TOTAL.search(html)
    total = int(mt.group(1)) if mt else 0
    pages = max(1, -(-total // limit))
    items = parse_page(html, title)

    def fetch(p):
        for attempt in range(3):
            h = cli.get("%s/%s/?limit=%d&page=%d" % (BASE, slug, limit, p))
            got = parse_page(h, title)
            if got:
                return got
            time.sleep(1 + attempt)
        return []

    if pages > 1:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for got in ex.map(fetch, range(2, pages + 1)):
                items.extend(got)
    print("  %s: total=%d, собрано=%d" % (slug, total, len(items)))
    return items


RE_CARD_SKU = re.compile(r'Код товара:</span>\s*([^\s<]+)')
RE_CARD_PID = re.compile(r'button-buy" data-id="(\d+)"')
RE_CARD_H1 = re.compile(r'<h1>(.*?)</h1>', re.S)


def parse_card_listing(html_text, url, category):
    """Собрать из карточки товара запись формата листинга (для товаров,
    которых нет в обходимых категориях)."""
    it = {"url": url, "category": category}
    m = RE_CARD_SKU.search(html_text)
    if not m:
        return None
    it["sku"] = m.group(1).strip()
    m = RE_CARD_PID.search(html_text)
    if m:
        it["product_id"] = m.group(1)
    m = RE_CARD_H1.search(html_text)
    if m:
        it["name"] = ihtml.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
    i = html_text.find("product__price_list")
    zone = html_text[i:i + 6000] if i > 0 else html_text
    pu, ph, ru, rh = parse_prices(zone)
    if pu:
        it["price_usd"] = pu
    if ph:
        it["price_uah"] = ph
    if ru:
        it["rrc_usd"] = ru
    if rh:
        it["rrc_uah"] = rh
    lab = RE_LABEL.search(html_text)
    if lab:
        it["label"] = lab.group(1).strip()
    return it


def fetch_their_feed(cli):
    """Скачать публичный фид сайта → ({url: offer_id}, {url: name})."""
    xml = cli.get(THEIR_FEED_URL, timeout=120)
    ids, names = {}, {}
    for m in re.finditer(
            r'<offer id="([^"]+)"[^>]*>.*?<url>([^<]+)</url>(?:.*?<name>([^<]*)</name>)?.*?</offer>',
            xml, re.S):
        ids[m.group(2)] = m.group(1)
        if m.group(3):
            names[m.group(2)] = ihtml.unescape(m.group(3))
    return ids, names


def main():
    cli = Client()
    cli.ensure_login()
    # дедуп по product_id: один Ц-код покрывает цветовые варианты,
    # у каждого варианта свой product_id/URL/имя
    seen = {}
    for slug, title in CATEGORIES:
        for it in crawl_category(cli, slug, title):
            key = it.get("product_id") or it["url"]
            if key not in seen:
                seen[key] = it

    # их публичный фид: offer id (ts…) + товары из скрытых категорий
    try:
        their_ids, _ = fetch_their_feed(cli)
    except Exception as e:
        print("Их фид не скачался (%s) — offer id будут фолбэчные" % e)
        their_ids = {}
    save_json("their_ids.json", their_ids)
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
    with ThreadPoolExecutor(max_workers=6) as ex:
        for u, it in zip(extras, ex.map(fetch_extra, extras)):
            if it and it.get("price_usd"):
                key = it.get("product_id") or it["url"]
                if key not in seen:
                    seen[key] = it
                    added += 1
            else:
                print("  ПРОПУЩЕН extra (%s): %s"
                      % ("нет цены" if it else "не распарсился", u))
    print("Добрано из их фида: %d" % added)

    out = list(seen.values())
    with_price = sum(1 for x in out if x.get("price_usd"))
    print("Уникальных SKU: %d, с ценой: %d" % (len(out), with_price))
    if len(out) < 3000 or with_price < len(out) * 0.5:
        print("ПОДОЗРИТЕЛЬНО МАЛО ДАННЫХ — не сохраняю", file=sys.stderr)
        sys.exit(1)
    save_json("listings.json", out)
    cli.save_cookies()


if __name__ == "__main__":
    main()
