# PDFBillr Code Review State

Last updated: 2026-07-28 (America/Los_Angeles)

## Repository summary

PDFBillr is a compact Flask invoice-generation SaaS. It provides anonymous PDF
generation, authenticated invoice persistence, public token-based invoice
views, Pro branding/email/recurring-invoice features, Stripe subscription
handling, and APScheduler-driven reminders/recurrence. The application is
currently a single deployable web process with business logic concentrated in
Blueprint routes and `utils/` helpers.

This file is the durable handoff for the in-progress security, correctness, and
reliability review. Production code had not been changed when this initial
state was recorded.

## Current branch and starting commit

- Starting branch: `main`
- Starting commit: `477a80e` (`fix(healthcheck): replace curl with python3 urllib, correct path`)
- Current branch: `main`
- Current commit: `477a80e` (changes remain uncommitted as requested)
- Review diff: 50 repository files changed/added; pre-existing untracked
  `.claude/` excluded
- Upstream: `origin/main`
- Initial worktree state: clean tracked tree; pre-existing untracked `.claude/`
  and ignored `.playwright-mcp/`, Python bytecode, and local files under
  `static/logos/`

## Architecture map

### Application startup

- `app.py:create_app()` validates `Config`, conditionally applies `ProxyFix`,
  initializes SQLAlchemy/Login/Mail/Limiter/CSRF, configures Stripe's
  module-global API key, registers four Blueprints, registers the explicit
  database command, and installs response/cache/security headers.
- Factory imports have no database/thread side effects. Development/test may
  opt into `AUTO_CREATE_DB`; production leaves it off.
- `wsgi.py` constructs the Gunicorn app (`wsgi:app`).
- `scheduler_worker.py` is a separate blocking process and must run exactly
  once.
- `gunicorn.conf.py` uses one threaded worker and mounts the app under
  `/pdfbillr` through `SCRIPT_NAME`.

### Blueprint and route ownership

- `blueprints/public.py`: landing page, invoice form, PDF generation, saving a
  logged-in user's generated invoice, public invoice-token views, and health.
- `blueprints/auth.py`: registration, login/logout, forgot/reset password,
  user loader, and account emails.
- `blueprints/dashboard.py`: authenticated invoice list/detail/download/
  duplicate/delete/send, branding/logo uploads, draft save, and recurring
  invoice CRUD.
- `blueprints/billing.py`: upgrade page, Stripe Checkout, Stripe customer
  portal, signed webhook ingestion, subscription synchronization, and billing
  emails.

### Data model

- `User` owns invoices, one subscription, one branding profile, and recurring
  invoice templates.
- `Invoice` stores parties, line-item JSON, float monetary totals, status,
  public view token/count, and reminder flags.
- `Subscription` stores local Pro state and Stripe identifiers.
- `ProcessedStripeEvent` uses the Stripe event ID as its primary key.
- `RecurringInvoice` stores an invoice template and next/last run state.
- `BrandingProfile` stores an uploaded logo filename and PDF styling choices.

### Main dependency flows

```text
HTTP request -> Blueprint route -> ad hoc validation/helper -> SQLAlchemy model
             -> Jinja HTML / WeasyPrint PDF / Flask-Mail / Stripe response

Stripe webhook -> signature verification -> event-specific handler
               -> Stripe API (some events) -> SQLAlchemy commit -> email

In-process APScheduler -> reminder/recurrence helper -> database query
                       -> PDF/email side effect -> database commit
```

The PDF-generate/save route pair, recurring-template save path, webhook
handlers, and scheduler helpers combine validation, financial calculation,
side effects, and transaction ownership. These are the clearest candidates for
small cohesive services.

### Supporting components

- Invoice context and PDF rendering: `utils/pdf.py` plus four invoice templates.
- Gating: `utils/gating.py`, based on local subscription plan/status/period.
- Input helpers: `utils/helpers.py`.
- Client behavior: plain JavaScript under `static/js/`.
- Deployment: Python 3.12 slim image, Gunicorn, Docker Compose; SQLite and
  uploaded logos are bind-mounted.
- Database initialization/migration: explicit, inspect-before-alter
  `flask --app app db-upgrade`; there is still no Alembic/Flask-Migrate setup.
- Logging/error handling: standard Flask/Python logging, with several broad
  exception catches and no request-ID or structured logging facility.

## Runtime and test commands

- Local development: `python app.py` (binds `127.0.0.1:8000`)
- Production entry point: `gunicorn --config gunicorn.conf.py wsgi:app`
- Compose: `docker compose up --build`
- Syntax check: `python -m compileall .`
- Tests: no test suite or pytest dependency existed at the starting commit
- Migration command: `flask --app app db-upgrade`
- Scheduler command: `DISABLE_SCHEDULER=false python scheduler_worker.py`

## Baseline test results

