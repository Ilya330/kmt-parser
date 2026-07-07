# -*- coding: utf-8 -*-
"""Точное наличие через корзину → qty.json (ключ = product_id).

Сайт не показывает число на складе (только метки «много»/«в наличии»),
но корзина обрезает заказ до фактического остатка:
POST checkout/cart/add quantity=9999 → in_cart = реальный остаток.

⚠️ Скрипт работает С КОРЗИНОЙ АККАУНТА: перед началом и в конце корзина
ПОЛНОСТЬЮ ОЧИЩАЕТСЯ. Не запускать, пока в корзине лежит реальный заказ.

Батчами: добавили BATCH товаров (in_cart из ответа) → прочитали ключи
корзины → удалили всё. Env: WORKERS (деф 6), BATCH (деф 100), LIMIT.
"""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from kmt import BASE, Client, load_json, save_json

RE_KEY = re.compile(r'name="quantity\[(\d+)\]"')
PROBE_QTY = 9999


def cart_keys(cli):
    h = cli.get(BASE + "/shopping-cart/")
    return RE_KEY.findall(h)


def clear_cart(cli, workers=6):
    keys = cart_keys(cli)
    if not keys:
        return 0
    def rm(k):
        try:
            cli.get_json(BASE + "/index.php?route=checkout/cart&remove=%s&ajax=1" % k)
        except Exception as e:
            print("  ERR remove %s: %s" % (k, e))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(rm, keys))
    left = cart_keys(cli)
    if left:
        print("  ВНИМАНИЕ: в корзине осталось %d позиций" % len(left))
    return len(keys)


def main():
    cli = Client()
    cli.ensure_login()
    listings = load_json("listings.json", [])
    limit = int(os.environ.get("LIMIT", "0"))
    workers = int(os.environ.get("WORKERS", "6"))
    batch = int(os.environ.get("BATCH", "100"))

    todo = [it for it in listings if it.get("product_id")]
    if limit:
        todo = todo[:limit]
    print("Товаров к замеру: %d" % len(todo))

    removed = clear_cart(cli, workers)
    if removed:
        print("Корзина очищена перед стартом: %d позиций" % removed)

    qty = {}
    err = 0

    def probe(it):
        try:
            j = cli.get_json(
                BASE + "/index.php?route=checkout/cart/add",
                data={"product_id": it["product_id"], "quantity": PROBE_QTY})
            if "in_cart" in j:
                return it["product_id"], int(str(j["in_cart"]).replace(" ", "") or 0)
            # не добавился (нет в наличии и т.п.)
            return it["product_id"], 0
        except Exception:
            return it["product_id"], None

    t0 = time.time()
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for pid, n in ex.map(probe, chunk):
                if n is None:
                    err += 1
                else:
                    qty[pid] = n
        clear_cart(cli, workers)
        done = min(i + batch, len(todo))
        rate = done / max(1e-9, time.time() - t0)
        print("  %d/%d (%.1f тов/с), ошибок %d" % (done, len(todo), rate, err))
        save_json("qty.json", qty)

    save_json("qty.json", qty)
    print("qty.json: %d SKU, ошибок %d" % (len(qty), err))
    if err > len(todo) * 0.2:
        print("Слишком много ошибок замера", file=sys.stderr)
        sys.exit(1)
    cli.save_cookies()


if __name__ == "__main__":
    main()
