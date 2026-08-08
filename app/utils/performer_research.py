"""
app/utils/performer_research.py — AI Dossier research for the Performer page.

run_performer_research() sends an act's name (+ any existing bio draft) to
Anthropic (BYOK key, same model preference as ingest-side AI Assist), lets it
research with the web-search tool, and returns a drafted biography plus
suggested external resource links (collector sites, discography databases,
etc.) for human review. Nothing is written to the Performer record —
the caller (frontend) reviews and applies: the bio is copy-paste into `bio`,
each resource link is an individual "Add" action into the Resources list.

Same "AI suggests, human approves" rule as app/utils/ai_assist.py — see the
AI Assist Refinement spec (Context Library, 2026-07-20/21): auto-apply was
deliberately removed there after a wrong-but-confident result silently
overwrote a recording's date. Nothing here auto-applies either.
"""

import os
import re
import time

from app.utils.ai_assist import AiAssistError, _compute_cost


def _log(msg):
    print("[dossier] " + msg, flush=True)

# Guarded import so the module loads even when the SDK isn't installed yet —
# same pattern as app/utils/ai_assist.py.
try:
    import anthropic
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


_SYSTEM = """You are the Flux Audio Research Assistant, researching a live-music \
performing act for an archivist's reference page (this is a library of live concert \
recordings — ROIOs — not a commercial streaming catalog).

Rules:
- Write a biography: a few concise paragraphs — formation, key lineup history, era/genre,
notable characteristics as a live act. Grounded in what your research actually supports;
never invent unverifiable specifics (exact dates, member names, etc.) just to sound complete.
If your research is thin, write a shorter, honest biography rather than padding it.
- PLAIN PROSE ONLY in the biography. No citation markers, no footnote brackets like [1],
no markdown, no HTML, no <cite> tags. Separate paragraphs with a blank line and nothing
else. Cited sources belong in the 'sources' field, never inline in the text.
- Suggest external resource links: prioritize collector/taper community sites, discography
or setlist databases (setlist.fm, etree, archive.org, a fan-maintained "known shows"
database if one exists for this act), and dedicated fan archives. These are the kind of
links useful to someone cataloging live recordings — not generic Wikipedia/streaming
service links unless nothing more specific exists for this act.
- Every resource link needs a label (what it is, e.g. "Setlist database") and a url.
- When done, call submit_dossier exactly once with your findings. Keep 'thinking' to
1-2 sentences — what you found and how confident you are, not a repeat of the biography.
"""

_SUBMIT_TOOL = {
    "name": "submit_dossier",
    "description": "Submit your biography draft and suggested resource links for human review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thinking":  {"type": "string", "description": "Brief reasoning/confidence note."},
            "biography": {"type": "string", "description": "Drafted biography, a few paragraphs."},
            "resources": {
                "type": "array",
                "description": "Suggested external resource links.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "url":   {"type": "string"},
                    },
                    "required": ["label", "url"],
                },
            },
            "sources": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"title": {"type": "string"}, "url": {"type": "string"}}},
            },
        },
        "required": ["thinking", "biography"],
    },
}


# Citation markup the web-search tool leaves in generated prose. It surfaced as
# literal "<cite index=...>" text in the biography on the Performer page
# (2026-08-07). Stripped SERVER-SIDE so what we store is already clean —
# scrubbing at render time would leave the mess in `dossier_json` forever and
# require every future consumer to re-implement the same cleanup.
_CITE_RE = re.compile(r"</?cite[^>]*>", re.IGNORECASE)
# Bare bracketed reference markers: [1], [12], [1,2], [3][4]
_REF_RE = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")
_WS_RE = re.compile(r"[ \t]{2,}")


def _clean_prose(text):
    """Strip citation markup and tidy whitespace, preserving paragraph breaks."""
    if not text:
        return text
    out = _CITE_RE.sub("", text)
    out = _REF_RE.sub("", out)
    # Punctuation left stranded by a removed marker: "word ." / "word ,"
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = _WS_RE.sub(" ", out)
    # Collapse 3+ newlines to a paragraph break; keep single/double as authored.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _extract_dossier(resp):
    """Pull the submit_dossier tool input from the response; fall back to text."""
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_dossier":
            data = dict(block.input)
            for key in ("biography", "thinking"):
                if data.get(key):
                    data[key] = _clean_prose(data[key])
            return data
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return {"thinking": _clean_prose(text) or "No structured result returned.",
            "biography": "", "resources": [], "sources": []}


def run_performer_research(performer_name, current_bio, api_key, model):
    """
    Run the Dossier research pass. Returns a dict:
      {thinking, biography, resources:[{label,url}], sources:[...], usage, model}
    Raises AiAssistError on any recoverable failure (reused from ai_assist.py —
    same class of failure, no reason for a second exception type).
    """
    if not _HAS_SDK:
        raise AiAssistError("The 'anthropic' package is not installed. Run: pip install anthropic")
    if not api_key:
        raise AiAssistError("No Anthropic API key configured.")

    text = "Act name: %s\n" % performer_name
    if (current_bio or "").strip():
        text += "\nExisting bio draft (may be incomplete or outdated):\n%s\n" % current_bio.strip()
    text += "\nResearch this act and call submit_dossier with your findings."

    _log("start act=%r model=%s" % (performer_name, model))

    client = anthropic.Anthropic(api_key=api_key, timeout=300.0)
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            system=_SYSTEM,
            tools=[
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 6},
                _SUBMIT_TOOL,
            ],
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.AuthenticationError:
        raise AiAssistError("Anthropic rejected the API key (check it in Settings).")
    except anthropic.APITimeoutError:
        raise AiAssistError("Anthropic request timed out (research took too long).")
    except anthropic.APIError as e:
        _log("APIError: %s" % getattr(e, "message", str(e)))
        raise AiAssistError("Anthropic API error: %s" % getattr(e, "message", str(e)))

    _log("anthropic returned in %.1fs stop_reason=%s" % (time.time() - t0, getattr(resp, "stop_reason", None)))
    result = _extract_dossier(resp)
    result.setdefault("resources", [])
    result.setdefault("sources", [])
    result["model"] = model

    usage = _compute_cost(getattr(resp, "usage", None), model)
    result["usage"] = usage
    if usage:
        _log("usage: in=%d out=%d searches=%d cost=%.3f¢"
             % (usage["input_tokens"], usage["output_tokens"],
                usage["web_search_requests"], usage["cost_cents"]))
    return result
