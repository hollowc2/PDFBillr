# PDFBillr

PDFBillr is a Flask 3.1 invoice-generation SaaS. Anonymous visitors can render
PDFs without persistence. Authenticated users can save invoices; Pro users can
use branding, email delivery, public invoice links, reminders, and recurring
templates. Stripe webhooks are the authority for local subscription state.

Python 3.12 is the supported runtime. SQLite is the currently verified
database. PostgreSQL remains a planned, unverified deployment target until a
real PostgreSQL integration and concurrency tests are added. The Psycopg 3
binary driver is installed so configured PostgreSQL URLs can be exercised
without rebuilding the application image.

## Architecture

```text
HTTP -> Flask Blueprint -> validation/service -> SQLAlchemy
                              |              -> Jinja response
                              |              -> WeasyPrint PDF
                              |              -> Flask-Mail
                              +--------------> Stripe

one scheduler process -> reminder/recurrence services -> database -> mail/PDF
```

- `app.py`: side-effect-free application factory, configuration validation,
  extensions, headers, and explicit Alembic bootstrap command.
- `wsgi.py`: Gunicorn application construction.
- `blueprints/`: public, authentication, dashboard, and billing routes.
- `models.py`: users, subscriptions, invoices, recurring templates, branding,
  and processed Stripe events.
- `utils/invoice_calculations.py`: authoritative bounded Decimal math.
- `utils/pdf.py`: stored/form context and restricted WeasyPrint rendering.
- `utils/scheduler.py`: idempotency-aware job functions.
- `scheduler_worker.py`: the only supported long-running scheduler process.

Routes still own some orchestration and transaction logic. Durable Stripe and
scheduler delivery queues, remaining legacy money/date storage, and workflow
decisions are documented in `CODE_REVIEW_STATE.md`.

## Local development

Create an environment and install both requirement sets:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
cp .env.example .env
set -a
. ./.env
set +a
```

Set a unique development `SECRET_KEY`, then initialize and run:

```bash
AUTO_CREATE_DB=false .venv/bin/flask --app app db-bootstrap
.venv/bin/python app.py
```

The direct development server listens on `http://127.0.0.1:8000`. Keep
`SESSION_COOKIE_SECURE=false` for local HTTP only.

Run checks with:

```bash
.venv/bin/python -m compileall .
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Tests use a temporary SQLite database, suppressed mail, fake Stripe
configuration, disabled rate limits/scheduler startup, a temporary upload
directory, and a deterministic test-only secret. They must not call live Stripe,
SMTP, or external URLs.

## Dependency locking and audits

`requirements.txt` and `requirements-dev.txt` are the short, directly pinned
dependency manifests. Deployments use the Python 3.12 transitive
`requirements.lock`; local development and CI use `requirements-dev.lock`.
Both lock files contain accepted distribution hashes, and Docker/CI install
them with pip's `--require-hashes` enforcement.

After deliberately changing either manifest, regenerate and review both locks:

```bash
uv pip compile requirements.txt \
  --python-version 3.12 --generate-hashes --no-emit-index-url \
  --output-file requirements.lock
uv pip compile requirements.txt requirements-dev.txt \
  --python-version 3.12 --generate-hashes --no-emit-index-url \
  --output-file requirements-dev.lock
pip-audit --progress-spinner off -r requirements.lock
```

The current audit exceptions are WeasyPrint `PYSEC-2026-2034` and
`PYSEC-2026-3412`. The first concerns redirects reached through
`default_url_fetcher`, while PDFBillr's fetcher permits only image `data:` URLs;
the second requires `presentational_hints=True`, which PDFBillr never enables.
CI ignores only these two identifiers, so new advisories still fail the build.
WeasyPrint 68 fixes the first issue, but upgrading from 60 is a major,
PDF-rendering-sensitive change and requires visual/regression testing; the
second currently has no published fixed version. Reassess both exceptions on
every WeasyPrint update.

## Docker Compose

Compose requires a secret rather than silently starting with an empty value:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
mkdir -p instance static/logos
docker compose up --build
```

The image uses a multi-stage build and runs as an unprivileged `pdfbillr`
account. Compose builds that account with `APP_UID=1000` and `APP_GID=1000` by
default so bind-mounted files are normally writable by the host developer. Set
both values to the owning host account before the first build when they differ:

```bash
export APP_UID="$(id -u)"
export APP_GID="$(id -g)"
docker compose build
```

Existing root-owned deployment directories require a deliberate, one-time
ownership correction while the services are stopped. Back them up, verify the
exact host paths, then change only `instance/` and `static/logos/` to the
configured UID/GID. The application never performs this privileged ownership
change itself. Do not replace these bind mounts with empty volumes without
migrating their database and upload contents.

