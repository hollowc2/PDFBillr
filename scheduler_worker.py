"""Dedicated single-process scheduler entry point."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.blocking import BlockingScheduler

from app import create_app
from utils.scheduler import process_recurring_invoices, send_payment_reminders

log = logging.getLogger(__name__)


def create_scheduler(app):
    timezone_name = app.config.get("SCHEDULER_TIMEZONE", "UTC")
    try:
        scheduler_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Unknown SCHEDULER_TIMEZONE: {timezone_name}") from exc

    scheduler = BlockingScheduler(
        timezone=scheduler_timezone,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 3600,
        },
    )
    scheduler.add_job(
        send_payment_reminders,
        "cron",
        id="payment-reminders",
        replace_existing=True,
        hour=8,
        minute=0,
        args=[app],
    )
    scheduler.add_job(
        process_recurring_invoices,
        "cron",
        id="recurring-invoices",
        replace_existing=True,
        hour=8,
        minute=5,
        args=[app],
    )
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    if app.config.get("DISABLE_SCHEDULER"):
        raise RuntimeError("Scheduler is disabled by DISABLE_SCHEDULER.")
    log.info(
        "Starting PDFBillr scheduler in timezone %s",
        app.config.get("SCHEDULER_TIMEZONE", "UTC"),
    )
    create_scheduler(app).start()


if __name__ == "__main__":
    main()
