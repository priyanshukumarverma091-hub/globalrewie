from datetime import timedelta

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)
from sqlalchemy import or_

from ..extensions import csrf, db, limiter
from ..models import Article, Author, Category, Subscriber, utcnow
from ..textutil import escape_like
from ..utils import EMAIL_RE, parse_date, safe_next_url

bp = Blueprint("public", __name__)

SECTIONS = [  # (heading, category slug, include editorial analysis?)
    ("Geopolitical Analysis", "geopolitics"),
    ("Global Conflicts", "conflicts"),
    ("Defence Watch", "defence"),
    ("Global Economy", "economy"),
    ("Diplomatic Developments", "diplomacy"),
    ("Technology & AI", "technology"),
]


def _per_page():
    return current_app.config["ARTICLES_PER_PAGE"]


def _page():
    return max(1, request.args.get("page", 1, type=int) or 1)


@bp.route("/")
def index():
    live = Article.live
    order = (Article.published_at.desc(),)
    lead = (live().filter(Article.is_featured.is_(True), Article.kind != "collected").order_by(*order).first()
            or live().filter(Article.kind != "collected").order_by(*order).first()
            or live().order_by(*order).first())
    used = {lead.id} if lead else set()

    secondary = []
    if lead:
        secondary = (live().filter(Article.id.notin_(used)).filter(Article.kind != "collected")
                     .order_by(*order).limit(4).all())
        used |= {a.id for a in secondary}
    latest = live().filter(Article.id.notin_(used or {0})).order_by(*order).limit(14).all()

    sections = []
    for heading, slug in SECTIONS:
        items = (live().join(Category).filter(Category.slug == slug).order_by(*order).limit(4).all())
        if items:
            sections.append({"heading": heading, "slug": slug, "items": items})

    month_ago, two_days = utcnow() - timedelta(days=30), utcnow() - timedelta(days=2)
    most_read = (live().filter(Article.published_at >= month_ago, Article.views > 0)
                 .order_by(Article.views.desc()).limit(5).all())
    trending = (live().filter(Article.published_at >= two_days, Article.views > 0)
                .order_by(Article.views.desc()).limit(5).all())
    picks = live().filter(Article.is_editors_pick.is_(True)).order_by(*order).limit(4).all()
    analysis = live().filter(Article.kind.in_(("analysis", "opinion"))).order_by(*order).limit(3).all()
    return render_template("index.html", lead=lead, secondary=secondary, latest=latest, sections=sections,
                           most_read=most_read, trending=trending, picks=picks, analysis=analysis)


@bp.route("/article/<slug>")
def article(slug):
    a = Article.live().filter(Article.slug == slug).first_or_404()
    Article.query.filter_by(id=a.id).update({"views": Article.views + 1})
    db.session.commit()
    related = (Article.live().filter(Article.category_id == a.category_id, Article.id != a.id)
               .order_by(Article.published_at.desc()).limit(4).all())
    return render_template("article.html", a=a, related=related, preview=False)


@bp.route("/category/<slug>")
def category(slug):
    cat = Category.query.filter_by(slug=slug).first_or_404()
    sort = request.args.get("sort", "newest")
    q = Article.live().filter(Article.category_id == cat.id)
    q = q.order_by(Article.published_at.asc() if sort == "oldest" else Article.published_at.desc())
    pg = q.paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("listing.html", heading=cat.name, intro=cat.description, pg=pg,
                           canonical=url_for("public.category", slug=slug, _external=True), sort=sort)


@bp.route("/analysis")
def analysis():
    kind = request.args.get("kind", "")
    atype = request.args.get("type", "")
    q = Article.live().filter(Article.kind.in_(("analysis", "opinion")))
    if kind in ("analysis", "opinion"):
        q = q.filter(Article.kind == kind)
    if atype in Article.ANALYSIS_TYPES and atype:
        q = q.filter(Article.analysis_type == atype)
    pg = q.order_by(Article.published_at.desc()).paginate(page=_page(), per_page=_per_page(), error_out=False)
    return render_template("listing.html", heading="Analysis", pg=pg, analysis_filters=True, types=Article.ANALYSIS_TYPES,
                           intro="Long-form geopolitical analysis, country studies, conflict timelines "
                                 "and expert commentary. Analysis and opinion are labelled separately "
                                 "from reported news.", sort="newest")


@bp.route("/author/<slug>")
def author(slug):
    au = Author.query.filter_by(slug=slug).first_or_404()
    pg = (Article.live().filter(Article.author_id == au.id).order_by(Article.published_at.desc())
          .paginate(page=_page(), per_page=_per_page(), error_out=False))
    return render_template("listing.html", heading=au.name, intro=f"{au.title}. {au.bio}".strip(". "),
                           pg=pg, sort="newest")


