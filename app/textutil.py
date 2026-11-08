"""Pure text helpers (no Flask / DB imports) so they are easy to test."""
import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def slugify(value: str, max_length: int = 120) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:max_length].strip("-") or "item"


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"}
    SKIP = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_tags(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:  # malformed markup: fall back to a crude strip
        return re.sub(r"<[^>]*>", " ", value or "")
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return cut + "\u2026"


def clean_summary(raw: str, limit: int = 300) -> str:
    return truncate(strip_tags(html.unescape(raw or "")), limit)


# ---------------------------------------------------------------- sanitizer
ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "h2", "h3", "h4", "ul", "ol", "li",
    "blockquote", "a", "hr", "figure", "figcaption", "img", "table", "thead", "tbody",
    "tr", "th", "td", "sup", "sub", "code", "pre",
}
TAG_MAP = {"div": "p", "h1": "h2", "h5": "h4", "h6": "h4", "strike": "s", "del": "s"}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math"}
VOID_TAGS = {"br", "hr", "img"}
_SAFE_SCHEMES = {"http", "https", "mailto", ""}


def _safe_url(value: str, allow_mailto: bool, allow_local_uploads: bool = False) -> str | None:
    value = re.sub(r"[\x00-\x20]", "", value or "")
    if not value:
        return None
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme == "mailto":
        return value if allow_mailto else None
    if scheme in ("http", "https"):
        return value
    if scheme == "" and not value.startswith("//"):
        if value.startswith("#"):
            return value
        if value.startswith("/") and (not allow_local_uploads or value.startswith("/uploads/")):
            return value
    return None


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        tag = TAG_MAP.get(tag, tag)
        if self.skip or tag not in ALLOWED_TAGS:
            return
        allowed = ALLOWED_ATTRS.get(tag, set())
        rendered = []
        for name, val in attrs:
            name = (name or "").lower()
            if name not in allowed or val is None:
                continue
            if name == "href":
                val = _safe_url(val, allow_mailto=True)
            elif name == "src":
                val = _safe_url(val, allow_mailto=False, allow_local_uploads=True)
            elif name in ("colspan", "rowspan"):
                val = val if val.isdigit() and int(val) < 50 else None
            if val is None:
                continue
            rendered.append(f' {name}="{html.escape(val, quote=True)}"')
        if tag == "a":
            rendered.append(' rel="noopener noreferrer nofollow"')
        if tag == "img" and not any(a.startswith(" src=") for a in rendered):
            return
        self.out.append(f"<{tag}{''.join(rendered)}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and tag not in DROP_CONTENT:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        tag = TAG_MAP.get(tag, tag)
        if self.skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(value: str) -> str:
    """Allow-list sanitizer for editor content. Unknown tags are unwrapped,
    dangerous tags (script, iframe...) are removed together with their content."""
    parser = _Sanitizer()
    parser.feed(value or "")
    parser.close()
    return parser.result().strip()


# --------------------------------------------------------------- URL helpers
_TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "ocid", "cmpid", "ref", "ref_src"}


def normalize_url(url: str) -> str:
    p = urlparse((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def reading_minutes(html_text: str, wpm: int = 220) -> int:
    words = len(re.findall(r"\w+", strip_tags(html_text or "")))
    return max(1, round(words / wpm))
