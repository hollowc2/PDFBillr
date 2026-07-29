# Contributing to PDFBillr

Thanks for taking an interest in the project. Small, focused changes with tests
and clear commit messages are easiest to review.

## Local setup

PDFBillr supports Python 3.12. Follow the setup steps in the [README](README.md),
then use the commands below:

```bash
make test
make lint
make format-check
```

`make up` starts the Docker stack after `SECRET_KEY` is set. `make db-bootstrap`
applies pending migrations using the currently loaded environment.

## Before opening a pull request

- Keep the change narrow and explain its user or operational value.
- Add or update tests for changed behavior.
- Run `make check`.
- Never commit `.env` files, secrets, uploaded customer files, or local database
  files.
- Include a migration for persistent-model changes and test both fresh and
  upgraded databases when relevant.

For security-sensitive findings, follow [SECURITY.md](SECURITY.md) rather than
opening a public issue.