@bp.route("/search")
@limiter.limit("60 per minute")
def search():
    term = request.args.get("q", "").strip()[:100]
    cat_slug = request.args.get("category", "")
    kind = request.args.get("kind", "")
    sort = request.args.get("sort", "newest")
    d_from, d_to = parse_date(request.args.get("from", "")), parse_date(request.args.get("to", ""))

    q = Article.live()
    for word in term.split()[:6]:
        like = f"%{escape_like(word)}%"
        q = q.filter(or_(Article.title.ilike(like, escape="\\"), Article.subtitle.ilike(like, escape="\\"),
                         Article.summary.ilike(like, escape="\\"), Article.body.ilike(like, escape="\\"),
                         Article.tags.ilike(like, escape="\\")))
    if cat_slug:
        q = q.join(Category).filter(Category.slug == cat_slug)
    if kind in Article.KINDS:
        q = q.filter(Article.kind == kind)
    if d_from:
        q = q.filter(Article.published_at >= d_from)
    if d_to:
        q = q.filter(Article.published_at < d_to + timedelta(days=1))
    q = q.order_by(Article.published_at.asc() if sort == "oldest" else Article.published_at.desc())
    pg = q.paginate(page=_page(), per_page=_per_page(), error_out=False)
    categories = Category.query.order_by(Category.sort_order, Category.name).all()
    return render_template("search.html", pg=pg, term=term, categories=categories, cat_slug=cat_slug,
                           kind=kind, sort=sort, d_from=request.args.get("from", ""),
                           d_to=request.args.get("to", ""))


@bp.route("/subscribe", methods=["POST"])
@limiter.limit("10 per hour")
def subscribe():
    email = request.form.get("email", "").strip().lower()
    back = safe_next_url(request.form.get("next")) or url_for("public.index")
    if request.form.get("website"):  # honeypot field: bots fill it, humans don't see it
        return redirect(back)
    if not EMAIL_RE.match(email) or len(email) > 254:
        flash("Please enter a valid email address.", "error")
        return redirect(back + "#newsletter")
    existing = Subscriber.query.filter_by(email=email).first()
    if existing:
        existing.is_active = True
    else:
        db.session.add(Subscriber(email=email))
    db.session.commit()
    flash("Thank you. You are subscribed to The Global Review newsletter.", "success")
    return redirect(back + "#newsletter")


@bp.route("/page/<name>")
def static_page(name):
    pages = {"about": "About", "contact": "Contact", "privacy": "Privacy Policy", "terms": "Terms of Use"}
    if name not in pages:
        abort(404)
    return render_template(f"pages/{name}.html", heading=pages[name])


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    resp = send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, max_age=31536000)
    return resp


@bp.route("/robots.txt")
def robots():
    body = ("User-agent: *\nDisallow: /admin/\nDisallow: /search\nAllow: /\n\n"
            f"Sitemap: {url_for('public.sitemap', _external=True)}\n")
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    urls = [(url_for("public.index", _external=True), None),
            (url_for("public.analysis", _external=True), None)]
    urls += [(url_for("public.static_page", name=n, _external=True), None) for n in ("about", "contact")]
    urls += [(url_for("public.category", slug=c.slug, _external=True), None) for c in Category.query.all()]
    for a in (Article.live().filter(Article.kind != "collected").order_by(Article.published_at.desc())
              .limit(5000)):
        urls.append((url_for("public.article", slug=a.slug, _external=True), a.updated_at or a.published_at))
    return Response(render_template("sitemap.xml", urls=urls), mimetype="application/xml")


@bp.route("/healthz")
def healthz():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify(status="ok", database="up")
    except Exception:
        db.session.rollback()
        return jsonify(status="degraded", database="down"), 503


@bp.route("/tasks/fetch-news", methods=["POST"])
@csrf.exempt
@limiter.limit("6 per hour")
def cron_fetch():
    """Optional endpoint for free external schedulers (cron-job.org, GitHub Actions).
    Disabled unless CRON_TOKEN is set; requires header X-Cron-Token."""
    import hmac

    from ..services.collector import collect_all
    token = current_app.config["CRON_TOKEN"]
    supplied = request.headers.get("X-Cron-Token", "")
    if not token or not hmac.compare_digest(token, supplied):
        abort(404)
    results = collect_all()
    return jsonify({name: {"new": n, "status": msg} for name, (n, msg) in results.items()})
