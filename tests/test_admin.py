import io

from PIL import Image

from app.extensions import db
from app.models import AdminLog, Article, Category
from tests.conftest import login


def _payload(**over):
    with_defaults = dict(title="Ceasefire holds", subtitle="", summary="A fragile calm.", kind="news",
                         body="<p>Officials confirmed the truce.</p>", category_id="", author_id="1", action="publish")
    with_defaults.update(over)
    return with_defaults


def _cat_id(app, slug="world"):
    with app.app_context():
        return Category.query.filter_by(slug=slug).first().id


def test_admin_routes_require_login(client):
    for path in ("/admin/", "/admin/articles", "/admin/articles/new", "/admin/collected", "/admin/subscribers",
                 "/admin/categories", "/admin/logs", "/admin/ticker"):
        r = client.get(path)
        assert r.status_code == 302 and "/admin/login" in r.headers["Location"], path
    assert client.post("/admin/articles/1/delete").status_code == 302


def test_login_logout_and_failures(client, app):
    assert client.post("/admin/login", data={"email": "admin@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/admin/login", data={"email": "ghost@example.com", "password": "x"}).status_code == 401
    assert login(client).status_code == 302
    assert client.get("/admin/").status_code == 200
    assert client.post("/admin/logout").status_code == 302
    assert client.get("/admin/").status_code == 302
    with app.app_context():
        assert AdminLog.query.filter_by(action="login_failed").count() == 2


def test_login_redirect_is_same_site_only(client):
    r = client.post("/admin/login?next=https://evil.example/", data={"email": "admin@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_create_edit_publish_delete_article(admin_client, app):
    cid = _cat_id(app)
    r = admin_client.post("/admin/articles/new", data=_payload(category_id=cid))
    assert r.status_code == 302
    with app.app_context():
        a = Article.query.one()
        assert a.status == "published" and a.slug == "ceasefire-holds" and a.published_at
        aid = a.id
    assert admin_client.get("/").status_code == 200 and b"Ceasefire holds" in admin_client.get("/").data

    r = admin_client.post(f"/admin/articles/{aid}/edit", data=_payload(title="Ceasefire holds for a week", category_id=cid, action="save"))
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Article, aid).title == "Ceasefire holds for a week"

    admin_client.post(f"/admin/articles/{aid}/toggle")
    with app.app_context():
        assert db.session.get(Article, aid).status == "draft"
    assert admin_client.get("/article/ceasefire-holds").status_code == 404

    assert admin_client.post(f"/admin/articles/{aid}/delete").status_code == 302
    with app.app_context():
        assert Article.query.count() == 0


def test_validation_errors_do_not_save(admin_client, app):
    r = admin_client.post("/admin/articles/new", data=_payload(title="", category_id=_cat_id(app)))
    assert r.status_code == 400 and b"headline is required" in r.data
    r = admin_client.post("/admin/articles/new", data=_payload(category_id="", action="publish"))
    assert r.status_code == 400 and b"Choose a category" in r.data
    with app.app_context():
        assert Article.query.count() == 0


def test_body_is_sanitised_on_save(admin_client, app):
    body = '<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:alert(1)">bad</a><iframe src="//e"></iframe>'
    admin_client.post("/admin/articles/new", data=_payload(category_id=_cat_id(app), body=body))
    with app.app_context():
        saved = Article.query.one().body
    assert "<script" not in saved and "onclick" not in saved and "javascript:" not in saved and "iframe" not in saved


def test_editor_cannot_delete_or_manage_categories(client, app, make_article):
    a = make_article("Protected")
    login(client, "editor@example.com", "another-long-password")
    assert client.post(f"/admin/articles/{a.id}/delete").status_code == 403
    assert client.get("/admin/categories").status_code == 403
    assert client.get("/admin/subscribers").status_code == 403
    assert client.get("/admin/articles").status_code == 200


def test_image_upload_validation(admin_client, app):
    cid = _cat_id(app)
    bad = _payload(category_id=cid, image=(io.BytesIO(b"<?php evil ?>"), "shell.php"))
    assert admin_client.post("/admin/articles/new", data=bad, content_type="multipart/form-data").status_code == 400
    fake = _payload(category_id=cid, image=(io.BytesIO(b"not really a png"), "fake.png"))
    assert admin_client.post("/admin/articles/new", data=fake, content_type="multipart/form-data").status_code == 400
    buf = io.BytesIO()
    Image.new("RGB", (1200, 700), "navy").save(buf, "PNG")
    buf.seek(0)
    ok = _payload(category_id=cid, image=(buf, "photo.png"))
    assert admin_client.post("/admin/articles/new", data=ok, content_type="multipart/form-data").status_code == 302
    with app.app_context():
        name = Article.query.one().featured_image
    assert name.endswith(".jpg") and admin_client.get(f"/uploads/{name}").status_code == 200


def test_categories_authors_ticker_management(admin_client, app):
    admin_client.post("/admin/categories", data={"name": "Space Policy", "description": "x", "sort_order": "3"})
    admin_client.post("/admin/authors", data={"name": "Grace Hopper", "title": "Correspondent", "bio": ""})
    admin_client.post("/admin/ticker", data={"text": "Talks begin in Geneva", "url": "/analysis"})
    admin_client.post("/admin/ticker", data={"text": "bad", "url": "javascript:alert(1)"})
    with app.app_context():
        assert Category.query.filter_by(slug="space-policy").count() == 1
    html = admin_client.get("/analysis").get_data(as_text=True)  # public page shows the live ticker
    assert "Talks begin in Geneva" in html and "javascript:alert" not in html


def test_subscriber_export_requires_admin(client, admin_client):
    assert admin_client.get("/admin/subscribers/export.csv").mimetype == "text/csv"
