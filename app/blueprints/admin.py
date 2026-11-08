import csv
import io
from functools import wraps

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_

from ..extensions import db, limiter
from ..models import (AdminLog, Article, Author, Category, Source, Subscriber, TickerItem, User,
                      utcnow)
from ..services.collector import collect_all, collect_source
from ..textutil import escape_like, sanitize_html, strip_tags
from ..utils import (UploadError, delete_image, is_http_url, log_action, parse_local_datetime,
                     safe_next_url, save_image, to_int, unique_slug)

bp = Blueprint("admin", __name__)

_DUMMY_HASH = None


def admin_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


@bp.before_request
def require_login():
    if request.endpoint == "admin.login":
        return None
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not current_user.is_active:
        logout_user()
        return current_app.login_manager.unauthorized()
    return None


def _per_page():
    return current_app.config["ADMIN_PER_PAGE"]


def _page():
    return max(1, request.args.get("page", 1, type=int) or 1)


# ---------------------------------------------------------------- auth
@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute; 30 per hour", methods=["POST"])
def login():
    global _DUMMY_HASH
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user is None:  # equalise timing so unknown emails are not distinguishable
            if _DUMMY_HASH is None:
                from werkzeug.security import generate_password_hash
                _DUMMY_HASH = generate_password_hash("dummy-password")
            from werkzeug.security import check_password_hash
            check_password_hash(_DUMMY_HASH, password)
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=False)
            user.last_login_at = utcnow()
            log_action("login", user.email, user_id=user.id)
            db.session.commit()
            return redirect(safe_next_url(request.args.get("next")) or url_for("admin.dashboard"))
        log_action("login_failed", email, user_id=None)
        db.session.commit()
        flash("Invalid email or password.", "error")
        return render_template("admin/login.html"), 401
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    log_action("logout", current_user.email)
    db.session.commit()
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("admin.login"))


# ----------------------------------------------------------- dashboard
@bp.route("/")
def dashboard():
    counts = dict(db.session.query(Article.status, func.count(Article.id)).group_by(Article.status).all())
    stats = {
        "total": sum(counts.values()), "published": counts.get("published", 0),
        "draft": counts.get("draft", 0), "pending": counts.get("pending", 0),
        "subscribers": Subscriber.query.filter_by(is_active=True).count(),
        "views": db.session.query(func.coalesce(func.sum(Article.views), 0)).scalar(),
    }
    recent = Article.query.order_by(Article.updated_at.desc()).limit(8).all()
    logs = AdminLog.query.order_by(AdminLog.created_at.desc()).limit(8).all() if current_user.is_admin else []
    return render_template("admin/dashboard.html", stats=stats, recent=recent, logs=logs)


# ------------------------------------------------------------ articles
@bp.route("/articles")
def articles():
    q = Article.query.filter(Article.kind != "collected")
    term, status = request.args.get("q", "").strip(), request.args.get("status", "")
    kind, cat = request.args.get("kind", ""), request.args.get("category", type=int)
    if term:
        q = q.filter(Article.title.ilike(f"%{escape_like(term)}%", escape="\\"))
    if status in Article.STATUSES:
        q = q.filter(Article.status == status)
    if kind in Article.EDITORIAL_KINDS:
        q = q.filter(Article.kind == kind)
    if cat:
        q = q.filter(Article.category_id == cat)
    pg = q.order_by(Article.updated_at.desc()).paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("admin/articles.html", pg=pg, categories=Category.query.order_by(Category.name).all(),
                           term=term, status=status, kind=kind, cat=cat, title="Articles")


FORM_TEXT_FIELDS = ("title", "subtitle", "slug", "summary", "analysis_type", "country", "timeline",
                    "commentary", "references", "tags", "image_alt", "image_credit", "meta_description",
                    "source_name", "source_url", "published_at")


