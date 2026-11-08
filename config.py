"""Application configuration. All secrets come from environment variables."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        # Zero-config local fallback so the site runs immediately.
        return "sqlite:///" + str(BASE_DIR / "instance" / "globalreview.db")
    # Render/Neon/Heroku style URLs -> SQLAlchemy + psycopg2 driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_pre_ping + recycle: free-tier Postgres (Neon) suspends idle connections.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    SITE_NAME = "The Global Review"
    SITE_TAGLINE = "Independent international news and geopolitical analysis"
    SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "editor@example.com")

    UPLOAD_FOLDER = os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads"))
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024  # 6 MB request cap (image uploads)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours
    WTF_CSRF_TIME_LIMIT = None  # CSRF token valid for the whole session

    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    ARTICLES_PER_PAGE = 12
    ADMIN_PER_PAGE = 20

    # News collection
    NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
    FETCH_CACHE_MINUTES = int(os.environ.get("FETCH_CACHE_MINUTES", "30"))
    FETCH_TIMEOUT = (5, 15)  # connect, read seconds
    FETCH_MAX_BYTES = 3 * 1024 * 1024
    FETCH_MAX_ITEMS = 40
    CRON_TOKEN = os.environ.get("CRON_TOKEN", "").strip()

    BEHIND_PROXY = _bool("BEHIND_PROXY", False)
    TESTING = False


class DevelopmentConfig(BaseConfig):
    DEBUG = _bool("FLASK_DEBUG", False)


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    BEHIND_PROXY = True


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    NEWSAPI_KEY = ""
    CRON_TOKEN = "test-cron-token"
    UPLOAD_FOLDER = str(BASE_DIR / "instance" / "test_uploads")


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
