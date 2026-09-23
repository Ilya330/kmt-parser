# -*- coding: utf-8 -*-
"""Скрейп карточек товара → catalog.json (мастер-кеш тяжёлой информации, ключ=URL).

Из карточки берём: числовой код («Код»), характеристики (list-description),
описание (text-description), фото 1000x1000 своего Ц-кода, хлебные крошки
(категория), цены Drop/Опт. Наличие и «Ваша цена» сюда НЕ пишем — они из
listings.json каждый прогон.

Env: REFRESH=1 — пере-скрейпить все карточки, иначе только новые SKU.
WORKERS (деф 6), LIMIT (деф 0 = без лимита, для теста).
"""
import base64
import html as ihtml
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from kmt import Client, load_json, save_json

# --- новая вёрстка карточки (редизайн 2026-09-23) ---
RE_SKU = re.compile(r'<span class="art">\s*Арт\.\s*([^\s<]+)\s*</span>')
RE_TITLE = re.compile(r'<h1[^>]*class="p-title"[^>]*>(.*?)</h1>', re.S)
RE_BRAND = re.compile(r'<div class="p-sub">\s*<b>([^<]*)</b>')
# характеристики: <div class="spec"><div><span>Имя</span><b>Значение</b></div>…
RE_SPEC_BLOCK = re.compile(r'<div class="spec">(.*?)</div>\s*</div>', re.S)
RE_SPEC_ROW = re.compile(r'<div><span>([^<]+)</span><b>([^<]*)</b></div>')
RE_DESCR = re.compile(r'<div data-pane="desc">(.*?)</div>', re.S)
RE_IMG = re.compile(r'https://kmt5\.com\.ua/images/([A-Za-z0-9+/=]+)\.jpg')
RE_CRUMB_BLOCK = re.compile(r'<div class="crumbs">(.*?)</div>', re.S)
RE_CRUMB_A = re.compile(r'<a href="[^"]*"[^>]*>([^<>]+)</a>')
# цены в buybox: «Ваша цена», зачёркнутая, РРЦ, Drop, Опт
RE_YOUR = re.compile(r'class="opt" data-pdprice data-baseprice="([\d.]+)">\$?([\d.]+)<')
RE_RRC = re.compile(r'РРЦ\s*\$?([\d.]+)')
RE_DROP = re.compile(r'Drop:\s*<b[^>]*>\$?([\d.]+)')
RE_OPT = re.compile(r'Опт:\s*<b[^>]*>\$?([\d.]+)')
RE_LADDER = re.compile(r'data-qty="(\d+)" data-price="([\d.]+)"')


def clean_text(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    return ihtml.unescape(re.sub(r'\s+', ' ', s)).strip()


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_card(html_text, sku):
    """Тяжёлые данные карточки: характеристики, описание, фото, РРЦ.
    Цены Ваша/Drop/Опт тоже кешируем — РРЦ в листинге больше нет."""
    card = {}
    m = RE_SKU.search(html_text)
    if m:
        card["sku"] = m.group(1).strip()
    m = RE_TITLE.search(html_text)
    if m:
        card["name"] = clean_text(m.group(1))
    m = RE_BRAND.search(html_text)
    if m and m.group(1).strip():
        card["brand"] = ihtml.unescape(m.group(1).strip())

    attrs = []
    mb = RE_SPEC_BLOCK.search(html_text)
    if mb:
        for a, v in RE_SPEC_ROW.findall(mb.group(1)):
            a, v = clean_text(a).rstrip(":"), clean_text(v)
            if a and v:
                attrs.append([a, v])
    card["attrs"] = attrs

    md = RE_DESCR.search(html_text)
    if md:
        d = clean_text(md.group(1))
        if d:
            card["description"] = d

    # фото: только своего Ц-кода, в максимальном размере 1000x1000
    pics, seen = [], set()
    for b in RE_IMG.findall(html_text):
        try:
            path = base64.b64decode(b).decode("utf-8", "ignore")
        except Exception:
            continue
        if sku in path and path.endswith(":1000:1000"):
            u = "https://kmt5.com.ua/images/%s.jpg" % b
            if u not in seen:
                seen.add(u)
                pics.append(u)
    card["pictures"] = pics

    i = html_text.find("bb-price")
    zone = html_text[i:i + 3000] if i > 0 else html_text
    m = RE_YOUR.search(zone)
    if m:
        card["price_usd"] = _num(m.group(2))
    for key, rx in (("rrc_usd", RE_RRC), ("drop_usd", RE_DROP), ("opt_usd", RE_OPT)):
        m = rx.search(zone)
        if m:
            card[key] = _num(m.group(1))
    ladder = [(int(q), _num(p)) for q, p in RE_LADDER.findall(zone)]
    if ladder:
        card["ladder"] = ladder

    mc = RE_CRUMB_BLOCK.search(html_text)
    if mc:
        crumbs = [clean_text(c) for c in RE_CRUMB_A.findall(mc.group(1))]
        crumbs = [c for c in crumbs if c and c not in ("Главная", "Головна")]
        if crumbs:
            card["breadcrumbs"] = crumbs
    return card


def main():
    cli = Client()
    cli.ensure_login()
    listings = load_json("listings.json", [])
    catalog = load_json("catalog.json", {})
    refresh = os.environ.get("REFRESH") == "1"
    limit = int(os.environ.get("LIMIT", "0"))
    workers = int(os.environ.get("WORKERS", "6"))

    todo = [it for it in listings if refresh or it["url"] not in catalog]
    if limit:
        todo = todo[:limit]
    print("Карточек к скрейпу: %d (кеш: %d)" % (len(todo), len(catalog)))

    done = [0]
    lock = threading.Lock()

    def fetch(it):
        try:
            h = cli.get(it["url"])
            card = parse_card(h, it["sku"])
            card["url"] = it["url"]
            card["name"] = it.get("name", "")
            done[0] += 1
            if done[0] % 200 == 0:
                print("  ...%d/%d" % (done[0], len(todo)))
                # снимок под замком: словарь наполняют другие потоки
                with lock:
                    save_json("catalog.json", dict(catalog))
            return it["url"], card
        except Exception as e:
            print("  ERR %s: %s" % (it["url"], e))
            return it["url"], None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for url, card in ex.map(fetch, todo):
            if card is not None:
                catalog[url] = card
            time.sleep(0)

    save_json("catalog.json", catalog)
    print("catalog.json: %d карточек" % len(catalog))
    cli.save_cookies()


if __name__ == "__main__":
    main()