def _values_from_article(a):
    v = {k: getattr(a, k, "") or "" for k in FORM_TEXT_FIELDS if k != "published_at"}
    v["published_at"] = a.published_at.strftime("%Y-%m-%dT%H:%M") if a.published_at else ""
    v.update(body=a.body or "", kind=a.kind or "news", category_id=a.category_id, author_id=a.author_id,
             is_featured=a.is_featured, is_editors_pick=a.is_editors_pick, featured_image=a.featured_image)
    return v


def _parse_form(form, collected: bool):
    v = {k: form.get(k, "").strip() for k in FORM_TEXT_FIELDS}
    v["body"] = sanitize_html(form.get("body", ""))
    v["kind"] = "collected" if collected else form.get("kind", "news")
    v["category_id"], v["author_id"] = to_int(form.get("category_id")), to_int(form.get("author_id"))
    v["is_featured"], v["is_editors_pick"] = "is_featured" in form, "is_editors_pick" in form
    return v


def _validate(v, action, collected):
    errors = []
    if not v["title"]:
        errors.append("A headline is required.")
    elif len(v["title"]) > 300:
        errors.append("Headline must be 300 characters or fewer.")
    if len(v["subtitle"]) > 400:
        errors.append("Subtitle must be 400 characters or fewer.")
    if len(v["meta_description"]) > 300:
        errors.append("Meta description must be 300 characters or fewer.")
    if not collected and v["kind"] not in Article.EDITORIAL_KINDS:
        errors.append("Choose a valid content type.")
    if v["category_id"] and not db.session.get(Category, v["category_id"]):
        errors.append("Selected category does not exist.")
    if v["author_id"] and not db.session.get(Author, v["author_id"]):
        errors.append("Selected author does not exist.")
    if v["published_at"] and not parse_local_datetime(v["published_at"]):
        errors.append("Publication date is not valid.")
    if v["source_url"] and not is_http_url(v["source_url"]):
        errors.append("Source URL must start with http:// or https://.")
    if action == "publish":
        if not v["category_id"]:
            errors.append("Choose a category before publishing.")
        if not collected:
            if not v["summary"]:
                errors.append("A short summary is required before publishing.")
            if not strip_tags(v["body"]):
                errors.append("Article body is required before publishing.")
            if not v["author_id"]:
                errors.append("Choose an author before publishing.")
    return errors


def _apply(a, v, action, files, remove_image):
    collected = a.kind == "collected"
    for k in ("title", "subtitle", "summary", "analysis_type", "country", "timeline", "commentary",
              "references", "tags", "image_alt", "image_credit", "meta_description"):
        setattr(a, k, v[k])
    if not collected:
        a.body, a.kind = v["body"], v["kind"]
        a.source_name, a.source_url = v["source_name"], v["source_url"]
    a.category_id, a.author_id = v["category_id"], v["author_id"]
    a.is_featured, a.is_editors_pick = v["is_featured"], v["is_editors_pick"]
    if v["slug"] or not a.slug:
        a.slug = unique_slug(Article, v["slug"] or v["title"], a.id)
    upload = files.get("image")
    if upload and upload.filename:
        old = a.featured_image
        a.featured_image = save_image(upload)
        delete_image(old)
    elif remove_image and a.featured_image:
        delete_image(a.featured_image)
        a.featured_image = ""
    when = parse_local_datetime(v["published_at"])
    if action == "publish":
        if a.status != "published" or when:
            a.published_at = when or a.published_at or utcnow()
        a.status = "published"
    elif action == "unpublish":
        a.status = "pending" if collected else "draft"
    else:
        if when:
            a.published_at = when
        if a.status in ("rejected",):
            a.status = "pending" if collected else "draft"
    if a.status == "published" and not a.published_at:
        a.published_at = utcnow()


