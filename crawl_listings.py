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

from kmt import BASE, Client, save_json

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
# блок «Ваша цена»: $ и грн
RE_YOUR = re.compile(
    r'box-price__name">Ваша цена</span>.*?box-price_dollar">\s*\$?([\d.,]+).*?'
    r'box-price_hryvnia">([\d\s.,]+)\s*грн', re.S)
RE_RRC = re.compile(
    r'box-price__name">РРЦ</span>.*?box-price_dollar">\s*\$?([\d.,]+).*?'
    r'box-price_hryvnia">([\d\s.,]+)\s*грн', re.S)


def _num(s):
    s = s.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


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
        yp = RE_YOUR.search(b)
        if yp:
            it["price_usd"] = _num(yp.group(1))
            it["price_uah"] = _num(yp.group(2))
        rp = RE_RRC.search(b)
        if rp:
            it["rrc_usd"] = _num(rp.group(1))
            it["rrc_uah"] = _num(rp.group(2))
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
