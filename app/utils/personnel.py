"""
utils/personnel.py — resolve who played a given Performance.

Single source of truth for inherit vs. explicit personnel resolution (see
Context Library/Per-Show Personnel — Design Plan §3). Every endpoint/view
that needs a show's lineup must go through resolve_performance_personnel()
rather than querying Membership or PerformancePersonnel directly, so display
and any future authorization/aggregation logic can never disagree.
"""

import calendar

from sqlalchemy import func
from app.extensions import db
from app.models.performance_personnel import PerformancePersonnel
from app.utils.performers import resolve_or_create_artist


def _floor_date(y, m, d):
    """Earliest possible day for a partial date. None if year itself unknown."""
    if y is None:
        return None
    return (y, m or 1, d or 1)


def _ceil_date(y, m, d):
    """Latest possible day for a partial date. None if year itself unknown."""
    if y is None:
        return None
    m = m or 12
    d = d or calendar.monthrange(y, m)[1]
    return (y, m, d)


def _stint_covers(membership, performance):
    """
    True if `membership`'s stint bounds cover `performance`'s date.

    Bounds are inclusive and forgiving of missing month/day (see Membership's
    docstring): a NULL start means "always started"; a NULL end means "never
    ended." A performance with no year at all can't be dated against a stint
    at all, so it's treated as covered — better to over-include a roster
    member than silently drop one because the show's date is unknown.
    """
    perf_floor = _floor_date(performance.start_year, performance.start_month,
                              performance.start_day)
    perf_ceil = _ceil_date(performance.start_year, performance.start_month,
                            performance.start_day)
    if perf_floor is None:
        return True

    start_floor = _floor_date(membership.start_year, membership.start_month,
                               membership.start_day)
    end_ceil = _ceil_date(membership.end_year, membership.end_month,
                           membership.end_day)

    if start_floor is not None and perf_ceil < start_floor:
        return False
    if end_ceil is not None and perf_floor > end_ceil:
        return False
    return True


def _row_dict(row, source):
    return {
        "id":         row.id,          # PerformancePersonnel row id; None for inherited-only entries
        "artist_id":  row.artist_id,
        "name":       row.artist.name,
        "instrument": row.instrument,
        "order":      row.order,
        "is_guest":   row.is_guest,
        "note":       row.note,
        "source":     source,
    }


def resolve_performance_personnel(performance):
    """
    Resolve the ordered lineup for a Performance according to its
    personnel_mode. Returns a list of dicts:
        {artist_id, name, instrument, order, is_guest, note, source}
    `source` is one of:
        'inherited' — from the act roster via a Membership stint covering
                      this date
        'guest'     — a PerformancePersonnel row layered on top of an
                      inherited lineup (mode='inherit')
        'explicit'  — a PerformancePersonnel row in mode='explicit'; the act
                      roster is not consulted at all

    'explicit' mode ignores the act roster entirely (e.g. Acoustic All-Stars,
    where the roster is just a pick-list of usual suspects, not a truth
    source — see design doc §3A "Refinement").
    """
    if performance.personnel_mode == "explicit":
        rows = sorted(performance.personnel, key=lambda r: r.order)
        return [_row_dict(r, "explicit") for r in rows]

    # inherit: dedupe stints per artist (a person may have multiple stint
    # rows, e.g. Mickey Hart) — include them once if ANY stint covers this
    # date, with an order taken from their earliest stint per the design
    # doc's display-dedupe rule.
    stints_by_artist = {}
    for m in performance.performer.memberships:
        stints_by_artist.setdefault(m.artist_id, []).append(m)

    inherited = []
    for artist_id, stints in stints_by_artist.items():
        covering = [m for m in stints if _stint_covers(m, performance)]
        if not covering:
            continue
        order = min(m.order for m in stints)
        artist = stints[0].artist
        inherited.append({
            "id":         None,
            "artist_id":  artist_id,
            "name":       artist.name,
            "instrument": None,
            "order":      order,
            "is_guest":   False,
            "note":       None,
            "source":     "inherited",
        })
    inherited.sort(key=lambda d: d["order"])

    guest_rows = sorted(performance.personnel, key=lambda r: r.order)
    guests = [_row_dict(r, "guest") for r in guest_rows]

    return inherited + guests


