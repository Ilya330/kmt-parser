# -*- coding: utf-8 -*-
"""Общий HTTP-клиент kmt5.com.ua: логин, cookie-сессия, fetch с ретраями."""
import http.cookiejar
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://kmt5.com.ua"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
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
    def is_logged_in(self):
        """Залогинены = на карточке/каталоге видна «Ваша цена»."""
        try:
            h = self.get(BASE + "/my-account/")
            return "box-entrance_text-2" not in h and ("Выйти" in h or "my-account" in h)
        except Exception:
            return False

    def login(self, email=None, password=None):
        email = email or os.environ.get("KMT_EMAIL")
        password = password or os.environ.get("KMT_PASSWORD")
        if not email or not password:
            print("KMT_EMAIL / KMT_PASSWORD не заданы", file=sys.stderr)
            sys.exit(1)
        # выставить язык/валюту до логина
        self.get(BASE + "/")
        j = self.get_json(BASE + "/login/?ajax=1",
                          data={"email": email, "password": password})
        if not j.get("success"):
            raise RuntimeError("Логин не удался: %s" % j)
        self.save_cookies()
        return True

    def ensure_login(self):
        """Проверить сессию по главной, при необходимости перелогиниться.
        Маркер анонима — приглашение «Войдите в кабинет» в шапке
        (не завязываемся на конкретную категорию: их слаги меняются)."""
        if "Войдите в кабинет" not in self.get(BASE + "/"):
            return
        print("Сессия истекла, логинюсь заново...")
        self.login()
        if "Войдите в кабинет" in self.get(BASE + "/"):
            raise RuntimeError("После логина шапка всё ещё анонимная")


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
