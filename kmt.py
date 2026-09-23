# -*- coding: utf-8 -*-
"""Общий HTTP-клиент kmt5.com.ua: логин, cookie-сессия, fetch с ретраями."""
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://kmt5.com.ua"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
RE_RATE = re.compile(r'rate-badge">[^<]*<b>([\d.,]+)</b>')
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def _load_env():
    """Подхватить .env рядом с проектом (локальный запуск)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


def make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            ctx = ssl.create_default_context()
            ctx.load_default_certs()
            return ctx
        except Exception:
            return ssl._create_unverified_context()


class Client:
    def __init__(self):
        self.jar = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
        if os.path.exists(COOKIES_FILE):
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        self.ctx = make_ssl_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ctx),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.opener.addheaders = [("User-Agent", UA)]

    def save_cookies(self):
        try:
            self.jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass

    def request(self, url, data=None, xhr=False, retries=3, timeout=30):
        headers = {"User-Agent": UA}
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        last = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with self.opener.open(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "ignore")
            except Exception as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise last

    def get(self, url, **kw):
        return self.request(url, **kw)

    def post(self, url, data, **kw):
        return self.request(url, data=data, **kw)

    def get_json(self, url, data=None, **kw):
        return json.loads(self.request(url, data=data, xhr=True, **kw))

    # --- авторизация ---
    def is_logged_in(self, html_text=None):
        """Залогинены = в шапке есть бейдж курса «Курс: NN.NN грн».
        У анонима весь сайт отдаёт заглушку «Доступ к сайту ограничен»."""
        h = html_text if html_text is not None else self.get(BASE + "/")
        return "rate-badge" in h

    def login(self, email=None, password=None):
        email = email or os.environ.get("KMT_EMAIL")
        password = password or os.environ.get("KMT_PASSWORD")
        if not email or not password:
            print("KMT_EMAIL / KMT_PASSWORD не заданы", file=sys.stderr)
            sys.exit(1)
        self.get(BASE + "/")
        j = self.get_json(BASE + "/login/?ajax=1",
                          data={"email": email, "password": password})
        if not j.get("success"):
            raise RuntimeError("Логин не удался: %s" % j)
        self.save_cookies()
        return True

    def ensure_login(self):
        """Проверить сессию, при необходимости перелогиниться.
        Не завязываемся ни на категорию, ни на текст в шапке: маркер —
        бейдж курса, который виден только авторизованному."""
        if self.is_logged_in():
            return
        print("Сессия анонимная, логинюсь...")
        self.login()
        h = self.get(BASE + "/")
        if not self.is_logged_in(h):
            raise RuntimeError("После логина сайт всё ещё отдаёт анонимную версию")
        self._rate = None

    _rate = None

    def rate(self):
        """Курс доллара из шапки (цены на сайте только в $)."""
        if self._rate:
            return self._rate
        m = RE_RATE.search(self.get(BASE + "/"))
        if not m:
            raise RuntimeError("Курс не найден в шапке")
        self._rate = float(m.group(1))
        return self._rate


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
