import os

from config import Config

bind = "0.0.0.0:8000"
# A shared limiter is required before scaling beyond one worker. The app also
# rejects the in-memory backend in production so limits cannot silently become
# per-process.
workers = Config.WEB_CONCURRENCY
if workers > 1 and Config.RATELIMIT_STORAGE_URI.strip().lower().startswith(
    "memory://"
):
    raise RuntimeError(
        "WEB_CONCURRENCY > 1 requires a shared RATELIMIT_STORAGE_URI"
    )
threads = 8
worker_class = "gthread"
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
# Preserve useful request metadata without logging URL paths. Reset and public
# invoice bearer tokens are path components and must not enter access logs.
access_log_format = '%(h)s "%(m)s" %(s)s %(b)s %(L)s'
errorlog = "-"
loglevel = "info"
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
raw_env = ["SCRIPT_NAME=/pdfbillr"]
