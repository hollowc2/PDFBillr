# Deployment guide

This guide describes the repository's Docker Compose deployment model. Keep
environment-specific settings and secrets outside Git.

## Before first deploy

1. Copy `.env.example` to a private `.env` file and provide a strong,
   unique `SECRET_KEY`.
2. Set `APP_ENV=production`, the canonical HTTPS `PUBLIC_BASE_URL`, and
   `TRUSTED_HOSTS` for the public deployment.
3. Configure `SESSION_COOKIE_SECURE=true` and `ENABLE_HSTS=true` only after
   HTTPS is confirmed end to end.
4. Provide production SMTP and Stripe settings only for enabled features.
5. Use a shared rate-limit store such as Redis when running more than one web
   process. `memory://` is intended only for one process.

Never commit `.env`, customer uploads, local database files, or reverse-proxy
configuration containing secrets.

## Deploy

```bash
git pull --ff-only
docker compose up --build -d
docker compose ps
```

Compose applies migrations with its one-shot `migrate` service before starting
the web and scheduler services. Run exactly one scheduler replica. Environment-
specific Compose overrides may be kept on the host and must be reviewed before
updating the stack.

## Verify

```bash
curl -fsS https://your-domain.example/pdfbillr/health/live
curl -fsS https://your-domain.example/pdfbillr/health
docker compose ps
```

The readiness response should report `status: "ok"`. Check both web and
scheduler logs after any runtime or configuration change:

```bash
docker compose logs --tail=100 web scheduler
```

## Backups and rollback

Back up the database and uploaded logos before migrations or infrastructure
changes. For a release rollback, return to the prior known-good Git revision,
review migration compatibility, and bring the stack up again. Do not roll back
database schema blindly; Alembic migrations require an explicit, tested
rollback plan.