The services are:

- `migrate`: runs `flask --app app db-bootstrap` once.
- `web`: starts Gunicorn only after migration succeeds.
- `scheduler`: starts one blocking APScheduler only after migration succeeds.

The default public URL is `http://localhost:8080/pdfbillr`. Exactly one
`scheduler` replica is permitted. Do not start APScheduler inside Gunicorn
workers. Scaling web workers/replicas also requires shared rate-limit storage;
the default in-memory backend is only correct for one web process.

Production refuses to start with `memory://` rate limiting. Configure an
external Redis service, for example
`RATELIMIT_STORAGE_URI=redis://redis.internal:6379/0`; the Python Redis client
is installed by the application requirements. PDFBillr does not start or
persist Redis for you.

Docker image builds exclude `.env`, local tool logs, databases, and existing
contents under `static/logos/` so customer files cannot be baked into image
layers.

## Configuration

Production must set:

- `APP_ENV=production`
- `SECRET_KEY`: unique and at least 32 characters
- `PUBLIC_BASE_URL`: canonical HTTPS origin, including `/pdfbillr` if used
- `TRUSTED_HOSTS`: comma-separated accepted HTTP hosts
- Stripe keys/price/webhook secret when billing is enabled
- SMTP settings when account or invoice email is enabled

Important operational settings:

- `TRUST_PROXY_HEADERS=false` by default. Enable only when a trusted reverse
  proxy is the sole network path to Gunicorn. Match Gunicorn
  `FORWARDED_ALLOW_IPS` to that topology.
- `AUTO_CREATE_DB=false` in production. Run `db-bootstrap` once before web and
  scheduler startup.
- `SCHEDULER_TIMEZONE=UTC` by default. Scheduler business dates use this zone;
  per-user timezone support remains unresolved.
- `UPLOAD_FOLDER=/app/instance/uploads` stores normalized private logo files.
- `SESSION_COOKIE_SECURE=true` and `ENABLE_HSTS=true` only behind verified HTTPS.
- `RATELIMIT_STORAGE_URI=memory://` supports one process only. Use a supported
  shared backend such as Redis in production. The Redis client dependency is
  included, but the Redis service remains an operator responsibility.
- `WEB_CONCURRENCY=1` controls Gunicorn worker count. More than one worker fails
  fast with the memory limiter and is not supported with SQLite.

Invalid Boolean environment values fail startup instead of silently changing
behavior.

## Request correlation and errors

Every response includes a locally generated `X-Request-ID`. Completion logs
include that identifier, HTTP method, Flask endpoint name, status, and elapsed
time. They intentionally omit paths, query strings, request bodies, remote
addresses, and user data because reset and public-invoice tokens can appear in
paths.

HTML and JSON error responses contain the same request ID and generic text;
internal exception details are never returned to the client. Retain the ID when
reporting an incident. Reverse proxies should forward the response header but
must not overwrite it with a client-supplied request ID.

## Database upgrades

Run:

```bash
flask --app app db-bootstrap
```

Back up the database and stop web/scheduler writers before every upgrade. The
command handles three explicit states:

- A fresh database runs the complete Alembic revision chain.
- A database with an `alembic_version` table upgrades normally to `head`.
- A known unversioned legacy database receives only the historical compatibility
  columns, is checked against the complete baseline schema, is stamped at the
  baseline, and then upgrades to `head`.

Unknown or partial legacy schemas fail without being stamped. Do not bypass the
check with `flask db stamp`. The former `db-upgrade` command remains only as a
deprecated alias so existing automation fails visibly rather than disappearing.
Production Compose uses `db-bootstrap`, and only one migration process may run.

The uniqueness revision refuses to proceed when a user already owns duplicate
invoice numbers. Find affected groups before deployment with:

```sql
SELECT user_id, invoice_number, COUNT(*)
FROM invoices
GROUP BY user_id, invoice_number
HAVING COUNT(*) > 1;
```

After a backup, assign distinct numbers using an audited database maintenance
process, then rerun `db-bootstrap`; the migration never silently renames
financial documents. New user-entered duplicates are rejected, while automated
duplicate/recurring flows choose an explicit numeric suffix.

The session-version revision intentionally invalidates all sessions and
remember cookies created before deployment. Users sign in once after the
upgrade; subsequent password changes and resets revoke all of that user's
existing sessions.

