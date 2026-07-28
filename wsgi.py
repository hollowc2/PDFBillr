"""Gunicorn entry point.

Keeping application construction here makes importing ``create_app`` safe for
tests, scripts, and migration tooling.
"""

from app import create_app

app = create_app()
