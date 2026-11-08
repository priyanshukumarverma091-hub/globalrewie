"""Fetches headlines from configured sources and stores them as *pending* items
for editorial review. Only headline, a short provider summary and the original
link are stored - full articles are never copied."""
import logging

import requests
from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Article, Category, Source, utcnow
from ..textutil import slugify, url_hash
from . import feeds

log = logging.getLogger(__name__)
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"


def _http_get(url, **kwargs):
    cfg = current_app.config
    headers = {"User-Agent": f"{cfg['SITE_NAME'].replace(' ', '')}Bot/1.0 (+{cfg['SITE_URL'] or 'local'})"}
    headers.update(kwargs.pop("headers", {}))
    resp = requests.get(url, headers=headers, timeout=cfg["FETCH_TIMEOUT"], stream=True, **kwargs)
    resp.raise_for_status()
    content = resp.raw.read(cfg["FETCH_MAX_BYTES"] + 1, decode_content=True)
    if len(content) > cfg["FETCH_MAX_BYTES"]:
        raise feeds.FeedError("Response too large.")
    return content


def _fetch_items(source: Source) -> list[dict]:
    import json
    if source.kind == "rss":
        return feeds.parse_feed(_http_get(source.feed_url))
    if source.kind == "gdelt":
        params = {"query": source.query or "geopolitics sourcelang:english", "mode": "artlist",
                  "format": "json", "maxrecords": 50, "sort": "datedesc", "timespan": "1d"}
        raw = _http_get(GDELT_URL, params=params)
        return feeds.parse_gdelt(json.loads(raw.decode("utf-8", "replace")))
    if source.kind == "newsapi":
        key = current_app.config["NEWSAPI_KEY"]
        if not key:
            raise feeds.FeedError("NEWSAPI_KEY is not configured.")
        params = {"category": "general", "language": "en", "pageSize": 50}
        if source.query:
            params["q"] = source.query
        raw = _http_get(NEWSAPI_URL, params=params, headers={"X-Api-Key": key})
        return feeds.parse_newsapi(json.loads(raw.decode("utf-8", "replace")))
    raise feeds.FeedError(f"Unknown source type: {source.kind}")


def collect_source(source: Source, force: bool = False) -> tuple[int, str]:
    """Returns (new_items, message). Never raises on network/parse failures."""
    ttl = current_app.config["FETCH_CACHE_MINUTES"] * 60
    if not force and source.last_fetched_at and (utcnow() - source.last_fetched_at).total_seconds() < ttl:
        return 0, "cached (recently fetched)"
    try:
        items = _fetch_items(source)[: current_app.config["FETCH_MAX_ITEMS"]]
    except (requests.RequestException, feeds.FeedError, ValueError) as exc:
        source.last_fetched_at, source.last_status = utcnow(), f"Error: {str(exc)[:150]}"
        db.session.commit()
        log.warning("Fetch failed for %s: %s", source.name, exc)
        return 0, source.last_status

    categories = {c.slug: c for c in Category.query.all()}
    hashes = {url_hash(i["link"]): i for i in items}
    existing = {h for (h,) in db.session.query(Article.url_hash).filter(Article.url_hash.in_(list(hashes)))} if hashes else set()
    added = 0
    for h, item in hashes.items():
        if h in existing:
            continue
        cat = categories.get(feeds.classify(item["title"], item["summary"])) or categories.get("world")
        article = Article(
            title=item["title"], slug=f"{slugify(item['title'], 90)}-{h[:8]}", summary=item["summary"],
            kind="collected", status="pending", url_hash=h, source_id=source.id,
            source_name=item["source_name"] or source.name, source_url=item["link"],
            category_id=cat.id if cat else None, published_at=item["published"] or utcnow(),
        )
        try:
            with db.session.begin_nested():
                db.session.add(article)
            added += 1
        except IntegrityError:
            continue  # duplicate slug/hash: skip silently
    source.last_fetched_at, source.last_status = utcnow(), f"OK - {added} new of {len(items)}"
    db.session.commit()
    return added, source.last_status


def collect_all(force: bool = False) -> dict:
    results = {}
    for source in Source.query.filter_by(is_active=True).all():
        try:
            results[source.name] = collect_source(source, force=force)
        except Exception as exc:  # last-resort guard: one bad source must not stop the rest
            db.session.rollback()
            log.exception("Unexpected collector error for %s", source.name)
            results[source.name] = (0, f"Error: {exc.__class__.__name__}")
    return results
