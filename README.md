# PDFBillr

PDFBillr is a Flask 3.1 invoice-generation SaaS. Anonymous visitors can render
PDFs without persistence. Authenticated users can save invoices; Pro users can
use branding, email delivery, public invoice links, reminders, and recurring
templates. Stripe webhooks are the authority for local subscription state.

Python 3.12 is the supported runtime. SQLite is the currently verified
database. PostgreSQL remains a planned, unverified deployment target until a
driver, Alembic migration set, and integration test are added.

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
  extensions, headers, and explicit `db-upgrade` command.
- `wsgi.py`: Gunicorn application construction.
- `blueprints/`: public, authentication, dashboard, and billing routes.
- `models.py`: users, subscriptions, invoices, recurring templates, branding,
  and processed Stripe events.
- `utils/invoice_calculations.py`: authoritative bounded Decimal math.
- `utils/pdf.py`: stored/form context and restricted WeasyPrint rendering.
- `utils/scheduler.py`: idempotency-aware job functions.
- `scheduler_worker.py`: the only supported long-running scheduler process.

Routes still own some orchestration and transaction logic. Stripe delivery
outbox work, database constraints, and full migration ownership are documented
in `CODE_REVIEW_STATE.md`.

## Local development

Create an environment and install both requirement sets:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
set -a
. ./.env
set +a
```

Set a unique development `SECRET_KEY`, then initialize and run:

```bash
AUTO_CREATE_DB=false .venv/bin/flask --app app db-upgrade
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

## Docker Compose

Compose requires a secret rather than silently starting with an empty value:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
docker compose up --build
```

The services are:

- `migrate`: runs `flask --app app db-upgrade` once.
- `web`: starts Gunicorn only after migration succeeds.
- `scheduler`: starts one blocking APScheduler only after migration succeeds.

The default public URL is `http://localhost:8080/pdfbillr`. Exactly one
`scheduler` replica is permitted. Do not start APScheduler inside Gunicorn
workers. Scaling web workers/replicas also requires shared rate-limit storage;
the default in-memory backend is only correct for one web process.

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
- `AUTO_CREATE_DB=false` in production. Run `db-upgrade` once before web and
  scheduler startup.
- `SCHEDULER_TIMEZONE=UTC` by default. Invoice business-date semantics are
  still UTC/host-date based; per-user timezone support is unresolved.
- `UPLOAD_FOLDER=/app/instance/uploads` stores normalized private logo files.
- `SESSION_COOKIE_SECURE=true` and `ENABLE_HSTS=true` only behind verified HTTPS.
- `RATELIMIT_STORAGE_URI=memory://` supports one process only. Use a supported
  shared backend such as Redis before scaling and include its client dependency.

Invalid Boolean environment values fail startup instead of silently changing
behavior.

## Database upgrades

Run:

```bash
flask --app app db-upgrade
```

The command creates a fresh schema and explicitly upgrades known legacy invoice
tracking/reminder columns plus the Stripe event-order column. It inspects before
altering, uses dialect-appropriate known types, and propagates unexpected DDL
errors. It is safe to rerun and must be invoked by one deployment process.

This command is a compatibility bridge, not a replacement for Alembic. Do not
run multiple upgrade commands concurrently. The next database milestone is an
Alembic baseline, verified legacy stamping/backfill, Numeric money columns, date
types, and selected constraints/indexes.

## Scheduler reliability

Run exactly one:

```bash
DISABLE_SCHEDULER=false python scheduler_worker.py
```

Web app construction never starts jobs. Recurring invoices are committed and
their schedule advanced before optional email, preventing a job rerun from
creating the same occurrence again in the supported single-scheduler topology.
Failed reminders keep their sent flags false. Recurring work rechecks Pro
entitlement. A durable occurrence/reminder outbox remains recommended for
multi-region operation and crash-perfect email delivery.

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

## Mail and partial failure

Welcome/reset/billing/invoice links are generated from `PUBLIC_BASE_URL`, never
from a request-supplied Host. Invoice status changes to `sent` only after
Flask-Mail returns successfully. Reminder flags change only after success.

Billing notification email is best effort after durable webhook state. A
failure does not roll back correct billing state and currently has no outbox
retry; this is recorded as remaining work.

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
- `GET /health`: PDF/database readiness; returns `503` when degraded.
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
run `db-upgrade`, then start web followed by exactly one scheduler. Test restores
regularly; a file copy of a live WAL database is not a backup procedure.

## Production checklist

- Use a unique secret, canonical HTTPS URL, trusted hosts, and correct proxy
  isolation.
- Run the schema upgrade once and back up before upgrading.
- Run one scheduler and one web process when using the memory limiter/SQLite.
- Persist and back up database/uploads.
- Configure/test Stripe signature secret, allowed price, and SMTP.
- Restrict and redact reverse-proxy/application logs; token paths are secrets.
- Monitor `/health`, SMTP failures, Stripe 5xx responses, scheduler exceptions,
  disk usage, and backup age.
- Keep debug off and do not expose Gunicorn directly when proxy trust is on.
- Review `CODE_REVIEW_STATE.md` for unresolved migration, outbox, timezone,
  paid-state, and public-token lifecycle work.
