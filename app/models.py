import re
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager
from .textutil import reading_minutes


def utcnow() -> datetime:
    """Naive UTC timestamp (consistent across SQLite and PostgreSQL)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="editor")  # admin | editor
    is_active_user = db.Column("is_active", db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    @property
    def is_active(self):  # Flask-Login hook
        return bool(self.is_active_user)

    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)  # scrypt/pbkdf2 + salt

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(300), default="")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    articles = db.relationship("Article", back_populates="category", lazy="dynamic")


class Author(db.Model):
    __tablename__ = "authors"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), unique=True, nullable=False, index=True)
    title = db.Column(db.String(150), default="")
    bio = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    articles = db.relationship("Article", back_populates="author", lazy="dynamic")


class Source(db.Model):
    """A feed/API the collector reads from (RSS, GDELT, NewsAPI)."""
    __tablename__ = "sources"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="rss")  # rss | gdelt | newsapi
    feed_url = db.Column(db.String(500), default="")
    search_query = db.Column("query", db.String(400), default="")  # GDELT query / NewsAPI query
    site_url = db.Column(db.String(300), default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_fetched_at = db.Column(db.DateTime)
    last_status = db.Column(db.String(200), default="")
    articles = db.relationship("Article", back_populates="source", lazy="dynamic")


class Article(db.Model):
    __tablename__ = "articles"

    KINDS = ("news", "analysis", "opinion", "collected")
    EDITORIAL_KINDS = ("news", "analysis", "opinion")
    STATUSES = ("draft", "pending", "published", "rejected")
    ANALYSIS_TYPES = {
        "": "General analysis",
        "country": "Country analysis",
        "conflict": "Conflict timeline",
        "diplomatic": "Diplomatic development",
        "economic": "Economic & strategic implications",
        "commentary": "Expert commentary",
    }
    KIND_LABELS = {"news": "Reported news", "analysis": "Analysis", "opinion": "Opinion",
                   "collected": "Aggregated headline"}

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    slug = db.Column(db.String(340), unique=True, nullable=False, index=True)
    subtitle = db.Column(db.String(400), default="")
    summary = db.Column(db.Text, default="")
    body = db.Column(db.Text, default="")
    kind = db.Column(db.String(20), nullable=False, default="news", index=True)
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    analysis_type = db.Column(db.String(40), default="")
    country = db.Column(db.String(80), default="")
    timeline = db.Column(db.Text, default="")      # lines: "date | event"
    commentary = db.Column(db.Text, default="")    # lines: "Name | Role | Quote"
    references = db.Column(db.Text, default="")    # lines: "Title | URL" or "URL"
    tags = db.Column(db.Text, default="")          # comma separated
    featured_image = db.Column(db.String(120), default="")
    image_alt = db.Column(db.String(250), default="")
    image_credit = db.Column(db.String(200), default="")
    meta_description = db.Column(db.String(300), default="")
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    is_editors_pick = db.Column(db.Boolean, nullable=False, default=False)
    views = db.Column(db.Integer, nullable=False, default=0)

    # Aggregated (collected) items only
    source_name = db.Column(db.String(150), default="")
    source_url = db.Column(db.String(1000), default="")
    url_hash = db.Column(db.String(64), unique=True, index=True)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id"))
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    published_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    category = db.relationship("Category", back_populates="articles")
    author = db.relationship("Author", back_populates="articles")
    source = db.relationship("Source", back_populates="articles")

    # ---- helpers
    @classmethod
    def live(cls):
        """Query of publicly visible articles."""
        return cls.query.filter(cls.status == "published", cls.published_at <= utcnow())

    @property
    def is_collected(self):
        return self.kind == "collected"

    @property
    def kind_label(self):
        return self.KIND_LABELS.get(self.kind, "")

    @property
    def reading_time(self):
        return reading_minutes(self.body or "")

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    @property
    def references_list(self):
        items = []
        for line in (self.references or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                label, url = [p.strip() for p in line.split("|", 1)]
            else:
                label, url = line, line
            if not re.match(r"^https?://", url):
                url = ""
            items.append({"label": label or url, "url": url})
        return items

    @property
    def timeline_items(self):
        out = []
        for line in (self.timeline or "").splitlines():
            if "|" in line:
                when, what = [p.strip() for p in line.split("|", 1)]
                if when and what:
                    out.append({"when": when, "what": what})
        return out

    @property
    def commentary_items(self):
        out = []
        for line in (self.commentary or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[2]:
                out.append({"name": parts[0], "role": parts[1], "quote": "|".join(parts[2:])})
        return out

    @property
    def was_updated(self):
        return bool(self.published_at and self.updated_at and
                    (self.updated_at - self.published_at).total_seconds() > 3600)


class TickerItem(db.Model):
    __tablename__ = "ticker_items"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(250), nullable=False)
    url = db.Column(db.String(500), default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Subscriber(db.Model):
    __tablename__ = "subscribers"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class AdminLog(db.Model):
    __tablename__ = "admin_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False)
    target = db.Column(db.String(200), default="")
    detail = db.Column(db.String(500), default="")
    ip = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User")
