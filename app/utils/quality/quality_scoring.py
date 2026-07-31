"""
quality_scoring.py — Listening Quality score, v2.

A PURE FUNCTION over the raw features produced by quality_features.py. No audio
decode happens here, which is the point: retuning any curve or weight below is a
fast re-score pass over stored feature values.

Spec: Context Library/Recording Quality Score — v2 Build Brief (HANDOFF).md

──────────────────────────────────────────────────────────────────────────────
WHAT THIS IS FOR
──────────────────────────────────────────────────────────────────────────────
Ryan's design brief, which overrides any instinct toward technical
completeness:

    "The primary use case of this entire system is to LISTEN TO THE MUSIC that
     I have collected. I do not want to mess around with the lower quality
     things, life is too short. I want to be able to share with others who are
     definitely in that 99% who are biased against live recordings because they
     don't like the boominess, tinniness, and otherwise weird listening
     experience that many of them have."

Every complaint in that sentence is a TONE complaint. Hence Tone at half the
weight, and hence a couple of digital pops counting for nothing.

──────────────────────────────────────────────────────────────────────────────
STRUCTURE — two separate things
──────────────────────────────────────────────────────────────────────────────
  "Listening Quality"  0-100, from three groups:
        Tone      50%   presence balance, midrange scoop, spectral tilt
        Noise     30%   mid-band signal-to-noise, hum
        Dynamics  20%   crest factor

  "Technical Issues"   a pass/fail list that deducts ONLY when tripped.
        Clipping, dead channel, out of phase, dropouts.

All four Technical Issues scored clean across every recording tested so far.
They are insurance against the day something genuinely broken is ingested, not
discriminators — which is exactly why they are gates rather than weighted
metrics. As weighted metrics they were 25% of the score doing no work.

v1 had 16 measurements with hand-drawn curves and ~36 tuned numbers, fitted
against 13 human grades. That was far too many knobs for the evidence. This is
6 curves and 4 gates.

──────────────────────────────────────────────────────────────────────────────
SCALE — criterion-referenced, NOT norm-referenced
──────────────────────────────────────────────────────────────────────────────
Curves are anchored to perception, not to the rest of the library.

  100 means "at or beyond the point where further improvement is inaudible."
  It does NOT mean "best recording I have."

So ties at the top are correct; a score never changes because something else
was ingested; and scores stay comparable across artists and meaningful to a
peer node with a different library.

PRECISION: reported to one decimal because that is useful for ranking, but the
honest resolution is roughly +/-2 points.
"""

import numpy as np

QUALITY_SCORE_VERSION = "2"


def curve(x, points):
    """Piecewise-linear interpolation over (input, score) anchors. Clamped."""
    if x is None:
        return None
    return float(np.clip(np.interp(x, [p[0] for p in points],
                                   [p[1] for p in points]), 0.0, 100.0))


def _geo(parts):
    """
    Weighted GEOMETRIC mean over (score, weight) pairs, skipping unavailable
    metrics.

    Geometric rather than arithmetic because listening is gated by the WORST
    problem, not the average of all of them. A recording with pristine dynamics
    and no treble is not "average", it is unpleasant. The two means agree within
    a point when components are balanced and diverge sharply only when one
    collapses — which is the whole behaviour we want, from one line rather than
    a table of special-case caps.
    """
    live = [(s, w) for s, w in parts if s is not None]
    if not live:
        return None
    tw = sum(w for _, w in live)
    return float(np.exp(sum(w * np.log(max(s, 1.0)) for s, w in live) / tw))


# ═════════════════════════════════════════════════════════════════════════════
# TONE  (50%)
# ═════════════════════════════════════════════════════════════════════════════

