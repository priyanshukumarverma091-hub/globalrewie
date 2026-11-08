from app.textutil import normalize_url, reading_minutes, sanitize_html, slugify, strip_tags, truncate, url_hash


def test_slugify():
    assert slugify("Ceasefire: Gaza & Lebanon — 2026!") == "ceasefire-gaza-lebanon-2026"
    assert slugify("Ünïcödé Hëadline") == "unicode-headline"
    assert slugify("???") == "item"


def test_sanitizer_blocks_xss_vectors():
    dirty = ('<p>ok</p><script>alert(1)</script><img src=x onerror=alert(1)><a href="  JaVa\nscript:alert(1)">x</a>'
             '<a href="https://example.com">fine</a><style>p{}</style><svg onload=alert(1)></svg>')
    clean = sanitize_html(dirty)
    for bad in ("<script", "onerror", "javascript", "onload", "<style", "<svg", "alert(1)<"):
        assert bad not in clean.lower()
    assert '<a href="https://example.com" rel="noopener noreferrer nofollow">fine</a>' in clean
    assert "<p>ok</p>" in clean


def test_sanitizer_balances_and_maps_tags():
    assert sanitize_html("<div>hi<b>bold</div>") == "<p>hi<b>bold</b></p>"
    assert sanitize_html("<h1>T</h1>") == "<h2>T</h2>"


def test_url_normalisation_and_hash():
    a = "https://News.Example.com/story/?utm_source=x&id=5#frag"
    b = "https://news.example.com/story?id=5"
    assert normalize_url(a) == normalize_url(b) and url_hash(a) == url_hash(b)


def test_strip_truncate_reading_time():
    assert strip_tags("<p>Hello <b>world</b></p><script>x</script>") == "Hello world"
    assert truncate("word " * 100, 30).endswith("\u2026") and len(truncate("word " * 100, 30)) <= 30
    assert reading_minutes("<p>" + "word " * 440 + "</p>") == 2