| Command | Result |
| --- | --- |
| `python --version` | PASS; environment is Python 3.14.6 |
| `python -m pytest -q` | BLOCKED; `pytest` is not installed and no tests exist |
| `python -m compileall -q -x '(^|/)(\.git|\.venv|venv|instance)(/|$)' .` | PASS |
| `ruff --version` | BLOCKED; Ruff is not installed |
| `docker compose config --quiet` | PASS with warnings for unset secret/Stripe variables |
| `docker version --format '{{.Client.Version}} / {{.Server.Version}}'` | BLOCKED; Docker client 29.6.2 is present but daemon is unavailable |

Supported runtime from the Dockerfile is Python 3.12. The current shell uses
Python 3.14.6, so final checks must distinguish repository support from the
review environment.

## Confirmed findings

| ID | Severity | Confidence | Area | Finding | Impact | Recommended action | Effort | Regression test |
| -- | -------- | ---------- | ---- | ------- | ------ | ------------------ | ------ | --------------- |
| SEC-01 | Critical | High | Secrets/auth | Production can start with a public fallback `SECRET_KEY`; Compose turns an unset secret into an empty string, bypassing the exact-default warning. | A known key permits forged sessions/reset tokens; an empty key breaks sessions/CSRF while health can remain green. | Add explicit environment validation; reject empty/default/weak production secrets and require the Compose value. | S | Production missing/empty/default secret tests |
| SEC-02 | Critical | High | Proxy/URLs | `ProxyFix` unconditionally trusts one forwarded host/proto/prefix/address hop, with no trusted-host list or canonical outbound origin. | Direct clients can poison password-reset links and spoof limiter identity; valid reset tokens can be disclosed to an attacker-controlled host. | Disable forwarded-header trust by default, support an explicit trusted-proxy mode, validate hosts, and build email/Stripe URLs from `PUBLIC_BASE_URL`. | M | Host/XFH/XFF poisoning and canonical URL tests |
| JOB-01 | High | High | Scheduler | Every application instance starts APScheduler; logical reminder/recurring occurrences have no database claim/unique key. | Multiple workers/replicas or crash retries can create and email duplicate invoices/reminders. | Remove scheduler startup from web app creation; add one explicit scheduler command/process, then add occurrence idempotency. | M/L | Multiple app factories start no jobs; repeated logical-job tests |
| JOB-02 | High | High | Reminders | Mail failure is swallowed but the reminder flag is still committed as sent. | A transient SMTP failure permanently suppresses the reminder. | Mark sent only after successful delivery and leave failures retryable. | S | First mail fails/flag false; retry succeeds once |
| JOB-03 | High | High | Feature gating | Recurring processing does not recheck whether the owner is still Pro. | Canceled users continue receiving Pro-only generation and auto-email behavior. | Check current entitlement before generating or sending. | S | Canceled owner produces no invoice/mail |
| FIN-01 | High | High | Financial correctness | Float arithmetic is authoritative and duplicated. Operands can be finite while multiplication overflows; there is no explicit rounding boundary. | Half-cent lines can disagree with subtotal displays, and stored totals can become non-finite. | Centralize bounded Decimal parsing/calculation with explicit half-up quantization; preserve Float columns only as a compatibility stage. | M | Half-cent, fractional, malformed, huge, NaN/infinity tables |
| FIN-02 | High | High | Recurring invoices | Recurring line-item JSON is accepted from the browser with unvalidated shapes/values and client-supplied `amount`; the scheduler sums those amounts. | Tampered or stale JSON creates materially incorrect invoices. | Validate every item, cap count/length/value, ignore submitted amount, and use the shared calculator. | M | Tampered amount, wrong type, malformed JSON tests |
| FIN-03 | High | High | Recurring invoices | Scheduler caps discount only for its local total but persists the original uncapped template discount. | A PDF can show subtotal `$10`, discount `-$100`, and total `$0`. | Persist the normalized/capped discount from the shared calculation. | S | Excess-discount consistency test |
| DB-01 | High | High | Database | `create_app()` performs raw `ALTER TABLE` on every process startup, catches every error, commits each column separately, and uses PostgreSQL-incompatible `DATETIME`/Boolean defaults. | Concurrent starts or real DDL failures can silently leave a partial schema while ORM queries expect all columns. | Introduce Alembic/Flask-Migrate and an explicit legacy transition; web startup must not mutate schema. | L | Fresh/legacy/rerun schema verification |
| STR-01 | High | High | Stripe entitlement | Webhook subscription updates never require `stripe_price_id == STRIPE_PRICE_ID_PRO`; local Pro gating ignores price. | Any other subscription price on the same Stripe customer/account can grant Pro. | Validate customer/subscription binding and allowlisted price/status before entitlement. | M | Configured/unknown/missing-price tests |
| STR-02 | High | High | Stripe ordering | Event handlers overwrite local state without comparing event creation time or state version. | A delayed older active event can reactivate a canceled subscription. | Persist last processed event time per subscription and reject stale transitions. | M | Older active-after-delete remains canceled |
| STR-03 | High | High | Stripe idempotency | Webhook uses check-then-add, handlers commit independently, and emails occur before a durable transaction; ignored early returns are not consistently recorded. | Concurrent duplicates or commit retries can repeat notifications/state changes, while acknowledged ignored events have no audit record. | Centralize a transaction, make claiming/outcome durable, rollback on failure, and separate post-commit notification. | M/L | Duplicate/concurrent/unknown-user/commit-failure tests |
| STR-04 | High | High | Stripe checkout | Checkout does not reuse an existing Stripe customer or guard active/pending subscription checkout. | Repeat checkout can create multiple customers/subscriptions and ongoing duplicate charges with only one mapped locally. | Reuse bound customer, guard duplicate active checkout, and reject conflicting completion events. | M | Repeat checkout customer/binding tests |
| AUTH-01 | High | High | Sessions | User loader restores inactive users; inactivity is checked only during a fresh login. | A disabled account keeps authenticated/remembered access. | Reject inactive users in the loader; document stronger session-version revocation as follow-up. | S | Disable after login and deny existing session |
| AUTH-02 | High | High | Cookies | Registration always requests a remember cookie, but only the session cookie has explicit Secure/HttpOnly/SameSite settings. | A long-lived authentication token can be sent less safely than the session cookie. | Align Flask-Login remember-cookie flags and add HSTS only for verified HTTPS production. | S | Inspect both cookie types |
| AUTH-03 | Medium | High | Redirects | Login `next` validation rejects `netloc` but not an explicit scheme such as `https:evil.example`. | Post-login redirect can be used for phishing. | Accept only a local absolute path beginning with exactly one `/`. | S | URL-variant table |
| AUTH-04 | High | High | Reset/session revocation | Reset tokens encode only email/time and remain reusable; password changes do not revoke existing sessions. | A recovered token can reset repeatedly for an hour and a compromised existing session remains usable. | Add per-user auth/reset versioning in a migration after session semantics are decided. | M | Token reuse/old-session tests |
| UP-01 | Medium | High | Uploads | Logos are accepted by filename extension and stored raw in the public static tree; old files are deleted before the replacement is safely written. | Malformed/decompression-bomb images can consume PDF resources; a failed replacement can lose the previous logo. | Decode, constrain, re-encode to a known image format, atomically replace, and move upload storage outside source/static. | M | Fake/malformed/huge/traversal/replacement-failure tests |
| PDF-01 | Medium | High | PDF defense | Current templates expose no user-controlled fetch URL, but WeasyPrint retains its unrestricted default URL fetcher. | A future template URL field can silently introduce HTTP/file SSRF. | Use a deny-by-default fetcher permitting only required in-memory data resources. | S | Deny `http:`, `https:`, and `file:` fetch tests |
| OPS-01 | High | High | Import side effects | Importing `app` constructs the production app, creates/alters a database, and starts scheduler threads before tests can supply config. | Tests/importers can mutate local state or start customer-facing jobs. | Separate factory import from a small WSGI entry point and explicit scheduler entry point. | S/M | Import creates no file/thread |
| OPS-02 | High | High | Container privacy | `.dockerignore` does not exclude ignored uploaded/local `static/logos/*`; `COPY . .` bakes them into immutable image layers. | Customer files can enter registries and remain recoverable after deletion. | Exclude upload contents from build context and use a dedicated persistent upload path. | S/M | Docker context/image assertion |
| OPS-03 | High | High | Health | Readiness calculates `degraded` but always returns HTTP 200; a failed DB session is not rolled back. | Orchestrators keep routing traffic to a dependency-broken process. | Split liveness/readiness or return 503 for degraded dependency readiness and rollback. | S | Dependency-failure response tests |
| OPS-04 | High | High | Deployment config | Compose omits documented DB, mail, limiter, cookie, and scheduler variables; `.env.example` recommends PostgreSQL without a DB driver. | Documented deployments ignore operator settings or fail at database startup. | Complete the Compose/config contract and either add/test psycopg or stop claiming PostgreSQL support. | S/M | Config-contract and optional Postgres smoke |
| LOG-01 | High | High | Secrets/logging | Gunicorn’s default request-line access log includes reset and public-invoice bearer tokens in URL paths. | Anyone with log access can reuse those credentials. | Redact sensitive paths at Gunicorn/proxy layers and document log retention/access; consider later token-exchange design. | M | Logger output contains no supplied token |
| TX-01 | Medium | High | Invoice lifecycle | Authenticated generation commits before PDF render; email is sent before status commit. | A render failure leaves an unexpected invoice; DB failure after mail invites duplicate send. | Tighten transaction boundaries and introduce delivery idempotency/outbox in stages. | M/L | Inject render/commit failures |
| DATE-01 | Medium | High | Recurrence/time | Jobs use host-local dates; overdue recurrence advances from `today`, skips periods, and `net_days=0` creates no due date. | Billing schedules drift across downtime/timezones and “due today” invoices lack due dates. | Define timezone/catch-up policy; advance from recorded occurrence; treat zero as today. | M | Downtime, UTC boundary, zero-net-days tests |
| WEB-01 | Medium | High | Browser/CSP | Inline delete-confirmation handlers are blocked by the current CSP. | Destructive forms submit without the confirmation the UI promises. | Move confirmation behavior to same-origin JavaScript. | S | Browser confirmation cancel/submit test |
| WEB-02 | Medium | High | Client rendering | Recurring form builds stored values with `innerHTML`; numeric values are not escaped and server JSON shape is unvalidated. | Malformed stored JSON can inject markup or break the editor UI. | Build DOM nodes with `.value`/`.textContent` and validate server-side. | S/M | Hostile stored-value UI test |
| DOC-01 | Medium | High | Product/privacy docs | Landing copy says invoice data never reaches a database and there are no accounts/subscriptions, while authenticated generation persists invoices and Stripe accounts exist. | Users receive materially inaccurate privacy/product claims. | Obtain a product/legal copy decision and qualify the statements; do not change storage behavior silently. | S | Copy review after decision |

