# -*- coding: utf-8 -*-
"""Сборка YML-фида docs/feed.xml из listings.json + catalog.json + qty.json.

Теги цен: <price> = РРЦ грн, <vendorprice> = «Ваша цена» грн,
<vendorpricedoll> = «Ваша цена» $. Наличие: <quantity_in_stock> — точное
число из qty.json, при отсутствии — оценка по метке
(много=100, в наличии=10, мало=3).
"""
import html
import time
import zlib

from kmt import load_json, save_json

LABEL_QTY = {"много": 100, "в наличии": 10, "мало": 3}


def esc(s):
    return html.escape(str(s), quote=False).replace('"', "&quot;")


def cat_id(path_tuple):
    return zlib.crc32(" / ".join(path_tuple).encode("utf-8")) % 10 ** 8


def main():
    listings = load_json("listings.json", [])
    catalog = load_json("catalog.json", {})
    qty = load_json("qty.json", {})

    # дерево категорий из хлебных крошек
    cats = {}  # (path...) -> id
    offers = []
    skipped = 0
    for it in listings:
        price_usd = it.get("price_usd")
        price_uah = it.get("price_uah")
        if not price_usd or not price_uah:
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
    for it, card, path, name, n in offers:
        oid = it.get("product_id") or card.get("code") or it["sku"].replace("Ц-", "C")
        w('<offer id="%s" available="%s">' % (esc(oid), "true" if n > 0 else "false"))
        w("<url>%s</url>" % esc(it["url"]))
        w("<price>%s</price>" % (it.get("rrc_uah") or it["price_uah"]))
        w("<vendorprice>%s</vendorprice>" % it["price_uah"])
        w("<vendorpricedoll>%s</vendorpricedoll>" % it["price_usd"])
        if it.get("rrc_usd"):
            w("<rrcdoll>%s</rrcdoll>" % it["rrc_usd"])
        w("<currencyId>UAH</currencyId>")
        w("<categoryId>%d</categoryId>" % cats[path])
        pics = card.get("pictures") or ([it["image"]] if it.get("image") else [])
        for p in pics[:10]:
            w("<picture>%s</picture>" % esc(p))
        vendor = next((v for a, v in card.get("attrs", []) if a == "Бренд"), None)
        if vendor:
            w("<vendor>%s</vendor>" % esc(vendor))
        w("<vendorCode>%s</vendorCode>" % esc(it["sku"]))
        if card.get("code"):
            w("<code>%s</code>" % esc(card["code"]))
        w("<name>%s</name>" % esc(name))
        if card.get("description"):
            w("<description><![CDATA[%s]]></description>"
              % card["description"].replace("]]>", "]] >"))
        w("<quantity_in_stock>%d</quantity_in_stock>" % n)
        for a, v in card.get("attrs", []):
            w('<param name="%s">%s</param>' % (esc(a), esc(v)))
        w("</offer>")
    w("</offers>")
    w("</shop>")
    w("</yml_catalog>")

    xml = "\n".join(out)
    with open("docs/feed.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    with_qty = sum(1 for it, *_ in offers if str(it.get("product_id")) in qty)
    print("feed.xml: %d офферов (%d с точным остатком, %d пропущено без цены), %.1f МБ"
          % (len(offers), with_qty, skipped, len(xml.encode()) / 1e6))


if __name__ == "__main__":
    main()
