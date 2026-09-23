# -*- coding: utf-8 -*-
"""Сборка YML-фида docs/feed.xml из listings.json + catalog.json + meta.json.

Теги цен: <price> = РРЦ грн, <vendorprice> = «Ваша цена» грн,
<vendorpricedoll> = «Ваша цена» $, <rrcdoll> = РРЦ $.
После редизайна сайта (23.09.2026) цены на страницах только в долларах —
гривну считаем сами по курсу из шапки сайта (meta.json).
Наличие: <quantity_in_stock> — точное число из листинга (data-max),
фолбэком старый замер qty.json, затем оценка по метке.
"""
import html
import re
import time
import zlib

from kmt import load_json, save_json

LABEL_QTY = {"много": 100, "в наличии": 10, "мало": 3,
             "в наявності": 10, "закінчується": 3, "багато": 100}


def esc(s):
    return html.escape(str(s), quote=False).replace('"', "&quot;")


def cat_id(path_tuple):
    return zlib.crc32(" / ".join(path_tuple).encode("utf-8")) % 10 ** 8


def main():
    listings = load_json("listings.json", [])
    catalog = load_json("catalog.json", {})
    qty = load_json("qty.json", {})
    their_ids = load_json("their_ids.json", {})
    their_codes = load_json("their_codes.json", {})
    rate = (load_json("meta.json", {}) or {}).get("rate")
    if not rate:
        raise SystemExit("meta.json без курса — гривну считать не из чего")
    print("курс: %.2f грн/$" % rate)

    def uah(usd):
        return round(usd * rate) if usd else None

    # дерево категорий из хлебных крошек
    cats = {}  # (path...) -> id
    offers = []
    skipped = 0
    for it in listings:
        price_usd = it.get("price_usd")
        if not price_usd:
            skipped += 1
            continue
        card = catalog.get(it["url"], {})
        crumbs = card.get("breadcrumbs") or [it.get("category", "Разное")]
        # последняя крошка на карточке = название товара? нет: крошки без товара,
        # но подстрахуемся — уберём крошку, совпадающую с именем
        name = it.get("name") or card.get("name") or it["sku"]
        if crumbs and crumbs[-1].strip().lower() == name.strip().lower():
            crumbs = crumbs[:-1] or [it.get("category", "Разное")]
        for i in range(1, len(crumbs) + 1):
            cats.setdefault(tuple(crumbs[:i]), None)
        # остаток: сайт сам отдаёт точное число в листинге (сверено с корзиной)
        n = it.get("qty")
        if n is None:
            n = qty.get(str(it.get("product_id")))
        if n is None:
            n = LABEL_QTY.get(it.get("label", "").lower(), 10)
        offers.append((it, card, tuple(crumbs), name, n))

    for path in sorted(cats):
        cats[path] = cat_id(path)

    now = time.strftime("%Y-%m-%d %H:%M")
    out = []
    w = out.append
    w('<?xml version="1.0" encoding="UTF-8"?>')
    w('<yml_catalog date="%s">' % now)
    w("<shop>")
    w("<name>KMT5</name><company>KMT5</company><url>https://kmt5.com.ua/</url>")
    w('<currencies><currency id="UAH" rate="1"/></currencies>')
    w("<categories>")
    for path in sorted(cats):
        pid = cats[path[:-1]] if len(path) > 1 else None
        if pid:
            w('<category id="%d" parentId="%d">%s</category>' % (cats[path], pid, esc(path[-1])))
        else:
            w('<category id="%d">%s</category>' % (cats[path], esc(path[-1])))
    w("</categories>")
    w("<offers>")
    used_ids = set()
    for it, card, path, name, n in offers:
        # offer id как в фиде поставщика (ts…); фолбэк для отсутствующих там:
        # ts + цифры Ц-кода (+ product_id, если такой id уже занят вариантом)
        oid = their_ids.get(it["url"])
        if not oid:
            base = "ts" + re.sub(r"\D", "", it["sku"])
            oid = base if base not in used_ids else "%s-%s" % (base, it.get("product_id") or len(used_ids))
        if oid in used_ids:
            oid = "%s-%s" % (oid, it.get("product_id") or len(used_ids))
        used_ids.add(oid)
        w('<offer id="%s" available="%s">' % (esc(oid), "true" if n > 0 else "false"))
        w("<url>%s</url>" % esc(it["url"]))
        # РРЦ живёт только на карточке (в листинге её больше нет) — из кеша
        rrc_usd = card.get("rrc_usd")
        price_usd = it["price_usd"]
        w("<price>%s</price>" % (uah(rrc_usd) or uah(price_usd)))
        w("<vendorprice>%s</vendorprice>" % uah(price_usd))
        w("<vendorpricedoll>%s</vendorpricedoll>" % price_usd)
        if rrc_usd:
            w("<rrcdoll>%s</rrcdoll>" % rrc_usd)
        w("<currencyId>UAH</currencyId>")
        w("<categoryId>%d</categoryId>" % cats[path])
        pics = card.get("pictures") or ([it["image"]] if it.get("image") else [])
        for p in pics[:10]:
            w("<picture>%s</picture>" % esc(p))
        vendor = (it.get("brand") or card.get("brand")
                  or next((v for a, v in card.get("attrs", []) if a == "Бренд"), None))
        if vendor:
            w("<vendor>%s</vendor>" % esc(vendor))
        w("<vendorCode>%s</vendorCode>" % esc(it["sku"]))
        code = card.get("code") or their_codes.get(it["url"])
        if code:
            w("<code>%s</code>" % esc(code))
        w("<name>%s</name>" % esc(name))
        if card.get("description"):
            w("<description><![CDATA[%s]]></description>"
              % card["description"].replace("]]>", "]] >"))
        w("<quantity_in_stock>%d</quantity_in_stock>" % n)
        for a, v in card.get("attrs", []):
            w('<param name="%s">%s</param>' % (esc(a), esc(v)))
        # ступенчатые опт-цены («от N шт — $X»), их отдаёт и листинг, и карточка
        for q, pr in (it.get("ladder") or card.get("ladder") or []):
            w('<param name="Опт від %d шт">$%s</param>' % (q, pr))
        if card.get("drop_usd"):
            w('<param name="Drop">$%s</param>' % card["drop_usd"])
        w("</offer>")
    w("</offers>")
    w("</shop>")
    w("</yml_catalog>")

    xml = "\n".join(out)
    with open("docs/feed.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    with_qty = sum(1 for it, *_ in offers if it.get("qty") is not None)
    with_rrc = sum(1 for _, c, *_ in offers if c.get("rrc_usd"))
    print("feed.xml: %d офферов (%d с остатком от сайта, %d с РРЦ, %d без цены), %.1f МБ"
          % (len(offers), with_qty, with_rrc, skipped, len(xml.encode()) / 1e6))


if __name__ == "__main__":
    main()
