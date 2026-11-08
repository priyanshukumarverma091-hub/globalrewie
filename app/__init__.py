import os
import secrets

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config_by_name

from .extensions import csrf, db, limiter, login_manager, migrate

NAV = [
    ("Home", "public.index", {}),
    ("World", "public.category", {"slug": "world"}),
    ("Geopolitics", "public.category", {"slug": "geopolitics"}),
    ("Defence", "public.category", {"slug": "defence"}),
    ("Economy", "public.category", {"slug": "economy"}),
    ("Analysis", "public.analysis", {}),
    ("Technology", "public.category", {"slug": "technology"}),
]


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("APP_ENV", "development")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))

    if not app.config.get("SECRET_KEY"):
        if config_name == "production":
            raise RuntimeError("SECRET_KEY environment variable is required in production.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)  # dev only: sessions reset on restart

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    if app.config["BEHIND_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "admin.login"
    login_manager.login_message = "Please sign in to continue."

    from .blueprints.admin import bp as admin_bp
    from .blueprints.public import bp as public_bp
    from .cli import register_cli

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    register_cli(app)
    _register_hooks(app)
    return app


def _register_hooks(app: Flask):
    from .models import Category, TickerItem, utcnow

    @app.context_processor
    def inject_globals():
        ctx = {"site_name": app.config["SITE_NAME"], "site_tagline": app.config["SITE_TAGLINE"],
               "nav_items": NAV, "now": utcnow(), "footer_categories": [], "ticker_items": []}
        try:
            ctx["footer_categories"] = Category.query.order_by(Category.sort_order, Category.name).all()
            if request.blueprint == "public":
                ctx["ticker_items"] = (TickerItem.query.filter_by(is_active=True)
                                       .order_by(TickerItem.created_at.desc()).limit(10).all())
        except Exception:  # database unavailable: render the shell anyway
            db.session.rollback()
        return ctx

    @app.template_global()
    def page_url(page: int) -> str:
        args = {**request.view_args, **request.args.to_dict(), "page": page}
        return url_for(request.endpoint, **args)

    @app.template_global()
    def abs_url(path: str = "") -> str:
        base = app.config["SITE_URL"] or request.url_root.rstrip("/")
        return base + (path if path.startswith("/") else "/" + path)

    @app.template_filter("img")
    def img_filter(name: str, size: str = "full") -> str:
        if not name:
            return ""
        if size == "small":
            name = name[:-4] + "-800.jpg"
        return url_for("public.uploaded_file", filename=name)

    @app.template_filter("dt")
    def dt_filter(value, fmt="%d %B %Y"):
        return value.strftime(fmt).lstrip("0") if value else ""

    @app.template_filter("iso")
    def iso_filter(value):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        if app.config["SESSION_COOKIE_SECURE"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.blueprint == "admin":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/error.html", code=404, heading="Page not found",
                               message="The page you are looking for does not exist or has been moved."), 404

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/error.html", code=403, heading="Access denied",
                               message="You do not have permission to view this page."), 403

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("errors/error.html", code=413, heading="Upload too large",
                               message="Files must be smaller than 6 MB."), 413

    @app.errorhandler(429)
    def rate_limited(_e):
        return render_template("errors/error.html", code=429, heading="Too many requests",
                               message="Please wait a moment and try again."), 429

    @app.errorhandler(CSRFError)
    def csrf_error(_e):
        flash("Your session expired or the form was invalid. Please try again.", "error")
        return redirect(request.referrer or url_for("public.index"))

    @app.errorhandler(500)
    def server_error(_e):
        db.session.rollback()
        return render_template("errors/error.html", code=500, heading="Something went wrong",
                               message="An unexpected error occurred. Our team has been notified."), 500