def _article_form(a, new):
    action = request.form.get("action", "save")
    if action not in ("save", "publish", "unpublish"):
        abort(400)
    collected = a.kind == "collected"
    v = _parse_form(request.form, collected)
    errors = _validate(v, action, collected)
    if not errors:
        try:
            with db.session.no_autoflush:
                _apply(a, v, action, request.files, "remove_image" in request.form)
            if new:
                a.created_by = current_user.id
                db.session.add(a)
            db.session.flush()
            log_action({"save": "article_saved", "publish": "article_published",
                        "unpublish": "article_unpublished"}[action] if not new else "article_created",
                       f"#{a.id} {a.title[:80]}")
            db.session.commit()
            flash({"publish": "Article published.", "unpublish": "Article moved back to drafts."}
                  .get(action, "Article saved."), "success")
            return redirect(url_for("admin.edit_article", article_id=a.id))
        except UploadError as exc:
            db.session.rollback()
            errors.append(str(exc))
    v["featured_image"] = a.featured_image if not new else ""
    return render_template("admin/article_form.html", a=a, v=v, errors=errors, new=new,
                           categories=Category.query.order_by(Category.name).all(),
                           authors=Author.query.order_by(Author.name).all(), title="Article"), 400


@bp.route("/articles/new", methods=["GET", "POST"])
def new_article():
    a = Article(kind="news", status="draft")
    if request.method == "POST":
        return _article_form(a, new=True)
    v = _values_from_article(a)
    mine = Author.query.filter_by(name=current_user.name).first()
    v["author_id"] = mine.id if mine else None
    return render_template("admin/article_form.html", a=a, v=v, errors=[], new=True,
                           categories=Category.query.order_by(Category.name).all(),
                           authors=Author.query.order_by(Author.name).all(), title="New article")


@bp.route("/articles/<int:article_id>/edit", methods=["GET", "POST"])
def edit_article(article_id):
    a = db.get_or_404(Article, article_id)
    if request.method == "POST":
        return _article_form(a, new=False)
    return render_template("admin/article_form.html", a=a, v=_values_from_article(a), errors=[], new=False,
                           categories=Category.query.order_by(Category.name).all(),
                           authors=Author.query.order_by(Author.name).all(), title="Edit article")


@bp.route("/articles/<int:article_id>/preview")
def preview_article(article_id):
    a = db.get_or_404(Article, article_id)
    return render_template("article.html", a=a, related=[], preview=True)


@bp.route("/articles/<int:article_id>/toggle", methods=["POST"])
def toggle_article(article_id):
    a = db.get_or_404(Article, article_id)
    if a.status == "published":
        a.status = "pending" if a.is_collected else "draft"
        verb = "unpublished"
    else:
        if not a.category_id or (not a.is_collected and (not strip_tags(a.body) or not a.summary or not a.author_id)):
            flash("Complete the summary, body, category and author in the editor before publishing.", "error")
            return redirect(url_for("admin.edit_article", article_id=a.id))
        a.status, a.published_at = "published", a.published_at if (a.published_at and a.published_at <= utcnow()) else utcnow()
        verb = "published"
    log_action(f"article_{verb}", f"#{a.id} {a.title[:80]}")
    db.session.commit()
    flash(f"Article {verb}.", "success")
    return redirect(request.referrer or url_for("admin.articles"))


@bp.route("/articles/<int:article_id>/delete", methods=["POST"])
@admin_only
def delete_article(article_id):
    a = db.get_or_404(Article, article_id)
    log_action("article_deleted", f"#{a.id} {a.title[:80]}")
    delete_image(a.featured_image)
    db.session.delete(a)
    db.session.commit()
    flash("Article deleted.", "success")
    return redirect(url_for("admin.articles"))


# ---------------------------------------------------- collected news
@bp.route("/collected")
def collected():
    status = request.args.get("status", "pending")
    q = Article.query.filter(Article.kind == "collected")
    if status in ("pending", "published", "rejected"):
        q = q.filter(Article.status == status)
    pg = q.order_by(Article.published_at.desc()).paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("admin/collected.html", pg=pg, status=status, title="Collected news",
                           sources=Source.query.order_by(Source.name).all(),
                           newsapi_ready=bool(current_app.config["NEWSAPI_KEY"]))


