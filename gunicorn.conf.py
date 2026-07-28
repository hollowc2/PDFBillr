import os

bind = "0.0.0.0:8000"
# Single worker is safe with in-memory rate limiting.
# To scale: set workers > 1 AND provide RATELIMIT_STORAGE_URI=redis://...
workers = 1
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