## Suspected findings requiring verification or product decisions

- Public view counts include scanners, previews, owner refreshes, and repeated
  requests. Deduplication/bot semantics are a product decision.
- Recurrence catch-up versus skip behavior after downtime is not defined.
- The application has no paid/closed/reminder-disabled invoice state, so a sent
  invoice remains reminder-eligible indefinitely. A paid-state workflow is a
  product decision.
- Foreign-key cascades and account deletion semantics are undefined because
  there is no account-delete workflow.
- Public invoice tokens are strong (256-bit generation) and owner-rotatable/
  revocable, but automatic expiry semantics need a product decision.
- PostgreSQL has a driver but remains untested; a CI service is needed
  before support can be claimed confidently.

## Prioritized work queue

1. Inventory malformed legacy money/date rows from representative backups and
   design reversible shadow-column migrations.
2. Add PostgreSQL and Redis service integration/concurrency tests, including
   Alembic upgrade and concurrent delivery/invoice-number claims.
3. Obtain product decisions for paid/closed/reminder opt-out and recurrence/
   reminder catch-up behavior.
4. Add manual invoice-send idempotency semantics and an outbox only after the
   resend UX/idempotency-key behavior is defined.
5. Decide public-token automatic expiry and view-count bot/dedup semantics.
6. Visually regression-test WeasyPrint 68+ and remove audit exceptions when
   safe.
