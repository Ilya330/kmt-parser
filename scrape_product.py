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
import time
from concurrent.futures import ThreadPoolExecutor

from kmt import Client, load_json, save_json

RE_CODE2 = re.compile(r'<span>Код:</span>\s*([0-9]+)')
RE_ATTR = re.compile(
    r'list-description_left">([^<]+)</div>\s*'
    r'<div class="list-description_right">([^<]*)</div>', re.S)
RE_DESCR = re.compile(r'<div class="text-description">(.*?)</div>', re.S)
RE_IMG = re.compile(r'https://kmt5\.com\.ua/images/([A-Za-z0-9+/=]+)\.jpg')
RE_CRUMB = re.compile(r'<ul class="breadcrumbs[^"]*">(.*?)</ul>', re.S)
RE_CRUMB_A = re.compile(r'<a href="[^"]*">([^<]+)</a>')
RE_PRICE_ITEM = re.compile(
    r'box-price__name">([^<]+)</span>(.*?)(?=box-price__name|collapse-card)', re.S)
RE_MONEY = re.compile(r'\$\s*([\d.,]+)|([\d\s.,]+)\s*грн')


def clean_text(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    return ihtml.unescape(re.sub(r'\s+', ' ', s)).strip()


def parse_card(html_text, sku):
    card = {}
    m = RE_CODE2.search(html_text)
    if m:
        card["code"] = m.group(1)
    # характеристики — только первый блок hidden-shot (основной товар)
    zone = html_text
    i = html_text.find("box-card_right")
    if i > 0:
        j = html_text.find("product__price_list", i)
        zone = html_text[i:j if j > 0 else i + 20000]
    attrs = []
    for a, v in RE_ATTR.findall(zone):
        a, v = clean_text(a).rstrip(":"), clean_text(v)
        if a and v:
            attrs.append([a, v])
    card["attrs"] = attrs
    md = RE_DESCR.search(html_text)
    if md:
        d = clean_text(md.group(1))
        if d:
            card["description"] = d
    # фото: только своего Ц-кода в 1000x1000
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
    mc = RE_CRUMB.search(html_text)
    if mc:
        crumbs = [clean_text(x) for x in RE_CRUMB_A.findall(mc.group(1))]
        crumbs = [c for c in crumbs if c and c != "Главная"]
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

    def fetch(it):
        try:
            h = cli.get(it["url"])
            card = parse_card(h, it["sku"])
            card["url"] = it["url"]
            card["name"] = it.get("name", "")
            done[0] += 1
            if done[0] % 200 == 0:
                print("  ...%d/%d" % (done[0], len(todo)))
                save_json("catalog.json", catalog)
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
