"""
app/utils/ai_assist.py — AI Research Assistant for the Add Recording flow.

run_ai_assist() sends a scanned recording's current metadata + any bundled
poster/flyer images to Anthropic (BYOK key, Sonnet 5 by default), lets the model
research with the web-search tool, and returns structured, source-cited proposals
for human review. Nothing is written — the caller (frontend) reviews and applies.

Design rules baked into the system prompt:
  • "AI suggests, human approves" — never assert; every proposal carries a
    confidence + source (+ url).
  • confidence "high" ONLY when corroborated by >=2 INDEPENDENT sources or a
    primary-source image (poster/flyer). Internal tags/info/DB all trace to one
    origin — they are NOT independent of each other.
  • Scalars (venue/city/state/country/date/source/event) are proposals and may be
    auto-applied by the UI when high-confidence. Track titles / setlist are NEVER
    proposals — setlist problems go to verify_items for human-by-ear resolution.
  • A finding described in 'thinking' but missing from 'proposals' is a bug (seen
    2026-07-14: model found a well-corroborated date/venue discrepancy, wrote it up
    in the narrative, and returned zero proposals). The prompt now explicitly
    requires every narrated discrepancy to have a matching proposal, even at low
    confidence — the UI never auto-applies below high, so there's no reason to
    withhold one.
  • Notes split: verify_items (transient, → DB Notes) vs provenance_notes
    (lasting, → info-file). ISO dates. Location = city[, state][, country], US only
    for state.
"""

import os
import time


def _log(msg):
    """Print to the Flask console (visible in the terminal running run.py)."""
    print("[ai-assist] " + msg, flush=True)

# Guarded import so the module loads even when the SDK isn't installed yet.
try:
    import anthropic
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


class AiAssistError(Exception):
    """Raised for any recoverable failure in the AI pass (surfaced to the UI)."""


# Scalar fields the model may propose (and the UI may auto-apply). Track titles
# are deliberately excluded — they are suggestion-only via verify_items.
_PROPOSAL_FIELDS = ["artist", "date", "venue", "city", "state", "country", "source", "event"]

_SYSTEM = """You are the Flux Audio Research Assistant, an expert archivist of live \
concert recordings (ROIOs). You verify and correct a recording's metadata using the \
web-search tool.

Rules:
- Suggest, never assert. Every proposal needs a confidence (high|medium|low), a \
source (web|info_file|tags|db_match), and a url when web-based.
- confidence "high" ONLY when corroborated by >=2 INDEPENDENT, authoritative sources. \
The recording's own tags, info file, and DB entry are NOT independent of each other \
(they usually share one origin) — treat them as a single source.
- Prefer canonical/authoritative sources (setlist.fm, artist official sites, \
institutional archives, etree) over forums.
- Propose scalar fields (artist, date, venue, city, state, country, source, event) in \
'proposals'.
- Every discrepancy you describe in 'thinking' MUST also appear as a structured entry in \
'proposals' — never narrate a correction ("the date is actually...", "this was really \
recorded at...") without also emitting the matching proposal(s). If you're not fully \
certain, propose it anyway at medium or low confidence rather than only mentioning it in \
prose — low-confidence proposals are never auto-applied, so submitting one is always safe \
and puts the finding in front of the human either way. A narrative-only finding with no \
matching proposal is a bug, not a valid result. When you correct a date or venue, also \
check whether city/state/country need a matching correction (a wrong venue often means \
the location fields are wrong too) and propose those alongside it.
- ALWAYS actively research the track listing / setlist — both online (setlist.fm, \
archive.org/etree, official sources) AND in the info file text if one is provided — and \
return it in 'track_titles' as {number, title} for as many of the audio files as you can \
confidently identify. Track titles are the single most valuable field and are usually \
missing — this is a primary goal of the pass. Order them to match the audio files. If the \
audio file count does not match the setlist you find, still return your best-ordered \
titles and flag the count discrepancy in verify_items. If you CANNOT find a setlist \
anywhere, say so explicitly in verify_items ("No setlist found for this recording"). \
Never invent titles.
- The info file often contains a real setlist typed as plain, unnumbered lines — no \
"1.", no track numbers, just song titles one per line in the spot where a tracklist \
normally goes. Recognize that pattern and treat it as a primary source, not just prose to \
skim:
  * Segue notation appended to a title ("->", "-->", "/") marks a transition into the \
next song — strip it, it isn't part of the title.
  * Footnote markers appended to a title (*, **, ***, †, ^, or a bracketed number) point \
to an annotation elsewhere in the file (often personnel/lineup notes). Strip the marker \
from the title; the annotation text itself is worth keeping but belongs in \
provenance_notes, not in the title.
  * Section/break labels ("Set I", "Set II", "Encore", "Disc 1", "Intro") are structural \
headers, not songs — skip them, but they confirm you're reading the tracklist section.
  * A line that's only a time value (e.g. "3:40") is a duration, not a title — drop it.
  * Named improvisation/instrumental segments ("Drums", "Bass", "Jam", "Tuning") ARE \
legitimate track titles in live-show setlists — don't discard them just because they're \
short or generic-sounding.
  * Once you have a clean candidate list, cross-check it against your web research (the \
artist's actual setlist for that date, or their general repertoire if the exact show \
isn't findable) to raise your confidence and correct spelling/capitalization. Compare the \
candidate count to the number of audio files as a sanity check, but a mismatch alone is \
not a reason to withhold them — flag it in verify_items instead.
- Dates are ISO (YYYY-MM-DD, partial ok). Venue = the real physical venue \
(canonicalize nicknames). Location = city, state (US only), country.
- Split your notes: verify_items = things the human should double-check (transient); \
provenance_notes = lasting facts worth keeping (date disputes, broadcast/remaster \
context, source anomalies).
- When done, call submit_analysis exactly once with your findings. Put your reasoning \
narrative in 'thinking' — keep it to 2-4 concise sentences. Be economical: don't repeat \
the proposals in prose.
"""