7. Build and runtime-smoke the non-root image on a host with Docker, including
   existing bind-mount ownership and persistence.
8. Add metrics/tracing/mail-provider delivery callbacks where production
   observability requirements justify them.
9. Adopt repository-wide Ruff formatting as a separate cosmetic-only change if
   desired.

## Implementation batches

### Batch 1 — Safety net, import/configuration trust

- Purpose: make the factory safe to import, create an isolated test harness, and
  close the known-secret/forwarded-host/canonical-URL failures before testing
  deeper integrations.
- Expected files: `app.py`, `config.py`, new `wsgi.py`, new `utils/urls.py`,
  `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`,
  test/tool configuration, and focused auth/config/header tests.
- Behavioral risk: production deployments behind a proxy must explicitly opt
  into trusted forwarded headers and configure their canonical public URL;
  Gunicorn entry point changes from `app:app` to `wsgi:app`.
- Required tests: factory import side effects, production secret validation,
  canonical URL/host poisoning, scheduler-disabled startup, cookie flags,
  registration/login/logout, inactive-user rejection, and two-user ownership.

### Batch 2 — Authoritative financial calculation

- Purpose: make one Decimal-based server calculation authoritative for normal
  and recurring invoices, and stop trusting recurring browser amounts.
- Expected files: new calculation service, `utils/pdf.py`,
  `blueprints/dashboard.py`, `utils/scheduler.py`, small client-preview
  consistency update, and table-driven calculation/recurrence tests.
- Behavioral risk: half-cent rounding becomes explicit (round each line to
  cents using `ROUND_HALF_UP`, sum rounded lines, round tax to cents, then apply
  a cent-rounded fixed discount capped at subtotal plus tax). Extreme and
  non-finite values will be rejected or normalized instead of overflowing.
- Required tests: fractional inputs, half-cent boundaries, malformed/negative/
  non-finite/large values, empty invoices, tax/discount combinations, and
  tampered recurring amounts.

