FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.lock .
RUN pip install \
    --require-hashes \
    --prefix=/install \
    -r requirements.lock

FROM python:3.12-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libgdk-pixbuf-xlib-2.0-0 \
        shared-mime-info \
        fonts-dejavu \
        fonts-liberation \
        fonts-noto \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${APP_GID}" pdfbillr \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --no-create-home \
        --shell /usr/sbin/nologin \
        pdfbillr

WORKDIR /app
# Sources and downloaded wheels are hash-verified in the disposable builder.
# Copy the resulting installation instead of hashing locally built wheels,
# whose archive hashes are not reproducible from an sdist.
COPY --from=builder /install /usr/local

COPY --chown=pdfbillr:pdfbillr . .
RUN mkdir -p /app/instance/uploads /app/static/logos \
    && chown -R pdfbillr:pdfbillr /app/instance /app/static/logos

USER pdfbillr

EXPOSE 8000
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