@bp.route("/collected/fetch", methods=["POST"])
def collected_fetch():
    source_id = to_int(request.form.get("source_id"))
    if source_id:
        src = db.get_or_404(Source, source_id)
        added, msg = collect_source(src, force=True)
        flash(f"{src.name}: {msg}", "success" if not msg.startswith("Error") else "error")
    else:
        results = collect_all(force=True)
        total = sum(n for n, _ in results.values())
        failed = [name for name, (_, m) in results.items() if m.startswith("Error")]
        flash(f"Fetch complete: {total} new items." + (f" Failed sources: {', '.join(failed)}." if failed else ""),
              "success" if not failed else "error")
    log_action("news_fetch", f"source={source_id or 'all'}")
    db.session.commit()
    return redirect(url_for("admin.collected"))


@bp.route("/collected/bulk", methods=["POST"])
def collected_bulk():
    action = request.form.get("action")
    ids = [i for i in (to_int(x) for x in request.form.getlist("ids")) if i]
    if action not in ("approve", "reject") or not ids:
        flash("Select at least one item and an action.", "error")
        return redirect(url_for("admin.collected"))
    items = Article.query.filter(Article.id.in_(ids), Article.kind == "collected").all()
    for a in items:
        if action == "approve":
            if not a.category_id:
                continue
            a.status = "published"
            a.published_at = a.published_at if (a.published_at and a.published_at <= utcnow()) else utcnow()
        else:
            a.status = "rejected"
    log_action(f"collected_{action}", f"{len(items)} items")
    db.session.commit()
    flash(f"{len(items)} item(s) {'approved and published' if action == 'approve' else 'rejected'}.", "success")
    return redirect(url_for("admin.collected", status=request.form.get("status", "pending")))


@bp.route("/sources", methods=["POST"])
@admin_only
def add_source():
    name, url = request.form.get("name", "").strip(), request.form.get("feed_url", "").strip()
    if not name or not is_http_url(url):
        flash("Provide a name and a valid http(s) feed URL.", "error")
    else:
        db.session.add(Source(name=name[:150], kind="rss", feed_url=url[:500], is_active=True))
        log_action("source_added", name)
        db.session.commit()
        flash("Feed added.", "success")
    return redirect(url_for("admin.collected"))


@bp.route("/sources/<int:source_id>/toggle", methods=["POST"])
@admin_only
def toggle_source(source_id):
    s = db.get_or_404(Source, source_id)
    s.is_active = not s.is_active
    log_action("source_toggled", f"{s.name} -> {'on' if s.is_active else 'off'}")
    db.session.commit()
    return redirect(url_for("admin.collected"))


# ----------------------------------------- categories / authors / ticker
def _simple_list(template, items, **ctx):
    return render_template(template, items=items, **ctx)


@bp.route("/categories", methods=["GET", "POST"])
@admin_only
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        cat = db.session.get(Category, to_int(request.form.get("id"), 0)) if request.form.get("id") else None
        if not name or len(name) > 80:
            flash("Category name is required (max 80 characters).", "error")
        elif Category.query.filter(func.lower(Category.name) == name.lower(), Category.id != (cat.id if cat else 0)).first():
            flash("A category with that name already exists.", "error")
        else:
            cat = cat or Category()
            cat.name = name
            cat.description = request.form.get("description", "").strip()[:300]
            cat.sort_order = to_int(request.form.get("sort_order"), 0)
            if not cat.id:
                cat.slug = unique_slug(Category, name)
                db.session.add(cat)
            log_action("category_saved", name)
            db.session.commit()
            flash("Category saved.", "success")
        return redirect(url_for("admin.categories"))
    return _simple_list("admin/categories.html", Category.query.order_by(Category.sort_order, Category.name).all(),
                        title="Categories")


@bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@admin_only
def delete_category(cat_id):
    cat = db.get_or_404(Category, cat_id)
    if cat.articles.count():
        flash("This category still has articles. Move or delete them first.", "error")
    else:
        log_action("category_deleted", cat.name)
        db.session.delete(cat)
        db.session.commit()
        flash("Category deleted.", "success")
    return redirect(url_for("admin.categories"))