### Batch 3 — Scheduler and Stripe reliability

- Purpose: move jobs to one explicit process, keep failures retryable, enforce
  entitlement on scheduled work, and close highest-confidence Stripe price/
  transaction/idempotency defects.
- Expected files: `app.py`, new scheduler entry point/command,
  `utils/scheduler.py`, `blueprints/billing.py`, models only if a safe migration
  is included, Compose/docs, and mocked job/webhook tests.
- Behavioral risk: operators must run exactly one scheduler service; unknown or
  wrong-price Stripe events will be recorded/acknowledged without granting Pro.
- Required tests: repeat job, mail failure/retry, downgraded owner, canonical
  scheduled links, invalid signature, duplicate/unknown/wrong-price events, and
  forced database failure.

### Batch 4 — Targeted upload/PDF/deployment hardening

- Purpose: safely decode/re-encode logos, deny unexpected PDF resource fetching,
  harden health/container behavior, and document remaining migration/deployment
  work.
- Expected files: upload/PDF helpers, dashboard route, health route, Docker/
  Compose, README/environment documentation, and focused tests.
- Behavioral risk: previously accepted malformed/animated files may be rejected;
  readiness will return HTTP 503 when required dependencies fail.
- Required tests: valid/fake/malformed/oversized images, traversal names,
  replacement failure, hostile PDF content/fetch URLs, and health status.

## Completed changes

- Created this durable review state file.
- Completed initial tree, git, runtime, and baseline-tooling inventory.
- Completed read-only architecture/security/correctness/operations review of
  Python, templates, JavaScript, CSS asset, Docker/Compose, environment example,
  dependencies, and recent history.
- Confirmed that all current authenticated invoice-ID and recurring-template-ID
  routes consistently perform ownership checks; no direct cross-user IDOR was
  found.
- Batch 1:
  - `app.py` now exposes only a side-effect-free factory; `wsgi.py` owns
    Gunicorn application construction.
  - Production rejects missing/empty/default/short secrets, missing canonical
    public URL, non-HTTPS canonical URL, and missing trusted-host configuration.
  - `ProxyFix` is disabled unless explicitly enabled for a protected proxy
    topology; Flask host validation can be configured with `TRUSTED_HOSTS`.
  - Outbound reset, Stripe, and invoice-view links use `PUBLIC_BASE_URL`.
  - Inactive users are rejected by the user loader; login/registration clear
    the pre-authentication session; login redirects accept only local absolute
    paths.
  - Remember-cookie security is aligned with session-cookie settings.
  - Compose now requires a secret and passes the documented database, mail,
    limiter, cookie, proxy, canonical-URL, and scheduler settings.
  - Docker build context excludes local browser logs and uploaded/local logo
    contents; Gunicorn now loads `wsgi:app`.
  - Added compatible CSP `form-action`/`frame-ancestors` and
    `Permissions-Policy` headers.
  - Added pytest/coverage/Ruff development dependencies and focused Ruff
    correctness rules without reformatting the legacy codebase.
- Batch 2:
  - Added `utils/invoice_calculations.py` as the authoritative bounded Decimal
    calculation service. It rounds each line and tax to cents using
    `ROUND_HALF_UP`, rounds/caps fixed discounts, rejects negative/non-finite/
    malformed/extreme numeric input, and only converts to float at the existing
    database/template compatibility boundary.
  - Normal PDF generation/draft saving and recurring-template generation now
    use the same service. Recurring browser-supplied `amount` values are ignored
    and recomputed from quantity/rate.
  - Recurring stored discounts are normalized/capped, `net_days=0` now means
    due today, net days are bounded to the UI's 0–365 contract, and recurring
    themes are allowlisted.
  - Browser preview now mirrors line/tax cent rounding and discount capping;
    server calculation remains authoritative.
  - Invoice form validation errors are now visibly rendered.
- Batch 3:
  - Removed all scheduler startup from web application construction and added
    `scheduler_worker.py` with one blocking, UTC-configured, coalescing,
    `max_instances=1` scheduler. Compose runs exactly one scheduler service
    after a one-shot migration service.
  - Recurring work rechecks Pro entitlement, commits each occurrence/schedule
    advance before optional external email, rolls back per-template failures,
    and produces canonical public links. Repeating a completed logical run does
    not create another invoice.
  - Reminder delivery returns explicit success; SMTP failure leaves the sent
    flag false for retry.
  - Replaced broad silent startup DDL with inspect-before-alter,
    dialect-aware, rerunnable `_upgrade_legacy_schema()` behind the explicit
    `flask --app app db-upgrade` command. Development/test auto-create remains
    configurable; production defaults it off. Added the legacy invoice-token
    index and Stripe event-order column upgrade.
  - Stripe webhooks now commit domain effects and processed-event markers in
    one transaction, handle concurrent event-ID conflicts, record unknown
    events, send billing notification only post-commit, enforce configured
    price/customer/subscription binding, and reject stale event ordering.
  - Checkout reuses an existing Stripe customer and refuses already-active Pro
    checkout. Pro gating now requires plan, valid status/period, and the
    configured price.
