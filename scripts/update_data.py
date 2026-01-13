import json
import os
import re
import hashlib
import datetime
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

DATA_DIR = "data"

# Настройки
MAX_ITEMS_PER_BRAND = 8          # сколько новостей максимум брать на бренд за один запуск
TIMEOUT = 12
UA = "fashionbook-bot/1.0 (+https://github.com/)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


def load_json(name, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(name, obj):
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def slug_id(s: str) -> str:
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return h[:16]


def iso_to_ymd(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def parse_entry_date(entry) -> str | None:
    # feedparser может дать published_parsed / updated_parsed
    tm = None
    if getattr(entry, "published_parsed", None):
        tm = entry.published_parsed
    elif getattr(entry, "updated_parsed", None):
        tm = entry.updated_parsed

    if not tm:
        return None

    d = datetime.datetime(*tm[:6], tzinfo=datetime.timezone.utc).astimezone(datetime.timezone.utc)
    return iso_to_ymd(d)


def resolve_final_url(url: str) -> str:
    """
    Пытаемся получить финальную ссылку (после редиректов).
    Если не получилось — оставляем как есть.
    """
    try:
        r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        return r.url or url
    except Exception:
        return url


def try_get_og_image(url: str) -> str | None:
    """
    Пытаемся вытащить og:image (превью-картинку) со страницы источника.
    Если не получилось — вернём None, это ок.
    """
    try:
        r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        if not r.ok or "text/html" not in (r.headers.get("Content-Type") or ""):
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if tag and tag.get("content"):
            img = tag["content"].strip()
            if img.startswith("http"):
                return img
        return None
    except Exception:
        return None


def normalize_brand_query(name: str) -> str:
    """
    Чуть улучшаем поиск: добавляем runway/collection/show
    """
    # можешь потом кастомизировать по брендам
    return f'{name} (runway OR collection OR show OR "fashion week")'


def google_news_rss_url(query: str, hl="en-US", gl="US", ceid="US:en") -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    posts = load_json("posts.json", [])
    shows = load_json("shows.json", [])
    brands = load_json("brands.json", [])

    # Индекс для дедупликации: по sourceUrl
    existing_urls = set()
    for p in posts:
        u = p.get("sourceUrl") or p.get("source_url") or ""
        if u:
            existing_urls.add(u)

    # brandId map
    # Ожидаем brands.json как список объектов с {id, name}
    brand_items = []
    for b in brands:
        if isinstance(b, dict) and b.get("id") and b.get("name"):
            brand_items.append((b["id"], b["name"]))

    new_posts = []
    now_ymd = iso_to_ymd(datetime.datetime.now(datetime.timezone.utc))

    for brand_id, brand_name in brand_items:
        query = normalize_brand_query(brand_name)
        rss = google_news_rss_url(query)

        feed = feedparser.parse(rss)
        if not feed.entries:
            continue

        picked = 0
        for e in feed.entries:
            if picked >= MAX_ITEMS_PER_BRAND:
                break

            title = (getattr(e, "title", "") or "").strip()
            link = (getattr(e, "link", "") or "").strip()
            if not title or not link:
                continue

            # Сначала попробуем получить финальную ссылку источника
            final_url = resolve_final_url(link)

            # Дедуп: если уже есть такая ссылка — пропускаем
            if final_url in existing_urls:
                continue

            date = parse_entry_date(e) or now_ymd

            # Пробуем добыть картинку (og:image). Если нет — оставим пусто.
            hero = try_get_og_image(final_url)

            post_id = slug_id(final_url)

            # Минимальный объект поста (совместим с твоим приложением)
            obj = {
                "id": post_id,
                "brandId": brand_id,
                "title": title,
                "date": date,
                "city": None,
                "season": None,
                "heroImageUrl": hero,
                "sourceUrl": final_url,
                "media": []  # на будущее для галереи
            }

            new_posts.append(obj)
            existing_urls.add(final_url)
            picked += 1

    if new_posts:
        # новые сверху, чтобы в фиде они были первыми
        posts = new_posts + posts
        save_json("posts.json", posts)

    meta = {
        "updatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        "newPostsAdded": len(new_posts),
        "postsCount": len(posts),
        "showsCount": len(shows),
        "brandsCount": len(brands),
    }
    save_json("_meta.json", meta)


if __name__ == "__main__":
    main()