# Presence balance: 2-6 kHz minus 250-800 Hz, in dB. The tinniness detector.
#
# Strongly ASYMMETRIC on purpose — nearly flat on the dark side, steep on the
# bright side. Justified twice over: perceptually harshness drives a listener
# away far faster than dullness does, and empirically this feature correlates
# NEGATIVELY with Ryan's grades (r = -0.42), i.e. darker reads as better.
#
# The decisive evidence was a controlled listen. Hammersmith (-19.9 dB) against
# Denver (-13.7 dB) on headphones: Ryan confirmed Hammersmith is audibly
# "muddier" — so the measurement is TRUE — and graded it an A anyway. The
# feature was right and the curve was wrong.
# v2 re-anchored on AUDIBILITY rather than corpus position. The previous curve
# mapped +2.7 dB to 10/100 — implying "about as bad as a recording can sound" —
# and that was drawn back when bandwidth metrics were still in this group
# propping the 1970 Georgia Tech tape up. With those removed it overshot wildly:
# Georgia Tech scored 36.6 against Ryan's C (63).
#
# +2.7 dB is "noticeably bright and thin", not unlistenable. The bottom of the
# scale is now reserved for balances that would genuinely be brutal (+10 dB and
# beyond). Georgia Tech now lands at 56 against a target of 63.
PRESENCE = [(-30, 82), (-24, 94), (-20, 100), (-8, 100), (-5, 92), (-3, 80),
            (0, 62), (3, 42), (6, 25), (10, 10), (15, 0)]

# Midrange scoop: low mids vs the mean of their neighbours. Negative = scooped,
# the hollow "smiley-face EQ" character that travels with tinniness.
# The single best raw predictor in the entire feature set (r = +0.60).
SCOOP = [(-10, 3), (-8, 5), (-6, 20), (-4, 40), (-2, 70), (0, 100), (6, 100)]

# Spectral tilt, dB/octave over 200 Hz - 8 kHz. Music is naturally pink-ish;
# both "too dark" and "no bottom end" are penalised.
TILT = [(-20, 0), (-18, 5), (-15, 22), (-12, 45), (-9, 70), (-7, 90),
        (-5, 100), (-3, 95), (-2, 80), (-1, 65), (0, 50)]

# Energy above 8 kHz relative to total — is there any top end at all.
#
# RESTORED to the Tone group 2026-07-28. The v2 simplification dropped every
# bandwidth measure because they correlated poorly (r = +0.106) with the
# original 20 grades. That left Tone unable to see "this recording has no
# treble", and it showed: the 1992 Danny Gatton tape measures 6.4 kHz cutoff
# and -48.5 dB above 8 kHz — Ryan calls it "made inside a cigar box" — yet
# scored 77.2, ABOVE the 1988 recording he grades an A.
#
# The original 20 simply had nothing that dark except Watkins Glen, so the
# correlation test could not see the metric's value. Restoring it fixed the
# ordering AND raised correlation across the 20 from 0.823 to 0.860.
HF_RATIO = [(-70, 0), (-60, 12), (-52, 28), (-45, 45), (-40, 58), (-35, 72),
            (-30, 83), (-25, 92), (-20, 100), (-10, 100)]

# ═════════════════════════════════════════════════════════════════════════════
# NOISE  (30%)
# ═════════════════════════════════════════════════════════════════════════════

# Signal-to-noise across 1-8 kHz.
#
# Measured in a band every recording actually occupies, whatever its bandwidth.
# An earlier version measured 10-16 kHz and was badly wrong: a recording
# band-limited to 8 kHz has neither music NOR much noise up there, so the ratio
# collapsed and it scored as hissy. We were measuring silence and calling it
# noise. The 1981 Bushnell tape read 13.0 dB that way; its real 1-8 kHz SNR is
# 27.0, the cleanest in its corpus, and Ryan graded it an A.
MID_SNR = [(6, 3), (10, 15), (13, 28), (16, 42), (19, 57), (22, 70), (25, 82),
           (28, 92), (32, 100), (40, 100)]

# Mains hum: dB above the local spectral median at 50/60 Hz and harmonics.
HUM = [(0, 100), (6, 100), (12, 80), (20, 50), (30, 20), (40, 0)]

