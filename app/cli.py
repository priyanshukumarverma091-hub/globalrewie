import os

import click
from flask import Flask

from .extensions import db
from .models import Author, Category, Source, User
from .textutil import slugify

CATEGORIES = [
    ("World", "Reporting on international affairs and world politics."),
    ("Geopolitics", "Power, territory, alliances and the strategic balance between states."),
    ("Defence", "Armed forces, procurement, security policy and military affairs."),
    ("Economy", "The global economy, trade, sanctions and finance."),
    ("Diplomacy", "Diplomatic developments, negotiations, treaties and summits."),
    ("Conflicts", "Wars, armed conflicts and humanitarian crises."),
    ("Technology", "Technology and AI in international affairs."),
    ("International Organisations", "The UN, NATO, the EU and other multilateral bodies."),
    ("Strategic Analysis", "Longer-term assessments of strategic questions."),
]
SOURCES = [  # public RSS feeds from established outlets; verify each outlet's terms before republishing anything
    ("BBC News - World", "rss", "http://feeds.bbci.co.uk/news/world/rss.xml", ""),
    ("The Guardian - World", "rss", "https://www.theguardian.com/world/rss", ""),
    ("Al Jazeera", "rss", "https://www.aljazeera.com/xml/rss/all.xml", ""),
    ("UN News", "rss", "https://news.un.org/feed/subscribe/en/news/all/rss.xml", ""),
    ("Deutsche Welle - World", "rss", "https://rss.dw.com/rdf/rss-en-world", ""),
    ("NPR - World", "rss", "https://feeds.npr.org/1004/rss.xml", ""),
    ("GDELT Project", "gdelt", "", "(geopolitics OR diplomacy OR sanctions OR \"foreign minister\") sourcelang:english"),
    ("NewsAPI", "newsapi", "", ""),
]


def seed_defaults(newsapi_key: str = ""):
    for i, (name, desc) in enumerate(CATEGORIES):
        if not Category.query.filter_by(slug=slugify(name)).first():
            db.session.add(Category(name=name, slug=slugify(name), description=desc, sort_order=i))
    for name, kind, url, query in SOURCES:
        if not Source.query.filter_by(name=name).first():
            db.session.add(Source(name=name, kind=kind, feed_url=url, query=query,
                                  is_active=(kind != "newsapi" or bool(newsapi_key))))
    db.session.commit()


def _make_admin(email, name, password, role="admin") -> bool:
    email = email.strip().lower()
    if User.query.filter_by(email=email).first():
        return False
    user = User(email=email, name=name.strip(), role=role)
    user.set_password(password)
    db.session.add(user)
    if not Author.query.filter_by(name=user.name).first():
        from .utils import unique_slug
        db.session.add(Author(name=user.name, slug=unique_slug(Author, user.name), title="Editor"))
    db.session.commit()
    return True


def register_cli(app: Flask):
    @app.cli.command("init-db")
    def init_db():
        """Create tables (idempotent) and seed categories and news sources."""
        db.create_all()
        seed_defaults(app.config["NEWSAPI_KEY"])
        click.echo("Database ready.")

    @app.cli.command("create-admin")
    @click.option("--email")
    @click.option("--name")
    @click.option("--password")
    @click.option("--role", type=click.Choice(["admin", "editor"]), default="admin")
    @click.option("--from-env", is_flag=True, help="Use ADMIN_EMAIL / ADMIN_NAME / ADMIN_PASSWORD; skip if the user exists.")
    def create_admin(email, name, password, role, from_env):
        """Create an administrator (or editor) account."""
        if from_env:
            email, name = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_NAME", "Editor")
            password = os.environ.get("ADMIN_PASSWORD")
            if not (email and password):
                click.echo("ADMIN_EMAIL / ADMIN_PASSWORD not set; skipping.")
                return
        email = email or click.prompt("Email")
        name = name or click.prompt("Display name")
        password = password or click.prompt("Password (min 12 characters)", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        click.echo("Account created." if _make_admin(email, name, password, role) else "A user with that email already exists.")

    @app.cli.command("reset-password")
    @click.option("--email", prompt=True)
    @click.password_option("--password", prompt="New password (min 12 characters)")
    def reset_password(email, password):
        """Reset a user's password."""
        user = User.query.filter_by(email=email.strip().lower()).first()
        if not user:
            raise click.ClickException("No such user.")
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        user.set_password(password)
        db.session.commit()
        click.echo("Password updated.")

    @app.cli.command("fetch-news")
    @click.option("--force", is_flag=True)
    def fetch_news(force):
        """Fetch headlines from all active sources into the review queue."""
        from .services.collector import collect_all
        for name, (n, msg) in collect_all(force=force).items():
            click.echo(f"{name}: {n} new - {msg}")