- Batch 4:
  - Added Pillow-backed logo validation, pixel/dimension/animation checks,
    server-side PNG re-encoding, generated names, atomic write-before-replace,
    owner-only serving, and private `instance/uploads` storage with legacy
    read compatibility.
  - Restricted WeasyPrint fetching to in-memory image data; HTTP, HTTPS, file,
    and non-image data resources are denied.
  - Added process liveness and dependency readiness; degraded readiness returns
    503 and rolls back failed DB sessions. Health HTML now reports the database.
  - Gunicorn access logs retain method/status/size/latency but omit token-bearing
    URL paths.
  - Removed CSP-blocked inline delete handlers, moved confirmation behavior to
    same-origin JavaScript, and removed recurring editor `innerHTML` interpolation.
  - Added server-side email normalization/header-injection rejection and
    targeted limits on expensive PDF/email/public-view/portal endpoints.
  - Added no-store/private caching for authenticated, auth, billing, and public
    bearer-token pages.
  - Added README operations/security/migration/scheduler/backup documentation,
    environment/Compose updates, and a Python 3.12 CI workflow.

## Tests added

- Application import/configuration validation, host/proxy behavior, security
  headers, health startup, registration/password hashing, login/logout,
  unauthenticated redirect, inactive-user session rejection, remember-cookie
  flags, login redirect validation, and canonical reset-email URLs.
- Negative two-user authorization tests cover invoice detail, download,
  duplicate, delete, and send plus recurring edit, toggle, and delete.
- Current suite after Batch 1: 25 passed; two upstream Flask-Login
  `datetime.utcnow()` deprecation warnings.
- Batch 2 added 21 table/behavior tests for multiple/fractional/zero/empty line
  items, half-cent rounding, tax/discount, malformed/negative/non-finite/large
  values, submitted-total tampering, authenticated persistence, failed-input
  non-persistence, recurring normalization, generation, and zero-day due dates.
- Current suite after Batch 2: 46 passed; the same two upstream warnings.
- Batch 3/4 tests cover dedicated scheduler configuration, repeated recurrence,
  reminder fail/retry, canceled-owner gating, canonical background links,
  fresh/legacy/rerun database upgrades, Stripe signature/duplicate/unknown/
  wrong-price/stale/checkout/commit-failure behavior, logo validation/storage/
  ownership/size/replacement, restricted PDF fetching, hostile PDF fields,
  public-token access/tracking, readiness/liveness, CSRF, email header
  injection, successful mocked email, and owner deletion.
- Final suite: 78 passed, with two Flask-Login `datetime.utcnow()` and one
  pydyf API deprecation warning from pinned upstream dependencies.

## Commands run and results

See **Baseline test results**. Also inspected `git status`, recent history,
tracked files, ignored files, the complete Python/configuration surface, and
the templates/static assets in progress. `static/css/tailwind.min.css` is a
28,738-byte minified single-line file (so `wc -l` reports zero); it is present
and tracked.

Batch 1:

- `python -m compileall -q -x '(^|/)(\.git|\.venv|venv|instance)(/|$)' .`
  — PASS.
- `SECRET_KEY=compose-validation-secret-value-000000 docker compose config --quiet`
  — PASS.
- Initial `uv run --python 3.12 ... pytest -q` — BLOCKED in the sandbox by
  network/DNS; rerun with approved dependency download installed CPython
  3.12.13 and pinned dependencies into the isolated environment.
- First test run — 14 passed, 11 failed due to test fixtures returning expired
  detached ORM instances; fixtures were corrected to return stable identifiers.
- `uv run --python 3.12 ... python -m pytest -q` — PASS, 25 passed, two upstream
  deprecation warnings.
- `uv run --python 3.12 ... ruff check .` — initial legacy/import findings were
  scoped/fixed without formatting churn; final PASS.

Batch 2:

- Focused `tests/test_calculations.py` — initial 20 passed/one failed because
  the existing invoice form did not render flashed errors; form error rendering
  was fixed, then 21 passed.
- Full `python -m pytest -q` — PASS, 46 passed, two upstream warnings.
- `ruff check .` — one unused test import found and removed; rerun pending with
  the next full validation.
- `git diff --check` — PASS.

Batch 3/4:

- Focused scheduler/database/calculation tests — PASS, 25 passed.
- Focused Stripe/scheduler/database tests — PASS, 10 passed.
- Focused upload/PDF tests — PASS, 8 passed with one pydyf deprecation warning.
- Full `python -m pytest -q` — PASS at intermediate checkpoints: 66 passed,
  then 73 passed after public-token/email/config additions.