@bp.route("/authors", methods=["GET", "POST"])
@admin_only
def authors():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        au = db.session.get(Author, to_int(request.form.get("id"), 0)) if request.form.get("id") else None
        if not name or len(name) > 120:
            flash("Author name is required (max 120 characters).", "error")
        else:
            au = au or Author()
            au.name, au.title = name, request.form.get("title", "").strip()[:150]
            au.bio = request.form.get("bio", "").strip()[:2000]
            if not au.id:
                au.slug = unique_slug(Author, name)
                db.session.add(au)
            log_action("author_saved", name)
            db.session.commit()
            flash("Author saved.", "success")
        return redirect(url_for("admin.authors"))
    return _simple_list("admin/authors.html", Author.query.order_by(Author.name).all(), title="Authors")


@bp.route("/authors/<int:author_id>/delete", methods=["POST"])
@admin_only
def delete_author(author_id):
    au = db.get_or_404(Author, author_id)
    if au.articles.count():
        flash("This author has articles. Reassign them first.", "error")
    else:
        log_action("author_deleted", au.name)
        db.session.delete(au)
        db.session.commit()
        flash("Author deleted.", "success")
    return redirect(url_for("admin.authors"))


@bp.route("/ticker", methods=["GET", "POST"])
def ticker():
    if request.method == "POST":
        text, url = request.form.get("text", "").strip(), request.form.get("url", "").strip()
        if not text or len(text) > 250:
            flash("Ticker text is required (max 250 characters).", "error")
        elif url and not (is_http_url(url) or url.startswith("/")):
            flash("Link must be a full http(s) URL or a site path starting with /.", "error")
        elif url.startswith("//"):
            flash("Invalid link.", "error")
        else:
            db.session.add(TickerItem(text=text, url=url[:500]))
            log_action("ticker_added", text[:80])
            db.session.commit()
            flash("Ticker item added.", "success")
        return redirect(url_for("admin.ticker"))
    return _simple_list("admin/ticker.html", TickerItem.query.order_by(TickerItem.created_at.desc()).all(),
                        title="Breaking ticker")


@bp.route("/ticker/<int:item_id>/<action>", methods=["POST"])
def ticker_action(item_id, action):
    item = db.get_or_404(TickerItem, item_id)
    if action == "toggle":
        item.is_active = not item.is_active
    elif action == "delete":
        db.session.delete(item)
    else:
        abort(404)
    log_action(f"ticker_{action}", item.text[:80])
    db.session.commit()
    return redirect(url_for("admin.ticker"))


# --------------------------------------------- subscribers / logs
@bp.route("/subscribers")
@admin_only
def subscribers():
    term = request.args.get("q", "").strip()
    q = Subscriber.query
    if term:
        q = q.filter(Subscriber.email.ilike(f"%{escape_like(term)}%", escape="\\"))
    pg = q.order_by(Subscriber.created_at.desc()).paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("admin/subscribers.html", pg=pg, term=term, title="Subscribers")


@bp.route("/subscribers/export.csv")
@admin_only
def export_subscribers():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "subscribed_at_utc", "active"])
    for s in Subscriber.query.order_by(Subscriber.created_at):
        email = s.email if not s.email[:1] in "=+-@" else "'" + s.email  # neutralise CSV formula injection
        writer.writerow([email, s.created_at.isoformat(), int(s.is_active)])
    log_action("subscribers_exported")
    db.session.commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=subscribers.csv"})


@bp.route("/subscribers/<int:sub_id>/delete", methods=["POST"])
@admin_only
def delete_subscriber(sub_id):
    s = db.get_or_404(Subscriber, sub_id)
    db.session.delete(s)
    log_action("subscriber_deleted", s.email)
    db.session.commit()
    flash("Subscriber removed.", "success")
    return redirect(url_for("admin.subscribers"))


@bp.route("/logs")
@admin_only
def logs():
    pg = AdminLog.query.order_by(AdminLog.created_at.desc()).paginate(page=_page(), per_page=40, error_out=False)
    return render_template("admin/logs.html", pg=pg, title="Activity log")
