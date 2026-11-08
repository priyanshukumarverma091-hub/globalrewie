from app.extensions import db
from app.models import Article, Subscriber


def test_homepage_empty_state_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"The Global Review" in r.data
    assert b"No stories published yet" in r.data


def test_homepage_shows_published_but_not_drafts(client, make_article):
    make_article("Visible story")
    make_article("Hidden draft", status="draft")
    html = client.get("/").get_data(as_text=True)
    assert "Visible story" in html and "Hidden draft" not in html


def test_article_page_and_seo(client, make_article):
    a = make_article("Treaty talks resume")
    r = client.get(f"/article/{a.slug}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "<h1 class=\"story-title\">Treaty talks resume</h1>" in html
    assert 'property="og:title"' in html and "application/ld+json" in html and "NewsArticle" in html
    assert "min read" in html


def test_unpublished_article_is_404(client, make_article):
    a = make_article("Secret", status="draft")
    assert client.get(f"/article/{a.slug}").status_code == 404


def test_collected_article_is_noindex_and_links_to_source(client, make_article):
    a = make_article("Wire headline", kind="collected", status="published", source_name="Example Wire",
                     source_url="https://example.com/story")
    html = client.get(f"/article/{a.slug}").get_data(as_text=True)
    assert 'content="noindex,follow"' in html and "https://example.com/story" in html
    assert "did not write or verify" in html


def test_analysis_labelled_and_listed(client, make_article):
    make_article("Why the pact matters", kind="analysis", slug="why-pact", country="Norway")
    html = client.get("/analysis").get_data(as_text=True)
    assert "Why the pact matters" in html
    assert "This is analysis" in client.get("/article/why-pact").get_data(as_text=True)


def test_search_keywords_filters_sort_pagination(client, make_article):
    make_article("Sanctions package announced", category="economy")
    make_article("Naval exercise begins", category="defence")
    assert "Sanctions package" in client.get("/search?q=sanctions").get_data(as_text=True)
    assert "Naval exercise" not in client.get("/search?q=sanctions").get_data(as_text=True)
    html = client.get("/search?category=defence").get_data(as_text=True)
    assert "Naval exercise" in html and "Sanctions package" not in html
    assert client.get("/search?sort=oldest&from=2000-01-01&to=2100-01-01").status_code == 200
    assert client.get("/search?q=%25_%5C").status_code == 200  # LIKE wildcards are escaped
    assert client.get("/search?page=999").status_code == 200


def test_category_page_filters(client, make_article):
    make_article("Budget deal", category="economy")
    make_article("Border clash", category="conflicts")
    html = client.get("/category/economy").get_data(as_text=True)
    assert "Budget deal" in html and "Border clash" not in html
    assert client.get("/category/nope").status_code == 404


def test_subscribe_valid_invalid_and_honeypot(client, app):
    client.post("/subscribe", data={"email": "reader@example.com"})
    client.post("/subscribe", data={"email": "not-an-email"})
    client.post("/subscribe", data={"email": "bot@example.com", "website": "spam"})
    with app.app_context():
        assert [s.email for s in Subscriber.query.all()] == ["reader@example.com"]


def test_robots_sitemap_health_and_errors(client, make_article):
    make_article("Indexed story")
    assert "Disallow: /admin/" in client.get("/robots.txt").get_data(as_text=True)
    assert "Indexed story".lower().replace(" ", "-") in client.get("/sitemap.xml").get_data(as_text=True)
    assert client.get("/healthz").get_json()["database"] == "up"
    assert client.get("/missing-page").status_code == 404


def test_mobile_markup_present(client):
    html = client.get("/").get_data(as_text=True)
    assert 'name="viewport"' in html and "nav-toggle" in html
    css = client.get("/static/css/main.css").get_data(as_text=True)
    assert "@media (max-width:900px)" in css and "@media (max-width:600px)" in css


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in r.headers["Content-Security-Policy"]