- `ruff check .` — PASS.
- `python -m compileall ...` — PASS.
- `SECRET_KEY=... docker compose config --quiet` — PASS.
- `git diff --check` — PASS.

Final verification:

- `uv run --offline --python 3.12 ... python -m pytest -q` — PASS,
  78 passed, three upstream deprecation warnings.
- `uv run --offline --python 3.12 ... python -m pytest --cov=. ... -q` —
  PASS at the 76-test checkpoint; 82% total Python statement coverage. The two
  final reset/config tests were then included in the 78-test non-coverage run.
- `uv run --offline --python 3.12 ... ruff check .` — PASS.
- `uv run --offline --python 3.12 ... ruff format --check .` — FAILS:
  19 legacy/mixed files would be reformatted. Formatting was not applied
  because it would create broad cosmetic churn; CI intentionally enforces the
  scoped correctness lint and tests, not repository-wide formatting yet.
- `python -m compileall -q -x ... .` — PASS.
- `SECRET_KEY=... docker compose config --quiet` — PASS.
- Fresh `/tmp` `flask --app app db-upgrade` — PASS; immediate rerun — PASS.
- `flask --app app routes` under the smoke configuration — PASS; 31 routes,
  including liveness/readiness and owner-only logo serving.
- `gunicorn --check-config --config gunicorn.conf.py wsgi:app` — PASS.
- `docker compose build` — BLOCKED after both sandboxed and approved retries:
  Docker daemon socket is absent and Compose also warned that buildx is not
  installed. No image-build success is claimed.
- `git diff --check` — PASS.
- Secret scan found only documented example/test prefixes and the README secret
  generation command; no private key, live Stripe key, `.env`, database, PDF,
  upload, or coverage artifact is included.

## Continuation update: migration, durability, and operations pass

This pass began from commit `a8da113` on `main` after the first review was
pushed. It used three parallel workstreams followed by root-agent integration
and review.

Completed:

- Added Flask-Migrate/Alembic with four linear revisions:
  - `20260728_01`: verified pre-Alembic baseline;
  - `20260728_02`: session versioning and per-user invoice-number uniqueness;
  - `20260728_03`: recurring occurrence and invoice delivery ledgers;
  - `20260728_04`: Stripe billing-notification delivery outbox.
- Replaced production schema mutation with `flask --app app db-bootstrap`.
  Fresh, already-versioned, and verified unversioned legacy databases are
  handled explicitly. Unknown/partial schemas and duplicate invoice numbers
  fail closed. The old command is a visible deprecated alias.
- Enabled SQLite foreign-key enforcement for application connections. Alembic
  suspends checks only on its dedicated SQLite batch-migration connection and
  runs `PRAGMA foreign_key_check` before restoring enforcement. A populated
  legacy-upgrade regression test caught and verifies this behavior.
- Added durable recurring occurrence claims and retryable reminder/recurring
  invoice email rows. Atomic leases prevent simultaneous claims; failed rows
  retry every five minutes. Recurring dates advance from the recorded scheduled
  occurrence rather than host `today`.
- Added a Stripe billing-notification outbox in the same transaction as the
  processed event. Duplicate webhook delivery and the scheduler retry failed
  notifications without duplicating successful sends.
- Added `auth_session_version`; password changes/reset revoke all prior sessions
  and remember cookies. Legacy pre-deployment sessions are intentionally logged
  out once.
- Added atomic public view counting plus owner-only public-link rotation and
  revocation. Old/revoked tokens immediately stop working.
- Added per-user invoice-number uniqueness, friendly user-entered duplicate
  rejection, concurrent-race rollback, and collision-safe automated duplicate
  and recurring numbering.
- PDF render now succeeds before an authenticated invoice is persisted, so a
  render failure cannot leave an unexpected invoice.
- Hardened runtime operations: multi-stage non-root image, configurable UID/GID,
  Redis/shared-limiter production enforcement, request IDs, privacy-safe request
  logging, generic correlated error responses, and Redis readiness checks.
- Corrected the landing-page claim: anonymous invoices are not account-saved or
  view-tracked, while account features can persist/email/track invoices.
- Added hash-verified Python 3.12 runtime/development locks, CI audit enforcement,
  Redis and Psycopg drivers, and safe Flask/Jinja security updates.
- Dependency audit now passes with only two explicitly documented WeasyPrint
  advisories ignored because the vulnerable paths are disabled by PDFBillr's
  data-image-only fetcher and `presentational_hints` is never enabled.

Continuation verification:

- Full locked Python 3.12 suite: `102 passed`, four upstream deprecation
  warnings.
