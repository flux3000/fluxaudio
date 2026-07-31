"""
quality_interpret.py — turns Listening Quality numbers into plain English.

Rule-driven only. There is no per-recording prose anywhere: a score maps to a
band, and the band supplies the sentence. That keeps every explanation
consistent, reviewable in one place, and impossible to drift out of sync with
the number it is describing.

Bands are the same everywhere: 90+, 80-90, 70-80, 60-70, 50-60, under 50.
"""

BANDS = [(90, "excellent"), (80, "good"), (70, "fair"),
         (60, "poor"), (50, "bad"), (0, "severe")]


def band(score):
    """Return the band key for a 0-100 score."""
    if score is None:
        return None
    for floor, key in BANDS:
        if score >= floor:
            return key
    return "severe"


# ── Overall ──────────────────────────────────────────────────────────────────
OVERALL = {
    "excellent": "An excellent-sounding recording. Nothing about the sound "
                 "should get between a listener and the performance.",
    "good":      "A good-sounding recording. Minor character of its own, but "
                 "comfortable to sit through from start to finish.",
    "fair":      "A listenable recording with obvious character. Fine on its "
                 "own terms, but not one to hand to a skeptic first.",
    "poor":      "Rough. Worth hearing for the performance, but the sound "
                 "itself will be a distraction for most listeners.",
    "bad":       "A difficult listen. Only worth it if you specifically want "
                 "this show.",
    "severe":    "Very hard to listen to. Archival interest only.",
}

# ── Tone (50% of the score) ──────────────────────────────────────────────────
TONE = {
    "excellent": "Natural, well-balanced tone. The frequency balance never "
                 "calls attention to itself.",
    "good":      "Slightly colored but comfortable — a mild tilt you stop "
                 "noticing within a minute.",
    "fair":      "Noticeably colored. A little bright and thin, or a little "
                 "thick, without being fatiguing.",
    "poor":      "Clearly unbalanced. Expect boominess or an edgy top end "
                 "that grows tiring over a full show.",
    "bad":       "Poor balance — hollow, honky or harsh enough to be the "
                 "first thing you notice.",
    "severe":    "Severely unbalanced. Tinny, muddy or boxy to the point of "
                 "being hard to sit through.",
}

# ── Noise (30%) ──────────────────────────────────────────────────────────────
NOISE = {
    "excellent": "Essentially silent behind the music.",
    "good":      "Very clean. Any hiss sits well below the performance.",
    "fair":      "Mild background hiss — audible in quiet passages, easy to "
                 "ignore once the music starts.",
    "poor":      "Noticeable hiss or hum. Present through quiet moments and "
                 "obvious between songs.",
    "bad":       "Intrusive noise. Hiss or mains hum competes with quiet "
                 "passages.",
    "severe":    "Heavy noise — a constant layer of hiss or hum across the "
                 "whole performance.",
}

# ── Dynamics (20%) ───────────────────────────────────────────────────────────
DYNAMICS = {
    "excellent": "Fully open dynamics. Transients land with real impact and "
                 "quiet passages stay quiet.",
    "good":      "Good dynamic range with only light compression.",
    "fair":      "Somewhat compressed. Loud and quiet passages sit closer "
                 "together than they should.",
    "poor":      "Noticeably squashed — typically a tape deck's automatic "
                 "gain control riding the level.",
    "bad":       "Heavily compressed. Flat, with little sense of light and "
                 "shade.",
    "severe":    "Crushed. The performance has been flattened to a near-"
                 "constant level.",
}

GROUPS = {"tone": TONE, "noise": NOISE, "dynamics": DYNAMICS}

GROUP_LABEL = {"tone": "Tone", "noise": "Noise", "dynamics": "Dynamics"}
GROUP_BLURB = {
    "tone":     "Frequency balance — the boominess, tinniness and hollowness "
                "that make many live recordings unpleasant. Half the score.",
    "noise":    "Hiss, mains hum and background noise sitting under the music.",
    "dynamics": "How much life is left in the loud-to-quiet range, or how "
                "squashed the recording is.",
}

# What a Technical Issue means, in one line each
ISSUE_TEXT = {
    "Clipping":     "The signal was pushed past maximum and the waveform tops "
                    "are flattened, which sounds like a hard edge on peaks.",
    "Dead channel": "One stereo channel is effectively silent.",
    "Out of phase": "The two channels are wired against each other. On "
                    "speakers this hollows out the center of the image.",
    "Dropouts":     "Short gaps of digital silence interrupt the audio.",
}


