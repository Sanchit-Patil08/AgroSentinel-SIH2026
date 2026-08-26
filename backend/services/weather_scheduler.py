"""
Weather background scheduler
-----------------------------
Makes AgroSentinel a *continuous* weather-monitoring app rather than one
that only fetches weather when a farmer happens to open a field:

    Field saved (has polygon/location)
           |
           v
    Background job wakes up every WEATHER_UPDATE_INTERVAL_MINUTES
           |
           v
    Fetches weather for every saved field's centroid
           |
           v
    New WeatherObservation rows stored (history builds up over time)

Kept in its own module (rather than inside weather_service.py, which owns
pure fetch/persist logic) so the "run this periodically, for every field"
scheduling concern stays separate and easy to find/adjust.

Uses APScheduler's BackgroundScheduler, which runs jobs on a thread inside
the same process -- no separate worker process/infra needed, appropriate
for this project's size. If/when this needs to scale beyond a single
process (e.g. multiple gunicorn workers), swap this for Celery beat or a
cron-triggered endpoint without changing weather_service.py at all.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _refresh_all_fields(app) -> None:
    """Runs inside the scheduler thread -- needs its own Flask app context
    to use the database (there's no active request here)."""
    from backend.models import Field
    from backend.services.weather_service import fetch_and_store_weather

    with app.app_context():
        fields = Field.query.all()
        logger.info("Weather refresh job: updating %d field(s)", len(fields))
        for field in fields:
            try:
                fetch_and_store_weather(field)
            except Exception:  # noqa: BLE001 - one field's failure shouldn't skip the rest
                logger.exception("Weather refresh failed for field_id=%s", field.id)


def start_weather_scheduler(app) -> None:
    """Starts the periodic weather-refresh job. Safe to call once per
    running process; a second call is a no-op."""
    global _scheduler
    if _scheduler is not None:
        return

    interval_minutes = app.config.get("WEATHER_UPDATE_INTERVAL_MINUTES", 90)

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _refresh_all_fields,
        trigger="interval",
        minutes=interval_minutes,
        args=[app],
        id="weather_refresh_job",
        max_instances=1,
        coalesce=True,
        # Newly-saved fields already get an immediate fetch (see
        # routes/fields.py), so the first periodic run can simply wait
        # a full interval rather than firing again right away.
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Weather background scheduler started (every %s minutes)", interval_minutes)