- Focused scheduler/billing/migration suite: `35 passed`.
- Fresh Alembic `db-bootstrap` through revision `20260728_04`: PASS.
- Legacy upgrade with populated foreign-key child rows: PASS.
- `ruff check .`: PASS.
- `python -m compileall -q .`: PASS.
- `pip check`: PASS, no broken requirements.
- `pip-audit ... --ignore-vuln PYSEC-2026-2034 --ignore-vuln
  PYSEC-2026-3412 -r requirements.lock`: PASS, no non-ignored vulnerabilities.
- `docker compose config --quiet`: PASS.
- `git diff --check`: PASS.
- `ruff format --check .`: FAILS because 27 mixed legacy/changed files would be
  reformatted. Broad formatting churn remains intentionally deferred.
- `docker build -t pdfbillr-review:local .`: BLOCKED after an approved retry
  because `/var/run/docker.sock` does not exist. No image-build success is
  claimed.

## Unresolved risks

- Existing money/date columns remain `Float` and strings. Decimal is
  authoritative in code, but a production-safe conversion needs a legacy-row
  inventory, shadow `Numeric`/`Date` columns, reversible validated backfill,
  dual-read compatibility, and a later constraint cutover.
- PostgreSQL and Redis clients/configuration are present, but PostgreSQL
  integration, Redis integration, multi-worker concurrency, and migration
  tests have not run against real services.
- Delivery queues are at-least-once. A process crash after SMTP accepts a
  message but before the database records success can still produce a duplicate;
  SMTP offers no portable exactly-once transaction.
- Reminder catch-up, recurrence catch-up after long downtime, per-user/business
  timezone, paid/closed state, and reminder opt-out remain undefined product
  semantics. The current scheduler produces at most one overdue recurrence per
  run and uses the configured deployment timezone.
- Public tokens can be rotated/revoked but do not expire automatically. View
  counts intentionally still include reloads, link scanners, and mail-security
  bots because deduplication semantics require a product/analytics decision.
- Manual invoice email is synchronous. A database failure after SMTP acceptance
  can require an operator/user decision about resend; unlike automated
  notifications, manual resend semantics need an idempotency-key UX.
- The non-root Docker image could not be built or runtime-smoked because the
  host Docker daemon is absent. Existing deployment bind mounts require the
  documented one-time ownership check before rollout.
- The Python base image is tag-pinned rather than digest-pinned. WeasyPrint
  remains at 60.2 pending visual regression work for the major upgrade to 68+.
- Repository-wide Ruff formatting is not adopted; the check currently reports
  27 files. Formatting remains cosmetic and is deliberately separate.
- Request IDs/logging are correlation-safe but not a full structured telemetry
  stack; no metrics/tracing or mail-provider delivery callbacks exist.

## Behavioral decisions

- Pricing, plan names, feature limits, invoice URL structure, and public-token
  workflow will remain unchanged unless a demonstrated security defect requires
  a compatible hardening change.
- The first money pass will use Decimal internally and explicitly quantize
  results while retaining existing float columns; a Float-to-Numeric schema
  conversion will not be attempted without a migration and compatibility plan.
- Tests must never deliver real mail, call live Stripe, or make external network
  requests.
- Existing untracked/ignored `.claude/`, `.playwright-mcp/`, local logo assets,
  and bytecode are user/environment files and will not be changed.
- Rounding policy is line amount to cents with `ROUND_HALF_UP`, sum rounded
  lines, tax that subtotal and round to cents, then apply a cent-rounded fixed
  discount capped at subtotal plus tax.
- `net_days=0` means due on the generation date.
- Production web/scheduler processes never create or alter schema; one explicit
  deployment migration process owns the compatibility upgrade.
- One scheduler process is the supported topology. Web factories never start
  jobs.
- Anonymous PDF generation is not persisted; authenticated generation remains
  persisted as before.
- Scheduler and billing delivery queues use at-least-once semantics with
  five-minute retry leases. Exactly-once SMTP delivery is not claimed.
- Public invoice-token expiry was not guessed; owners can rotate or revoke links
  without changing existing URL behavior.
- Development auto-create uses Alembic; only ephemeral test databases use
  `db.create_all()`.

## Recommended next task

Build the staged legacy money/date migration: add inventory/preflight tooling
and shadow `Numeric`/`Date` columns, test reversible backfill on representative
production backups, and add PostgreSQL integration/concurrency CI before any
read-path cutover. This is the highest remaining correctness risk and should
not be attempted as a direct in-place type conversion.

## Files intentionally not changed

- `.claude/` (pre-existing untracked local configuration)
- `.playwright-mcp/` (ignored local logs)
- `static/logos/*` except `.gitkeep` (ignored local/sample/user data)
- Existing invoice PDF theme layouts and product pricing/feature limits
- Existing Float/string money/date columns pending an Alembic migration
- Any local database, upload, virtual environment, cache, or bytecode file