# ═════════════════════════════════════════════════════════════════════════════
# Quick-glance: frequency cutoff and what it means
# ═════════════════════════════════════════════════════════════════════════════
def cutoff_verdict(f):
    """
    The one thing most people actually want to know: was this MP3-sourced?

    IMPORTANT: the cutoff frequency alone does not answer that. A 1970s
    audience cassette genuinely has nothing above 13 kHz — that is the medium,
    not a transcode. What identifies a lossy encoder is a CLIFF: a drop of
    35 dB or more inside 500 Hz, sitting in the 15.5-20.5 kHz band, with dead
    flat nothing above it. Analogue rolloff is a gentle slope over several kHz.

    So we report the cutoff number, and separately report the verdict, which
    comes from the wall test in quality_features._lossy().
    """
    edge = f.get("hf_edge_hz")
    lossy = f.get("likely_lossy_source")
    tier = f.get("lossy_tier_est")

    if edge is None:
        return {"khz": None, "verdict": "Unknown", "detail": "", "state": "ok"}

    khz = round(edge / 1000.0, 1)

    if lossy:
        # "short" is what fits in the quick-glance bar; "verdict" is the fuller
        # phrasing used in the tooltip heading.
        short = (tier or "MP3").split(" / ")[0]
        return {"khz": khz, "verdict": f"Lossy source — {tier or 'MP3'}",
                "short": short, "state": "bad",
                "detail": f"A sharp encoder wall sits at {khz} kHz with nothing "
                          "above it. This was encoded to a lossy format at some "
                          "point, even if it is now in a lossless container."}

    if edge >= 19000:
        d = "Content runs to the top of the audible range with no encoder wall."
        state = "good"
    elif edge >= 15000:
        d = ("Full-bandwidth for practical purposes, and the rolloff is gradual "
             "rather than a sharp encoder wall.")
        state = "good"
    elif edge >= 11000:
        d = ("Some high end missing, but it rolls off gradually — the signature "
             "of tape or a limited capture chain, not lossy encoding.")
        state = "ok"
    elif edge >= 7000:
        d = ("Noticeably limited high end, gradually rolled off. Typical of "
             "older analogue sources. Not an encoder wall.")
        state = "ok"
    else:
        d = ("Very restricted high end. The recording will sound dull, but the "
             "rolloff is gradual so this is the source, not lossy encoding.")
        state = "poor"
    return {"khz": khz, "verdict": "No lossy signature", "short": "No MP3 wall",
            "state": state, "detail": d}


# ═════════════════════════════════════════════════════════════════════════════
# Advanced metrics — what each one is, and where a value sits in its range
# ═════════════════════════════════════════════════════════════════════════════
# Each entry: label, unit, what it measures in one sentence, and a ladder of
# (upper_bound, state, description). The first rung whose bound the value is
# below wins. State drives the colour; description is shown on hover.
METRICS = [
    ("presence_balance_db", {
        "label": "Presence balance", "unit": " dB",
        "about": "How loud the 2–6 kHz presence region is versus the low mids. "
                 "Human hearing peaks around 2–5 kHz, so an excess here reads "
                 "as tinny and fatiguing.",
        "scale": [(-26, "ok",   "Very dark"),
                  (-20, "good", "Dark"),
                  (-8,  "good", "Natural"),
                  (-3,  "ok",   "Slightly forward"),
                  (0,   "poor", "Bright"),
                  (99,  "bad",  "Harsh, tinny")],
    }),
    ("midrange_scoop_db", {
        "label": "Midrange scoop", "unit": " dB",
        "about": "Whether the 250–800 Hz body sits below both its neighbours. "
                 "A scoop makes a recording sound hollow and boxy — the "
                 "single strongest predictor of a poor grade in testing.",
        "scale": [(-6, "bad",  "Severely hollow"),
                  (-4, "poor", "Hollow"),
                  (-2, "poor", "Scooped"),
                  (0,  "ok",   "Slightly scooped"),
                  (99, "good", "Full midrange")],
    }),
    ("spectral_tilt_db_oct", {
        "label": "Spectral tilt", "unit": " dB/oct",
        "about": "The overall slope from bass to treble. Music is naturally "
                 "pink-ish, around −3 to −7 dB per octave. Steeper is dull; "
                 "flatter is thin.",
        "scale": [(-14, "bad",  "Very muffled"),
                  (-10, "poor", "Dull"),
                  (-7,  "ok",   "Dark"),
                  (-3,  "good", "Natural"),
                  (-2,  "ok",   "Slightly thin"),
                  (99,  "poor", "Thin, no bass")],
    }),
    ("mid_snr_db", {
        "label": "Signal-to-noise", "unit": " dB",
        "about": "How far the music sits above the noise floor across 1–8 kHz "
                 "— the band carrying most musical information and where hiss "
                 "is most audible.",
        "scale": [(12, "bad",  "Heavy hiss"),
                  (16, "poor", "Noticeable hiss"),
                  (22, "ok",   "Some hiss"),
                  (28, "good", "Clean"),
                  (99, "good", "Very clean")],
    }),
    ("hum_ratio_db", {
        "label": "Mains hum", "unit": " dB",
        "about": "How far the 50/60 Hz hum line and its harmonics rise above "
                 "the surrounding spectrum. Caused by ground loops in the "
                 "recording chain.",
        "scale": [(6,  "good", "None"),
                  (12, "ok",   "Trace"),
                  (20, "poor", "Audible"),
                  (99, "bad",  "Strong")],
    }),
    ("crest_factor_db", {
        "label": "Crest factor", "unit": " dB",
        "about": "Peak level minus average level — how much dynamic range "
                 "survives. Low values mean compression, often a tape deck's "
                 "automatic gain control crushing the room.",
        "scale": [(9,  "bad",  "Heavily compressed"),
                  (13, "poor", "Compressed"),
                  (16, "ok",   "Moderate"),
                  (22, "good", "Open"),
                  (26, "good", "Very open"),
                  (99, "ok",   "Suspiciously high")],
    }),
    ("hf_edge_hz", {
        "label": "Frequency cutoff", "unit": " Hz",
        "about": "The highest frequency still carrying real musical content, "
                 "after subtracting the noise floor.",
        "scale": [(5000,  "bad",  "Very restricted"),
                  (8000,  "poor", "Restricted"),
                  (11000, "ok",   "Limited"),
                  (15000, "good", "Good extension"),
                  (99999, "good", "Full bandwidth")],
    }),
    ("lufs_integrated", {
        "label": "Loudness", "unit": " LUFS",
        "about": "Perceived loudness on the broadcast standard scale. Only "
                 "extremes matter — very quiet wastes headroom, very loud "
                 "means someone squashed it.",
        "scale": [(-26, "poor", "Very quiet"),
                  (-20, "ok",   "Quiet"),
                  (-10, "good", "Well-leveled"),
                  (-8,  "ok",   "Hot"),
                  (99,  "poor", "Slammed")],
    }),
    ("dc_offset", {
        "label": "DC offset", "unit": "", "dp": 5, "abs": True,
        "about": "Whether the waveform sits off-centre instead of swinging "
                 "symmetrically around zero. Caused by a faulty converter or "
                 "preamp. It wastes headroom and clicks at edit points — but "
                 "it is fixable in seconds with a high-pass filter, so it "
                 "never affects the score.",
        "scale": [(0.001, "good", "Centered"),
                  (0.01,  "ok",   "Slightly off-center"),
                  (9,     "poor", "Off-center")],
    }),
]

