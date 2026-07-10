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
        "source_modifier": rec.source_modifier,
        "quality":         rec.quality,
        "rating":          rec.rating,
        "is_complete":     rec.is_complete,
        "is_official":     rec.is_official,
        "track_count":     len(rec.tracks),
        # Total runtime in seconds (None-safe); powers the catalog length column.
        "duration_sec":    sum(t.duration or 0 for t in rec.tracks) or None,
    }