# ═════════════════════════════════════════════════════════════════════════════
# DYNAMICS  (20%)
# ═════════════════════════════════════════════════════════════════════════════

# Crest factor = peak minus RMS, in dB. How far transients stand above the
# average level — "punch", or inversely, "how squashed".
#
# Genuinely diagnostic here because the #1 cassette-audience defect is deck
# automatic gain control riding the level and crushing the room's dynamics.
# Very high values are suspicious rather than good: usually a very quiet
# transfer, or a single click inflating the peak.
CREST = [(4, 0), (6, 10), (9, 30), (13, 75), (16, 95), (19, 100), (22, 100),
         (26, 85), (30, 60), (36, 40)]


# ═════════════════════════════════════════════════════════════════════════════
# Group scoring
# ═════════════════════════════════════════════════════════════════════════════
# Reweighted 2026-07-28 from 50/30/20 after Ryan suggested dynamics was being
# undervalued. He was right, and by a wide margin — 40/20/40 gave the lowest
# average error of any combination tested against the n=20 fit (4.14 vs 5.35),
# tied the best correlation (0.860), and was the only weighting that put his
# stated best and worst Danny Gatton recordings first and last respectively.
#
# Noise dropping to 20% fit what the data had been saying independently: since
# the hum fix, hum barely varies across recordings, and mid-band SNR has been
# the metric most often at odds with what Ryan actually hears.
#
# Reweighted AGAIN 2026-07-30 to Dynamics 50 / Tone 35 / Noise 15 — Ryan's
# explicit call as the new default, made while discussing the Gatton labels
# (1/25/79 = B, 12/3/88 = A) rather than a re-run of the n=20/n=24 fit. Treat
# as the current default until re-verified against the fit — flag to Ryan
# rather than reverting unilaterally if a re-run says otherwise.
GROUP_WEIGHTS = {"tone": 0.35, "noise": 0.15, "dynamics": 0.50}


def _arith(parts):
    """
    Weighted ARITHMETIC mean, used WITHIN a group.

    Groups combine geometrically (worst problem gates the score) but their
    sub-metrics combine arithmetically, and the distinction is deliberate.
    Within a group the sub-metrics are three views of ONE phenomenon — spectral
    balance, or noise — so averaging them is the right way to read that
    phenomenon. Between groups they are independent failure modes, so gating on
    the worst is right.

    v1 used geometric everywhere, added specifically because Georgia Tech's
    excellent HF extension was averaging away its harsh balance. That reason is
    gone: bandwidth metrics are no longer in the Tone group, so all three
    members now measure the same thing and cannot mask each other. Keeping
    geometric here made Georgia Tech 26 points harsher than Ryan's grade.
    """
    live = [(s, w) for s, w in parts if s is not None]
    if not live:
        return None
    return sum(s * w for s, w in live) / sum(w for _, w in live)


def score_tone(f):
    """
    Reweighted 2026-07-28 after two recordings exposed the previous balance.

    Presence dropped 0.45 -> 0.35 because it demonstrably cannot tell pleasant
    brightness from harshness: the 1970 Georgia Tech tape reads +2.7 dB (Ryan:
    "crazy tinniness", C) and the 1988 Danny Gatton reads +2.8 dB (Ryan:
    "sounds excellent", A). Identical measurement, opposite verdicts — so it
    should not be carrying nearly half the Tone score on its own.

    HF extension added at 0.20 so Tone can see a recording with no top end.
    Together these lifted correlation across the 20 graded recordings from
    0.823 to 0.860 and put Ryan's best/worst Gatton pair in the right order.
    """
    return _arith([
        (curve(f.get("presence_balance_db"), PRESENCE), 0.35),
        (curve(f.get("midrange_scoop_db"), SCOOP), 0.25),
        (curve(f.get("spectral_tilt_db_oct"), TILT), 0.20),
        (curve(f.get("hf_energy_ratio_db"), HF_RATIO), 0.20),
    ])


