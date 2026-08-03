"""
Analytics service — aggregates click event data for a short URL.

Reads from the click_events table and returns structured summaries.
All aggregation is done in Python to keep the queries simple and portable.
For high-volume deployments this layer should be moved to SQL GROUP BY queries
or a dedicated analytics store.
"""

from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from service.models.click_event import ClickEvent
from service.models.short_url import ShortURL


def get_analytics(
    db: Session,
    short_url: ShortURL,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict:
    """
    Aggregate click events for `short_url` into a dashboard-ready summary.

    Returns a dict matching the AnalyticsResponse schema shape.
    """
    query = db.query(ClickEvent).filter(ClickEvent.short_url_id == short_url.id)

    if from_date:
        query = query.filter(ClickEvent.clicked_at >= from_date)
    if to_date:
        query = query.filter(ClickEvent.clicked_at <= to_date)

    events: list[ClickEvent] = query.all()

    total_clicks = len(events)
    unique_clicks = len({e.ip_hash for e in events if e.ip_hash})

    # Clicks grouped by calendar date (YYYY-MM-DD)
    date_counter: Counter = Counter()
    for e in events:
        date_counter[e.clicked_at.strftime("%Y-%m-%d")] += 1

    # Clicks grouped by country code
    country_counter: Counter = Counter(
        e.country_code or "unknown" for e in events
    )

    # Clicks grouped by device type
    device_counter: Counter = Counter(
        e.device_type or "unknown" for e in events
    )

    # Top referrers — treat empty/null as "direct"
    referrer_counter: Counter = Counter(
        _normalise_referrer(e.referrer) for e in events
    )

    return {
        "short_url_id": str(short_url.id),
        "short_code": short_url.short_code,
        "total_clicks": total_clicks,
        "unique_clicks": unique_clicks,
        "clicks_by_date": [
            {"date": d, "count": c}
            for d, c in sorted(date_counter.items())
        ],
        "clicks_by_country": [
            {"country_code": cc, "count": c}
            for cc, c in country_counter.most_common()
        ],
        "clicks_by_device": [
            {"device_type": dt, "count": c}
            for dt, c in device_counter.most_common()
        ],
        "top_referrers": [
            {"referrer": r, "count": c}
            for r, c in referrer_counter.most_common(10)
        ],
    }


def record_click(
    db: Session,
    short_url: ShortURL,
    ip_hash: str | None,
    user_agent: str | None,
    device_type: str | None,
    referrer: str | None,
    country_code: str | None,
) -> ClickEvent:
    """Persist a single click event. Called from the redirect handler."""
    event = ClickEvent(
        short_url_id=short_url.id,
        ip_hash=ip_hash,
        user_agent=user_agent,
        device_type=device_type,
        referrer=referrer,
        country_code=country_code,
    )
    db.add(event)
    db.commit()
    return event


def _normalise_referrer(referrer: str | None) -> str:
    """Extract the domain from a referrer URL, or return 'direct'."""
    if not referrer:
        return "direct"
    try:
        from urllib.parse import urlparse
        host = urlparse(referrer).netloc
        return host if host else "direct"
    except Exception:
        return "direct"