Money remains stored in legacy `Float` columns and invoice/due dates remain
legacy strings even though authoritative calculations use `Decimal`. Converting
them safely requires an inventory of malformed legacy rows, shadow
`Numeric`/`Date` columns, a validated and reversible backfill, application
dual-read compatibility, and only then a constraint/cutover migration. That
data-dependent conversion is intentionally not performed automatically.

## Scheduler reliability

Run exactly one:

```bash
DISABLE_SCHEDULER=false python scheduler_worker.py
```

Web app construction never starts jobs. Each recurring schedule occurrence has
a database uniqueness claim, and reminder/auto-send email uses a durable
delivery row committed before SMTP. Failures remain retryable after the
original trigger day; short leases prevent concurrent scheduler processes from
claiming the same row simultaneously, and retry jobs run every five minutes.
Recurring work rechecks Pro entitlement and advances from the recorded
scheduled date.

The delivery queues provide at-least-once recovery. No SMTP interface can
guarantee exactly-once delivery across a crash after the server accepts a
message but before PDFBillr records success, so downstream mail deduplication
and monitoring remain appropriate.

## Stripe

Configure the webhook endpoint at:

```text
POST <PUBLIC_BASE_URL>/billing/webhook
```

The handler verifies Stripe’s signature over the raw body, records event IDs,
commits state and the processed marker together, enforces the configured Pro
price/customer/subscription binding, rejects stale subscription events, and
sends best-effort notification email only after commit. Tests mock all Stripe
calls.

Do not log webhook bodies, signatures, secrets, or token-bearing invoice URLs.
Gunicorn’s access format intentionally omits URL paths.

Public invoice links remain bearer credentials. Owners can rotate or revoke a
link from the invoice detail page; the prior token immediately returns 404.
Tokens do not automatically expire, so avoid posting them in tickets or logs.

## Mail and partial failure

Welcome/reset/billing/invoice links are generated from `PUBLIC_BASE_URL`, never
from a request-supplied Host. Invoice status changes to `sent` only after
Flask-Mail returns successfully. Reminder flags change only after success.

Billing notification email is queued atomically with durable webhook state.
Failures do not roll back correct billing state and are retried by the
single scheduler process every five minutes. Delivery errors record only an
exception type, not message contents or customer data.

## Upload and PDF safety

Logo uploads are capped by request bytes, decoded with Pillow, checked for
format/dimensions/pixel count/animation, re-encoded to a server-named PNG, and
stored under `instance/uploads`. They are served only through the owning
authenticated Pro route. Legacy static logo lookup remains read-only for
upgrade compatibility.

WeasyPrint accepts only in-memory image data URLs from normalized/legacy logos.
HTTP, HTTPS, file, and non-image data resources are denied. User text remains
Jinja-autoescaped.

## Health

- `GET /health/live`: process liveness only.
- `GET /health`: PDF/database/shared-limiter readiness; returns `503` when
  degraded.
- Send `Accept: application/json` for machine-readable checks.

No secrets, configuration values, database contents, or customer diagnostics
are returned.

## Persistence, backup, and restore

Persist and back up:

- `/app/instance/pdfbillr.db` (or the configured SQLite file)
- `/app/instance/uploads/`
- legacy `/app/static/logos/` only while upgrading existing installations

For SQLite, stop web and scheduler writes, copy the database with SQLite’s
backup API (or a transactionally safe snapshot), copy uploads, and verify both
copies. Restore into a stopped deployment, preserve file ownership/permissions,
run `db-bootstrap`, then start web followed by exactly one scheduler. Test restores
regularly; a file copy of a live WAL database is not a backup procedure.

## Production checklist

- Use a unique secret, canonical HTTPS URL, trusted hosts, and correct proxy
  isolation.
- Configure shared Redis-backed rate limiting; production rejects `memory://`.
- Run the schema upgrade once and back up before upgrading.
- Run one scheduler and one web process when using the memory limiter/SQLite.
- Persist and back up database/uploads.
- Build with the host UID/GID that owns the bind mounts and verify the
  unprivileged container can write its database and upload directories.
- Configure/test Stripe signature secret, allowed price, and SMTP.
- Restrict and redact reverse-proxy/application logs; token paths are secrets.
- Monitor `/health`, SMTP failures, Stripe 5xx responses, scheduler exceptions,
  disk usage, and backup age.
- Include `X-Request-ID` in proxy logs and support reports without logging URL
  paths that contain bearer tokens.
- Keep debug off and do not expose Gunicorn directly when proxy trust is on.
- Review `CODE_REVIEW_STATE.md` for unresolved legacy money/date migration,
  catch-up/timezone, paid-state, and public-token expiry policy work.