def score_noise(f):
    return _arith([
        (curve(f.get("mid_snr_db"), MID_SNR), 0.65),
        (curve(f.get("hum_ratio_db"), HUM), 0.35),
    ])


def score_dynamics(f):
    return curve(f.get("crest_factor_db"), CREST)


# ═════════════════════════════════════════════════════════════════════════════
# Technical Issues — pass/fail, deduct only when tripped
# ═════════════════════════════════════════════════════════════════════════════
def technical_issues(f):
    """
    Returns (total_deduction, [issue dicts]).

    Normally returns (0.0, []) — every recording tested so far is clean on all
    four. These exist for genuinely broken material.

    Deduction sizes are a starting proposal and have never been exercised
    against a real failing recording. Revisit when one turns up.
    """
    issues = []

    clip = f.get("clipping_pct")
    if clip is not None and clip > 0.01:
        # Run-qualified clipping only: isolated full-scale samples are
        # normalisation, not damage. Scale severity, cap at -25.
        d = min(25.0, 5.0 + 20.0 * min(1.0, (clip - 0.01) / 0.5))
        issues.append({"issue": "Clipping", "detail": f"{clip:.3f}% of samples",
                       "deduction": round(d, 1)})

    ch_min = f.get("channel_rms_min_db")
    if ch_min is not None and ch_min < -60:
        issues.append({"issue": "Dead channel", "detail": f"{ch_min:.0f} dBFS",
                       "deduction": 30.0})

    ph = f.get("phase_correlation")
    if ph is not None and ph < -0.3:
        issues.append({"issue": "Out of phase", "detail": f"correlation {ph:+.2f}",
                       "deduction": 20.0})

    drop = f.get("dropout_count")
    if drop is not None and drop >= 3:
        issues.append({"issue": "Dropouts", "detail": f"{int(drop)} gaps",
                       "deduction": 10.0})

    return sum(i["deduction"] for i in issues), issues


# ═════════════════════════════════════════════════════════════════════════════
# Informational flags — never affect the score ("nerd zone")
# ═════════════════════════════════════════════════════════════════════════════
def informational_flags(f):
    """
    Provenance curiosities, never scored.

    DC offset and mains frequency used to live here as pills. They are now
    ordinary rows in the Advanced Metrics table instead — a value with a
    verdict reads better than a bare pill, and DC offset has a real range worth
    colour-coding. What remains here is genuinely rare and genuinely binary, so
    this list is normally empty.
    """
    out = []
    if f.get("likely_lossy_source"):
        out.append(f"Likely lossy source ({f.get('lossy_tier_est') or 'unknown tier'})")
    if f.get("bit_depth_padded"):
        out.append(f"Bit depth padded (effective {f.get('effective_bit_depth')})")
    if f.get("upsampled"):
        out.append("Upsampled sample rate")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Composite
# ═════════════════════════════════════════════════════════════════════════════
def score_recording(f, preset="listener"):
    """Score a raw-feature dict. `preset` retained for API compatibility."""
    if "error" in f:
        return {"error": f["error"]}

    groups = {"tone": score_tone(f), "noise": score_noise(f),
              "dynamics": score_dynamics(f)}
    base = _geo([(groups[k], GROUP_WEIGHTS[k]) for k in groups])

    deduction, issues = technical_issues(f)
    lq = None if base is None else float(np.clip(base - deduction, 0.0, 100.0))

    return {
        "listening_quality": round(lq, 1) if lq is not None else None,
        "score_tone": round(groups["tone"], 1) if groups["tone"] is not None else None,
        "score_noise": round(groups["noise"], 1) if groups["noise"] is not None else None,
        "score_dynamics": round(groups["dynamics"], 1) if groups["dynamics"] is not None else None,
        "technical_issues": issues,
        "technical_deduction": round(deduction, 1),
        "flags": informational_flags(f),
        "score_version": QUALITY_SCORE_VERSION,
    }
