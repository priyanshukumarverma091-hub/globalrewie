# The Global Review

An independent international news and geopolitical analysis platform: public newspaper-style site, admin dashboard, news collection with editorial review, search, SEO and free-tier deployment config.

**Stack:** Python 3.12 + Flask, SQLAlchemy (Flask-SQLAlchemy), PostgreSQL (SQLite fallback for local use), Flask-Login, Flask-WTF (CSRF), Flask-Limiter, Flask-Migrate, Pillow, Jinja templates, plain CSS/JS (no build step).

## Project structure

```
globalreview/
├── app/
│   ├── __init__.py          app factory, security headers, error handlers
│   ├── extensions.py        db, migrate, login, csrf, limiter
│   ├── models.py            User, Article, Category, Author, Source, TickerItem, Subscriber, AdminLog
│   ├── textutil.py          slugify, HTML sanitiser, URL normalising (pure, testable)
│   ├── utils.py             slugs, validated image upload, audit logging
│   ├── cli.py               flask init-db | create-admin | reset-password | fetch-news
│   ├── blueprints/
│   │   ├── public.py        homepage, article, category, analysis, search, subscribe, sitemap, robots
│   │   └── admin.py         login, dashboard, articles CRUD, review queue, categories, authors, ticker, subscribers, logs
│   ├── services/
│   │   ├── feeds.py         RSS/Atom, GDELT, NewsAPI parsers + topic classifier
│   │   └── collector.py     fetching, caching, de-duplication, failure handling
│   ├── templates/           public pages, admin/, errors/, pages/
│   └── static/              css/main.css, css/admin.css, js/main.js, js/admin.js
├── tests/                   pytest suite
├── config.py  wsgi.py  requirements.txt  requirements-dev.txt
├── .env.example  Procfile  render.yaml  runtime.txt  pytest.ini
```

## Run locally on Windows (PowerShell)

1. Install Python 3.12 from python.org (tick "Add python.exe to PATH") and optionally Git.
2. Open PowerShell in the project folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements-dev.txt
   copy .env.example .env
   ```
   If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
3. Generate a secret and paste it into `.env` as `SECRET_KEY`:
   ```powershell
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
4. Create the database and seed categories and news feeds:
   ```powershell
   flask --app wsgi init-db
   ```
5. Create your admin account (you will be prompted; password minimum 12 characters):
   ```powershell
   flask --app wsgi create-admin
   ```
6. Start the site:
   ```powershell
   flask --app wsgi run --debug
   ```
   Open http://127.0.0.1:5000 and sign in at http://127.0.0.1:5000/admin/login.

With `DATABASE_URL` empty the app uses a local SQLite file (`instance/globalreview.db`) so it runs immediately.

### Local PostgreSQL (optional)
Install PostgreSQL, then in `psql`: `CREATE DATABASE globalreview;`. Set in `.env`:
`DATABASE_URL=postgresql://postgres:YOURPASSWORD@localhost:5432/globalreview` and run `flask --app wsgi init-db` again.

## First-use workflow

1. **Admin > Authors**: add bylines (your own author is created with the admin account).
2. **Admin > New article**: write with the rich-text editor, choose type (Reported news / Analysis / Opinion), category, author, image. Preview, then Publish.
3. **Admin > Collected news**: click *Fetch from all active sources*. Headlines land as **pending**; tick the ones worth carrying and *Approve & publish*, or reject. Approved items show only headline, publisher summary, source name and a link to the original, and are labelled "Aggregated headline".
4. **Admin > Breaking ticker**: add short lines (optionally linked) for the scrolling bar.

No sample news is seeded. The homepage shows an honest empty state until you publish or approve something.

## News sources and API configuration

| Source | Setup |
|---|---|
| RSS (BBC, Guardian, Al Jazeera, UN News, DW, NPR) | Seeded and active. Add more under *Collected news*. Check each outlet's terms before relying on its feed. |
| GDELT DOC API | Seeded and active, no key needed. Edit the query in the `sources` table if desired. |
| NewsAPI | Set `NEWSAPI_KEY` in `.env`, then enable the *NewsAPI* source. Free developer keys have usage limits and delayed results. |

- **Caching:** each source is re-fetched at most once per `FETCH_CACHE_MINUTES` (default 30) unless an editor forces it.
- **De-duplication:** by SHA-256 of the normalised URL (tracking parameters stripped).
- **Failures:** timeouts, bad XML, HTTP errors and missing keys are recorded per source and never crash a request.
- **Rights:** the collector stores only headline, a publisher-supplied summary (max 300 characters) and the original link. It never copies article text or images.
- **Scheduling:** set `CRON_TOKEN`, then have a free scheduler (cron-job.org or a GitHub Actions cron) call `POST https://YOUR-SITE/tasks/fetch-news` with header `X-Cron-Token: <token>`. Or run `flask --app wsgi fetch-news` yourself.

## Database

