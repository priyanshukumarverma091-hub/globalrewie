import requests

from app.extensions import db
from app.models import Article, Source
from app.services import collector, feeds

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>UN Security Council debates ceasefire</title><link>https://news.example.com/a?utm_source=x</link>
<description>&lt;p&gt;Diplomats met on Tuesday.&lt;/p&gt;</description><pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Central bank raises interest rate</title><link>https://news.example.com/b</link><description>Inflation pressures.</description></item>
</channel></rss>"""


def test_parse_feed_and_classify():
    items = feeds.parse_feed(RSS)
    assert len(items) == 2 and items[0]["summary"] == "Diplomats met on Tuesday."
    assert feeds.classify(items[1]["title"], items[1]["summary"]) == "economy"


def test_feed_with_entities_rejected():
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>'
    try:
        feeds.parse_feed(bad)
    except feeds.FeedError:
        return
    raise AssertionError("entity declarations must be rejected")


def test_collect_dedupes_and_marks_pending(app, monkeypatch):
    monkeypatch.setattr(collector, "_http_get", lambda *a, **k: RSS)
    with app.app_context():
        src = Source.query.filter_by(kind="rss").first()
        added, _ = collector.collect_source(src, force=True)
        again, _ = collector.collect_source(src, force=True)
        assert added == 2 and again == 0
        assert {a.status for a in Article.query.all()} == {"pending"}
        assert Article.query.filter_by(kind="collected").count() == 2


def test_cache_prevents_repeat_fetch(app, monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "_http_get", lambda *a, **k: calls.append(1) or RSS)
    with app.app_context():
        src = Source.query.filter_by(kind="rss").first()
        collector.collect_source(src, force=True)
        _, msg = collector.collect_source(src)  # within TTL
        assert len(calls) == 1 and "cached" in msg


def test_api_failure_is_handled_gracefully(app, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("network down")
    monkeypatch.setattr(requests, "get", boom)
    with app.app_context():
        src = Source.query.filter_by(kind="rss").first()
        added, msg = collector.collect_source(src, force=True)
        assert added == 0 and msg.startswith("Error")
        assert db.session.get(Source, src.id).last_status.startswith("Error")
        results = collector.collect_all(force=True)  # must not raise
        assert all(n == 0 for n, _ in results.values())


def test_malformed_xml_is_handled(app, monkeypatch):
    monkeypatch.setattr(collector, "_http_get", lambda *a, **k: b"<html>not a feed")
    with app.app_context():
        src = Source.query.filter_by(kind="rss").first()
        assert collector.collect_source(src, force=True)[1].startswith("Error")


def test_review_workflow_approve_and_reject(admin_client, app, monkeypatch):
    monkeypatch.setattr(collector, "_http_get", lambda *a, **k: RSS)
    admin_client.post("/admin/collected/fetch", data={})
    with app.app_context():
        ids = [a.id for a in Article.query.filter_by(kind="collected").order_by(Article.id)]
        assert len(ids) >= 2
    assert b"UN Security Council" not in admin_client.get("/").data  # not public before approval
    admin_client.post("/admin/collected/bulk", data={"action": "approve", "ids": [str(ids[0])]})
    admin_client.post("/admin/collected/bulk", data={"action": "reject", "ids": [str(ids[1])]})
    with app.app_context():
        assert db.session.get(Article, ids[0]).status == "published"
        assert db.session.get(Article, ids[1]).status == "rejected"


def test_cron_endpoint_needs_token(client, monkeypatch):
    monkeypatch.setattr(collector, "_http_get", lambda *a, **k: RSS)
    assert client.post("/tasks/fetch-news").status_code == 404
    assert client.post("/tasks/fetch-news", headers={"X-Cron-Token": "wrong"}).status_code == 404
    assert client.post("/tasks/fetch-news", headers={"X-Cron-Token": "test-cron-token"}).status_code == 200
