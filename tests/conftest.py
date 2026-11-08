import pytest

from app import create_app
from app.cli import seed_defaults
from app.extensions import db
from app.models import Article, Author, Category, User, utcnow


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        seed_defaults()
        admin = User(email="admin@example.com", name="Ada Admin", role="admin")
        admin.set_password("correct-horse-battery")
        editor = User(email="editor@example.com", name="Ed Editor", role="editor")
        editor.set_password("another-long-password")
        db.session.add_all([admin, editor, Author(name="Ada Admin", slug="ada-admin", title="Editor", bio="Writes about diplomacy.")])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email="admin@example.com", password="correct-horse-battery"):
    return client.post("/admin/login", data={"email": email, "password": password})


@pytest.fixture()
def admin_client(client):
    assert login(client).status_code == 302
    return client


@pytest.fixture()
def make_article(app):
    def _make(title="Summit ends with new accord", status="published", kind="news", slug=None,
              category="world", body="<p>Leaders agreed a framework.</p>", **kw):
        cat = Category.query.filter_by(slug=category).first()
        au = Author.query.first()
        a = Article(title=title, slug=slug or title.lower().replace(" ", "-"), summary="Short summary.",
                    body=body, kind=kind, status=status, category_id=cat.id, author_id=au.id,
                    published_at=utcnow(), **kw)
        db.session.add(a)
        db.session.commit()
        return a
    return _make