_SUBMIT_TOOL = {
    "name": "submit_analysis",
    "description": "Submit your final research findings for human review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thinking": {"type": "string", "description": "Your reasoning narrative."},
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field":      {"type": "string", "enum": _PROPOSAL_FIELDS},
                        "current":    {"type": "string"},
                        "proposed":   {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "source":     {"type": "string",
                                       "enum": ["web", "info_file", "tags", "db_match"]},
                        "url":        {"type": "string"},
                    },
                    "required": ["field", "proposed", "confidence", "source"],
                },
            },
            "track_titles": {
                "type": "array",
                "description": "Researched setlist, ordered to match the audio files.",
                "items": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer"},
                        "title":  {"type": "string"},
                    },
                    "required": ["number", "title"],
                },
            },
            "verify_items":     {"type": "array", "items": {"type": "string"}},
            "provenance_notes": {"type": "array", "items": {"type": "string"}},
            "sources": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"title": {"type": "string"}, "url": {"type": "string"}}},
            },
        },
        "required": ["thinking", "proposals"],
    },
}


def _metadata_summary(current, folder_name):
    """Human-readable dump of the current metadata for the prompt."""
    lines = ["Folder name: %s" % folder_name, "", "Current metadata (to verify/correct):"]
    for k in ("artist", "date", "venue", "city", "state", "country", "source", "lineage", "event"):
        v = current.get(k)
        if v:
            lines.append("  %s: %s" % (k, v))
    tracks = current.get("tracks") or []
    if tracks:
        lines.append("")
        lines.append("Tracks on disk (%d) — titles may be missing/uncertain:" % len(tracks))
        for t in tracks[:60]:
            dur = t.get("duration")
            dur_s = " [%d:%02d]" % (int(dur) // 60, int(dur) % 60) if dur else ""
            lines.append("  %s. %s%s" % (t.get("number", "?"), t.get("title") or "(untitled)", dur_s))
    return "\n".join(lines)


def _extract_analysis(resp):
    """Pull the submit_analysis tool input from the response; fall back to text."""
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_analysis":
            return dict(block.input)
    # Fallback: model returned prose instead of calling the tool.
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return {"thinking": text.strip() or "No structured analysis returned.",
            "proposals": [], "verify_items": [], "provenance_notes": [], "sources": []}


def run_ai_assist(folder_path, current, api_key, model):
    """
    Run the research pass. Returns a dict:
      {thinking, proposals:[...], verify_items:[...], provenance_notes:[...], sources:[...]}
    Raises AiAssistError on any recoverable failure.
    """
    if not _HAS_SDK:
        raise AiAssistError("The 'anthropic' package is not installed. Run: pip install anthropic")
    if not api_key:
        raise AiAssistError("No Anthropic API key configured.")

    content = [
        {"type": "text", "text": _metadata_summary(current, os.path.basename(folder_path))},
    ]
    info_text = (current.get("info_file_content") or "").strip()
    if info_text:
        content.append({"type": "text", "text":
            "Info file contents, as found in the recording's folder (may contain an "
            "unnumbered setlist — see the info-file parsing rules above):\n---\n"
            + info_text + "\n---"})
    content.append({"type": "text",
                     "text": "Research this recording and call submit_analysis with your findings."})

    _log("start folder=%r model=%s" % (os.path.basename(folder_path), model))

    client = anthropic.Anthropic(api_key=api_key, timeout=600.0)
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=_SYSTEM,
            tools=[
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 6},
                _SUBMIT_TOOL,
            ],
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise AiAssistError("Anthropic rejected the API key (check it in Settings).")
    except anthropic.APITimeoutError:
        raise AiAssistError("Anthropic request timed out (research took too long).")
    except anthropic.APIError as e:
        _log("APIError: %s" % getattr(e, "message", str(e)))
        raise AiAssistError("Anthropic API error: %s" % getattr(e, "message", str(e)))

    _log("anthropic returned in %.1fs stop_reason=%s" % (time.time() - t0, getattr(resp, "stop_reason", None)))
    result = _extract_analysis(resp)
    _log("proposals=%d verify=%d provenance=%d"
         % (len(result.get("proposals", [])), len(result.get("verify_items", [])),
            len(result.get("provenance_notes", []))))
    # Normalize: ensure list keys exist and drop any non-scalar proposals defensively.
    result.setdefault("track_titles", [])
    result.setdefault("verify_items", [])
    result.setdefault("provenance_notes", [])
    result.setdefault("sources", [])
    result["proposals"] = [p for p in result.get("proposals", [])
                           if p.get("field") in _PROPOSAL_FIELDS and p.get("proposed")]
    result["model"] = model
    return result
