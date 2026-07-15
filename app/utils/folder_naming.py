"""
utils/folder_naming.py — Canonical folder name generation.

Convention:
  {Artist} - {date} - {Venue} - {Location} ({Source})

Date formats (partial dates degrade gracefully):
  Full:       1977-05-08
  Month only: 1977-06
  Year only:  1977
  Multi-day:  1977-05-08 to 1977-05-09

Source formats:
  source only: SBD

Examples:
  Grateful Dead - 1977-05-08 - Barton Hall - Ithaca, NY (SBD)
  Bela Fleck - 1998-01-15 - Bill and Claire's Living Room - Hickory, NC (SBD)
  Unknown Artist - 1963 - Unknown Venue - Unknown Location
"""

import re


def _format_date(year, month, day):
    """Build a date string from nullable year/month/day integers."""
    if not year:
        return "Unknown Date"
    if month and day:
        return f"{year}-{month:02d}-{day:02d}"
    if month:
        return f"{year}-{month:02d}"
    return str(year)


def _format_date_range(start_year, start_month, start_day,
                       end_year, end_month, end_day):
    """
    Build a date or date-range string.
    If end date is set and differs from start, render as 'START to END'.
    """
    start = _format_date(start_year, start_month, start_day)
    if not end_year:
        return start
    end = _format_date(end_year, end_month, end_day)
    if end == start:
        return start
    return f"{start} to {end}"


def _format_source(source):
    """
    "Other" is a catch-all bucket, not a meaningful source label — it loses the
    context that it's the source field — so it is dropped from the name.
    """
    if not source or source == "Other":
        return None
    return source


def _format_location(city, state, country):
    """Build a location string from available fields."""
    if city and state:
        return f"{city}, {state}"
    if city and country:
        return f"{city}, {country}"
    if city:
        return city
    if state:
        return state
    if country:
        return country
    return "Unknown Location"


def _sanitize(name):
    """Remove characters illegal in macOS filenames."""
    # macOS only disallows : and / in filenames (and NUL)
    return re.sub(r'[:/\x00]', '-', name).strip()


def build_folder_name(
    artist_name,
    start_year=None, start_month=None, start_day=None,
    end_year=None,   end_month=None,   end_day=None,
    venue_name=None,
    city=None, state=None, country=None,
    source=None,
):
    """
    Generate the canonical folder name for a recording.

    Args:
        artist_name     : str  — performer display name
        start/end date  : nullable ints
        venue_name      : str | None
        city/state/country : str | None
        source          : str | None  — e.g. "SBD", "AUD"

    Returns:
        str — folder name safe for macOS filesystem
    """
    artist  = _sanitize(artist_name or "Unknown Artist")
    date    = _format_date_range(start_year, start_month, start_day,
                                 end_year,   end_month,   end_day)
    venue   = _sanitize(venue_name or "Unknown Venue")
    loc     = _sanitize(_format_location(city, state, country))
    src     = _format_source(source)

    # Base: Artist - Date - Venue - Location
    name = f"{artist} - {date} - {venue} - {loc}"

    # Append source in parens if known
    if src:
        name = f"{name} ({_sanitize(src)})"

    return name


def build_folder_name_from_recording(recording, performance, performer, venue):
    """
    Convenience wrapper — builds folder name directly from ORM objects.

    Args:
        recording   : Recording model instance
        performance : Performance model instance
        performer   : Performer model instance
        venue       : Venue model instance | None
    """
    # Resolve location — performance overrides event, venue is canonical
    if venue:
        city    = venue.city
        state   = venue.state
        country = venue.country
    else:
        city    = performance.city
        state   = performance.state
        country = performance.country

    return build_folder_name(
        artist_name     = performer.name,
        start_year      = performance.start_year,
        start_month     = performance.start_month,
        start_day       = performance.start_day,
        end_year        = performance.end_year,
        end_month       = performance.end_month,
        end_day         = performance.end_day,
        venue_name      = venue.name if venue else None,
        city            = city,
        state           = state,
        country         = country,
        source          = recording.source,
    )