Tables: `users`, `articles`, `categories`, `authors`, `sources`, `ticker_items`, `subscribers`, `admin_logs` (see `app/models.py`). `flask init-db` creates them idempotently and is what Render runs on start.

**Migrations (Flask-Migrate/Alembic)** are wired in. Generate your first migration set once, commit `migrations/`, and use it for schema changes:
```powershell
flask --app wsgi db init
flask --app wsgi db migrate -m "initial schema"
flask --app wsgi db upgrade
```
If you adopt migrations on a database already created by `init-db`, run `flask --app wsgi db stamp head` after the first `db migrate`.

## Testing

```powershell
pytest -v
```
The suite uses an in-memory SQLite database and covers: homepage, article pages, SEO tags, search/filters/pagination, category pages, admin login/logout/lockdown, article create/edit/publish/unpublish/delete, validation, HTML sanitising, role permissions, image upload validation, collector parsing/dedup/cache/failure handling, the review queue, the cron endpoint, health check and security headers. Visual mobile behaviour (menu, layout at 380 px) should be checked in a browser's device toolbar.

## Deploy free: Render + Neon

1. **Neon** (neon.tech): create a project and database; copy the connection string (`postgresql://...?sslmode=require`).
2. Push this folder to a **private GitHub repo** (`.env` is git-ignored; never commit secrets).
3. **Render**: New > Blueprint > select the repo (uses `render.yaml`), or create a Web Service manually with:
   - Build: `pip install -r requirements.txt`
   - Start: `flask --app wsgi init-db && flask --app wsgi create-admin --from-env && gunicorn wsgi:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
4. Set environment variables in the Render dashboard: `APP_ENV=production`, `SECRET_KEY` (generated), `DATABASE_URL` (Neon), `SITE_URL` (your onrender.com URL), `CONTACT_EMAIL`, and for the first admin `ADMIN_EMAIL`, `ADMIN_NAME`, `ADMIN_PASSWORD` (12+ chars). **Delete `ADMIN_PASSWORD` after your first login.** Optionally `NEWSAPI_KEY`, `CRON_TOKEN`.
5. Visit `/healthz`, then `/admin/login`.

Vercel is **not needed**: the frontend is server-rendered by Flask.

### Free-tier limitations (verify current terms on each provider)
- **Render free web services sleep after ~15 minutes without traffic**; the first request afterwards can take 30-60 s. A scheduler pinging `/healthz` reduces this but may be limited by monthly free hours.
- **Render's free filesystem is ephemeral: uploaded images are deleted on every redeploy/restart.** For anything beyond a demo, attach a persistent disk (paid) and set `UPLOAD_DIR`, or move uploads to object storage (e.g. Cloudflare R2, S3-compatible) by adapting `save_image` in `app/utils.py`.
- **Neon free** databases have small storage and compute limits and auto-suspend when idle (the app uses `pool_pre_ping` to reconnect).
- Free instances have limited CPU/RAM: keep `--workers 2`. Rate limits use in-memory storage, so they are per-worker (use a Redis URL in `RATELIMIT_STORAGE_URI` for shared limits).
- Render shell access is not available on the free plan, which is why the admin is created from environment variables at start-up.

## Security notes

- Passwords hashed with Werkzeug (scrypt); login throttled (5/min, 30/h per IP) with generic errors and equalised timing; all attempts are written to the activity log.
- CSRF tokens on every POST; `SameSite=Lax`, `HttpOnly`, and `Secure` (production) session cookies; 8-hour sessions; admin pages send `Cache-Control: no-store`.
- Role checks: `editor` can write/publish; `admin` additionally deletes content and manages categories, authors, sources, subscribers and logs.
- Article HTML passes an allow-list sanitiser on save; links get `rel="noopener noreferrer nofollow"`; the CSP forbids inline scripts and styles.
- Uploads: extension allow-list, Pillow verification and **re-encoding** (strips metadata/payloads), pixel-count limit, random filenames, 6 MB request cap.
- All queries go through SQLAlchemy; LIKE wildcards from search input are escaped; redirects accept same-site paths only; CSV export neutralises spreadsheet formulas.
- The RSS parser rejects XML with entity declarations (XXE/entity-expansion protection).
- Production refuses to start without `SECRET_KEY`. No credentials live in code or templates.

## Content integrity

Every item is labelled: **Reported news**, **Analysis**, **Opinion**, or **Aggregated headline**. Analysis and opinion pages carry an explicit notice; aggregated items state that the site did not write or verify them, are `noindex`, and link to the original. The privacy and terms pages are templates: have them reviewed before launch.

## Known limitations / next steps
- Migrations folder is not pre-generated (see Database).
- Admin has no user-management screen: add users with `flask create-admin --role editor`.
- Newsletter subscribers are collected and exportable; sending mail is intentionally left to your provider of choice (export CSV or wire up a service).
- No full-text ranking (search is case-insensitive substring matching across headline, subtitle, summary, body and tags), which is fine for thousands of articles; add PostgreSQL full-text search if you outgrow it.
