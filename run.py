# -*- coding: utf-8 -*-
"""Оркестратор. Шаги через env (1/0):
STEP_LISTINGS (деф 1) — обход листингов (цены, метки наличия)
STEP_CARDS    (деф 1) — до-скрейп карточек новых SKU (REFRESH=1 — все)
STEP_QTY      (деф 0) — точный остаток через корзину (долгий, чистит корзину!)
STEP_FEED     (деф 1) — сборка docs/feed.xml
"""
import os
import subprocess
import sys


def step(name, script, default="1"):
    if os.environ.get(name, default) != "1":
        print("== %s пропущен" % script)
        return
    print("== %s" % script)
    r = subprocess.run([sys.executable, script])
    if r.returncode != 0:
        sys.exit(r.returncode)


step("STEP_LISTINGS", "crawl_listings.py")
step("STEP_CARDS", "scrape_product.py")
step("STEP_QTY", "probe_qty.py", "0")
step("STEP_FEED", "build_feed.py")
print("Готово.")
