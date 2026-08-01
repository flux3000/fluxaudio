"""
utils/serialize.py — Shared model → dict serialisers for API responses.

Single source of truth for the recording "summary" shape used by the catalog
(artists), performance detail, and library views, so the fields never diverge
between endpoints.
"""


def recording_summary(rec):
    """
    Compact recording dict for list/catalog contexts (not the full detail view).
    """
    return {
        "id":              rec.id,
        "source":          rec.source,
        # The MANUAL A/B+ letter grade. Rates the show as a whole — listening
        # quality AND performance quality — and is a human judgement.
        "quality":         rec.quality,
        # The AUTOMATED 0-100 Listening Quality score, measuring the audio only.
        # Deliberately a separate field: it complements the letter grade and
        # replaces neither. None until the recording has been analysed.
        "listening_quality": (rec.quality_score.listening_quality
                              if rec.quality_score else None),
        "rating":          rec.rating,
        # One-click human highlight. Independent of both fields above — see the
        # comment on Recording.is_favorite.
        "is_favorite":     bool(rec.is_favorite),
        "is_complete":     rec.is_complete,
        "is_official":     rec.is_official,
        "track_count":     len(rec.tracks),
        # Total runtime in seconds (None-safe); powers the catalog length column.
        "duration_sec":    sum(t.duration or 0 for t in rec.tracks) or None,
        # When this recording was ingested (distinct from the show date) — powers
        # the sortable "Date Added" catalog column.
        "created_at":      rec.created_at.isoformat() if rec.created_at else None,
    }


def recording_row(rec):
    """
    Self-contained recording row for flat catalog/collection displays — includes
    the performer, date, and venue so a single row fully describes the show.
    """
    from app.utils.format import format_partial_date
    p = rec.performance
    v = p.venue if p else None
    return {
        "id":              rec.id,
        "performer":       p.performer.name if (p and p.performer) else None,
        "performer_id":    p.performer_id if p else None,
        "date":            format_partial_date(p.start_year, p.start_month, p.start_day) if p else None,
        "start_year":      p.start_year  if p else None,
        "start_month":     p.start_month if p else None,
        "start_day":       p.start_day   if p else None,
        "venue":           v.name    if v else None,
        "city":            v.city    if v else (p.city    if p else None),
        "state":           v.state   if v else (p.state   if p else None),
        "country":         v.country if v else (p.country if p else None),
        "source":          rec.source,
        # The MANUAL A/B+ letter grade. Rates the show as a whole — listening
        # quality AND performance quality — and is a human judgement.
        "quality":         rec.quality,
        # The AUTOMATED 0-100 Listening Quality score, measuring the audio only.
        # Deliberately a separate field: it complements the letter grade and
        # replaces neither. None until the recording has been analysed.
        "listening_quality": (rec.quality_score.listening_quality
                              if rec.quality_score else None),
        "rating":          rec.rating,
        # One-click human highlight. Independent of both fields above — see the
        # comment on Recording.is_favorite.
        "is_favorite":     bool(rec.is_favorite),
        "is_complete":     rec.is_complete,
        "track_count":     len(rec.tracks),
        "duration_sec":    sum(t.duration or 0 for t in rec.tracks) or None,
        # When this recording was ingested (distinct from the show date) — powers
        # the sortable "Date Added" catalog column.
        "created_at":      rec.created_at.isoformat() if rec.created_at else None,
    }