def sync_performance_personnel_from_names(performance, target_names):
    """
    Reconcile a Performance's resolved lineup to match `target_names` (a flat
    ordered list of names) — the write side of the API split. This is the
    fix for the actual bug in the design doc: editing the recording page's
    Artists pill row used to silently rewrite the ACT's global roster
    (set_performer_members on p.performer). Now it only ever touches THIS
    performance's own personnel.

    The existing frontend always submits a full desired list, not a delta
    (add/remove both call this with the whole current pill set), so intent is
    inferred by diffing `target_names` against what the resolver currently
    reports for this performance — which maps directly onto the design doc's
    already-decided cases 4/5:

      - a name with no prior resolved entry -> added as a guest
        performance_personnel row (case 4: guest sit-in). personnel_mode is
        left untouched.
      - a currently-'inherited' name dropped from the list -> this
        performance switches to 'explicit' mode and its lineup is rewritten
        to the pre-edit resolved roster minus that name, plus anything newly
        added (case 5: member absent from this specific show — no
        is_excluded flag, per the design doc's explicit decision not to build
        a third mechanism for a rare case).
      - a currently-'guest' or 'explicit' name dropped from the list -> its
        row is deleted outright (undoes a prior addition; no mode flip).
      - names present in both -> left alone entirely.
    """
    names, seen = [], set()
    for n in (target_names or []):
        n = (n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            names.append(n)
    target_lower = {n.lower() for n in names}

    resolved = resolve_performance_personnel(performance)
    by_lower = {r["name"].lower(): r for r in resolved}

    dropped_inherited = [r for r in resolved
                          if r["source"] == "inherited" and r["name"].lower() not in target_lower]
    dropped_rows = [r for r in resolved
                    if r["source"] in ("guest", "explicit") and r["name"].lower() not in target_lower]
    added = [n for n in names if n.lower() not in by_lower]

    if dropped_inherited:
        drop_lower = {d["name"].lower() for d in dropped_inherited} | \
                     {d["name"].lower() for d in dropped_rows}
        keep = [r["name"] for r in resolved if r["name"].lower() not in drop_lower]

        final_names, fseen = [], set()
        for n in keep + added:
            if n.lower() not in fseen:
                fseen.add(n.lower())
                final_names.append(n)

        performance.personnel_mode = "explicit"
        db.session.query(PerformancePersonnel).filter_by(
            performance_id=performance.id).delete(synchronize_session=False)
        db.session.flush()
        for i, n in enumerate(final_names):
            artist = resolve_or_create_artist(n)
            db.session.add(PerformancePersonnel(
                performance_id=performance.id, artist_id=artist.id, order=i))
        db.session.flush()
        return

    # No inherited member was dropped: stay in the current mode, just
    # reconcile the guest/explicit rows directly.
    for r in dropped_rows:
        if r["id"] is not None:
            db.session.query(PerformancePersonnel).filter_by(id=r["id"]).delete(
                synchronize_session=False)

    if added:
        base_order = db.session.query(func.max(PerformancePersonnel.order)).filter_by(
            performance_id=performance.id).scalar()
        base_order = (base_order + 1) if base_order is not None else 0
        for i, n in enumerate(added):
            artist = resolve_or_create_artist(n)
            db.session.add(PerformancePersonnel(
                performance_id=performance.id, artist_id=artist.id,
                order=base_order + i, is_guest=(performance.personnel_mode == "inherit")))

    db.session.flush()


def set_performance_personnel_mode(performance, mode):
    """
    Manually toggle a Performance's personnel_mode — distinct from the
    automatic flip inside sync_performance_personnel_from_names' case 5
    (dropping an inherited member). This is the explicit "Inherit / Explicit"
    control on the recording page.

      inherit -> explicit: snapshot the CURRENTLY resolved lineup into
        performance_personnel rows first, so flipping the toggle never makes
        anyone visually disappear — you start explicit mode looking at
        exactly what you had a second ago, then edit from there.

      explicit -> inherit: clear performance_personnel entirely and revert
        to the act's plain resolved roster. Deliberately a clean revert, not
        a merge — keeping the old explicit rows around as bonus "guests"
        after switching back could silently double-list someone who's
        genuinely on the act roster (they'd show once as inherited, once as
        a leftover guest row).

    No-op if already in the requested mode.
    """
    if mode not in ("inherit", "explicit"):
        raise ValueError(f"invalid personnel_mode: {mode!r}")
    if mode == performance.personnel_mode:
        return

    if mode == "explicit":
        resolved = resolve_performance_personnel(performance)
        db.session.query(PerformancePersonnel).filter_by(
            performance_id=performance.id).delete(synchronize_session=False)
        db.session.flush()
        for i, r in enumerate(resolved):
            db.session.add(PerformancePersonnel(
                performance_id=performance.id, artist_id=r["artist_id"], order=i,
                instrument=r["instrument"], is_guest=r["is_guest"], note=r["note"]))
    else:
        db.session.query(PerformancePersonnel).filter_by(
            performance_id=performance.id).delete(synchronize_session=False)

    performance.personnel_mode = mode
    db.session.flush()