# Mains frequency (50 vs 60 Hz) was displayed here briefly and removed
# 2026-07-28. It told you which electrical grid the hum sat on, which is a
# geographic curiosity and not a listening-quality fact — nothing about it
# helps decide whether to play a recording. The value is still computed and
# stored (it falls out of the hum measurement for free) but nothing reads it.


def metric_rows(f):
    """Build display rows for the Advanced Metrics panel."""
    rows = []
    for key, m in METRICS:
        v = f.get(key)
        if v is None:
            continue
        cmpv = abs(v) if m.get("abs") else v
        state, desc = m["scale"][-1][1], m["scale"][-1][2]
        for bound, st, d in m["scale"]:
            if cmpv < bound:
                state, desc = st, d
                break
        rows.append({
            "key": key, "label": m["label"], "value": v, "unit": m["unit"],
            "dp": m.get("dp"), "abs": bool(m.get("abs")),
            "state": state, "verdict": desc, "about": m["about"],
            "scale": [{"upto": b, "state": s2, "text": d} for b, s2, d in m["scale"]],
        })
    return rows


def interpret(result):
    """
    Take a score_recording() result and return display-ready text.

    Returns {"overall": {...}, "groups": [{...}], "issues": [{...}]}
    """
    lq = result.get("listening_quality")
    out = {
        "overall": {
            "score": lq,
            "band": band(lq),
            "text": OVERALL.get(band(lq), ""),
        },
        "groups": [],
        "issues": [],
    }
    for key in ("tone", "noise", "dynamics"):
        s = result.get(f"score_{key}")
        b = band(s)
        out["groups"].append({
            "key": key,
            "label": GROUP_LABEL[key],
            "score": s,
            "band": b,
            "text": GROUPS[key].get(b, ""),
            "blurb": GROUP_BLURB[key],
        })
    for i in result.get("technical_issues", []):
        out["issues"].append({**i, "text": ISSUE_TEXT.get(i["issue"], "")})
    return out


def interpret_full(scored, feats):
    """interpret() plus the quick-glance strip and Advanced Metrics rows."""
    out = interpret(scored)
    out["cutoff"] = cutoff_verdict(feats)
    out["metrics"] = metric_rows(feats)
    out["quick"] = {
        "format": feats.get("format"),
        "bitrate_kbps": feats.get("bitrate_kbps"),
        "sample_rate_hz": feats.get("sample_rate_hz"),
        "bit_depth": feats.get("effective_bit_depth"),
    }
    return out
