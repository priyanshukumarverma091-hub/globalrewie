"""Pure parsing helpers for RSS/Atom, GDELT and NewsAPI payloads.
No Flask or database imports, so they are unit-testable in isolation."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..textutil import clean_summary, truncate


class FeedError(Exception):
    pass


CATEGORY_KEYWORDS = [
    ("conflicts", ["war", "airstrike", "air strike", "missile", "ceasefire", "offensive", "invasion",
                   "troops", "hostage", "militia", "shelling", "drone strike", "casualties",
                   "insurgent", "frontline", "bombing", "rebels"]),
    ("defence", ["defence", "defense", "military", "navy", "army", "air force", "nato", "weapons",
                 "arms deal", "fighter jet", "submarine", "pentagon", "warship", "procurement"]),
    ("economy", ["economy", "economic", "inflation", "gdp", "tariff", "tariffs", "trade", "central bank",
                 "interest rate", "imf", "world bank", "sanctions", "currency", "recession", "oil price",
                 "opec", "supply chain", "debt", "markets"]),
    ("technology", ["artificial intelligence", "ai", "semiconductor", "chips", "cyber", "cyberattack",
                    "satellite", "quantum", "data centre", "data center", "software", "tech"]),
    ("diplomacy", ["summit", "diplomat", "diplomats", "diplomacy", "ambassador", "talks", "treaty",
                   "foreign minister", "negotiations", "bilateral", "state visit", "envoy", "embassy"]),
    ("international-organisations", ["united nations", "un", "security council", "who", "unesco",
                                     "european union", "eu", "african union", "asean", "g20", "g7",
                                     "brics", "wto", "international court", "icc"]),
]
_CATEGORY_RES = [(slug, re.compile(r"\b(?:%s)\b" % "|".join(re.escape(k) for k in kws), re.I))
                 for slug, kws in CATEGORY_KEYWORDS]


def classify(title: str, summary: str = "", default: str = "world") -> str:
    best, best_score = default, 0
    for slug, rx in _CATEGORY_RES:
        score = 2 * len(rx.findall(title or "")) + len(rx.findall(summary or ""))
        if score > best_score:
            best, best_score = slug, score
    return best


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child_text(el, *names):
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _to_utc_naive(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return _to_utc_naive(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        pass
    try:
        return _to_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_feed(content: bytes) -> list[dict]:
    """Parse RSS 2.0 / RSS 1.0 (RDF) / Atom. Returns title, link, summary, published."""
    if re.search(rb"<!ENTITY", content, re.I):
        raise FeedError("Feed rejected: contains entity declarations.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise FeedError(f"Invalid XML: {exc}") from exc
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _child_text(el, "title")
        link = _child_text(el, "link")
        if not link:  # Atom: <link href="..."/>
            for child in el:
                if _local(child.tag) == "link" and child.get("href"):
                    if child.get("rel", "alternate") == "alternate":
                        link = child.get("href").strip()
                        break
        summary = _child_text(el, "description", "summary", "content", "encoded")
        published = parse_date(_child_text(el, "pubdate", "published", "updated", "date"))
        if title and link.startswith(("http://", "https://")):
            items.append({"title": truncate(clean_summary(title, 300), 300),
                          "link": link, "summary": clean_summary(summary, 300),
                          "published": published, "source_name": ""})
    return items


def parse_gdelt(data: dict) -> list[dict]:
    items = []
    for a in (data or {}).get("articles", []) or []:
        title, url = (a.get("title") or "").strip(), (a.get("url") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        published = None
        try:
            published = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ")
        except ValueError:
            pass
        items.append({"title": truncate(title, 300), "link": url, "summary": "",
                      "published": published, "source_name": a.get("domain", "")})
    return items


def parse_newsapi(data: dict) -> list[dict]:
    items = []
    for a in (data or {}).get("articles", []) or []:
        title, url = (a.get("title") or "").strip(), (a.get("url") or "").strip()
        if not title or title == "[Removed]" or not url.startswith(("http://", "https://")):
            continue
        items.append({"title": truncate(title, 300), "link": url,
                      "summary": clean_summary(a.get("description") or "", 300),
                      "published": parse_date(a.get("publishedAt", "")),
                      "source_name": (a.get("source") or {}).get("name", "")})
    return items
