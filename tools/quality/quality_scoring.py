"""
quality_scoring.py — Recording Quality Score, scoring layer.

A PURE FUNCTION over the raw features produced by quality_features.py.
No audio decode happens here, which is the whole point: retuning any curve or
weight below is a fast re-score pass over stored feature values.

Spec: Context Library/Recording Quality Score — Design Spec v1.md

──────────────────────────────────────────────────────────────────────────────
SCALE PHILOSOPHY — criterion-referenced, NOT norm-referenced
──────────────────────────────────────────────────────────────────────────────
Every curve below maps a physical measurement to a score against FIXED anchors
tied to perception, not against the rest of the library.

  100 means "at or beyond the point where further improvement is inaudible."
  It does NOT mean "best recording I have."

Consequences, all of them intentional:

  * Ties at the top are correct. Two flawless soundboards should both score ~95.
  * A recording's score never changes because something else was ingested.
  * Scores are comparable across artists, and remain meaningful when shared
    with a peer node that has a completely different library.

The alternative — percentile rank within the library — would give guaranteed
spread but would make every score unstable and non-portable. Rejected.

Where physics supplies an anchor (0 clipping is perfect; 16-bit is transparent;
0 dBTP is a hard line; content above 16 kHz is inaudible to most adults) the
curve is anchored there. Where it does not (how hissy is too hissy), the anchor
is perceptual judgement, sanity-checked against real recordings — and those are
exactly the numbers a listening review should correct.

PRECISION NOTE: the composite is reported to one decimal because that is useful
for ranking, but the honest resolution of this measurement is roughly +/-2
points. Do not read meaning into a 0.4-point gap.
"""

import numpy as np

QUALITY_SCORE_VERSION = "1"


def curve(x, points):
    """
    Piecewise-linear interpolation over (input, score) anchor points.
    Clamped at both ends. `points` must be sorted ascending by input.
    """
    if x is None:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return float(np.clip(np.interp(x, xs, ys), 0.0, 100.0))


# ═════════════════════════════════════════════════════════════════════════════
# Curves
# ═════════════════════════════════════════════════════════════════════════════

# ── NOISE ────────────────────────────────────────────────────────────────────
# How far programme sits above the noise floor in the 10-16 kHz band. This is
# the hiss measure that works: measuring noise against TOTAL energy instead
# rewards a recording for having no treble to be hissy in.
HF_SNR = [(0, 0), (4, 10), (7, 25), (10, 42), (12, 55), (15, 72),
          (20, 90), (25, 100)]

# Signal-to-noise across 1-8 kHz — the PRIMARY noise measure from rev 4 on.
# Measured in a band every recording actually occupies, so band-limited sources
# are judged on their noise rather than on their silence.
MID_SNR = [(6, 3), (10, 15), (13, 28), (16, 42), (19, 57), (22, 70), (25, 82),
           (28, 92), (32, 100), (40, 100)]

# Mains hum, dB above the local spectral median at 50/60 Hz + harmonics.
HUM = [(0, 100), (6, 100), (12, 80), (20, 50), (30, 20), (40, 0)]

# Sub-25 Hz energy relative to total.
#
# Softened considerably in rev 3. Subsonic energy is below the useful range of
# most playback systems — it wastes headroom and is a legitimate technical fault,
# but it is largely INAUDIBLE, and this is a listenability score. The old curve
# put the 1983 Hammersmith soundboard's Noise facet at 31 for a defect nobody
# can hear on normal speakers.
RUMBLE = [(-60, 100), (-45, 95), (-32, 85), (-24, 72), (-18, 55), (-12, 35),
          (-6, 15)]

# Measured effective bit depth. >=16 is transparent and never penalised.
BIT_DEPTH = [(8, 15), (10, 30), (12, 50), (14, 70), (16, 100), (24, 100)]

# ── CLARITY ──────────────────────────────────────────────────────────────────
# Highest frequency carrying real programme content (noise-subtracted).
#
# Rev 3: flattened hard above 6 kHz. HF rolloff is the MOST FORGIVABLE of all
# the defects we measure. A clean soundboard band-limited to 9 kHz sounds warm
# and vintage — like a good cassette or an FM broadcast — not broken. What
# sounds broken is 4 kHz (AM radio) and below.
#
# The old curve scored 8 kHz at 45, treating a pleasant warm recording as
# half-ruined. Ryan graded the 1983 Hammersmith soundboard (7.8 kHz edge on the
# sampled track) a clear A.
HF_EDGE = [(2000, 0), (3000, 10), (4000, 25), (5000, 42), (6000, 58),
           (7000, 70), (8000, 78), (9000, 84), (11000, 92), (13000, 97),
           (16000, 100), (22050, 100)]

# Energy above 8 kHz relative to total. Softened in rev 3 for the same reason.
HF_RATIO = [(-70, 0), (-60, 12), (-52, 28), (-45, 45), (-40, 58), (-35, 72),
            (-30, 83), (-25, 92), (-20, 100), (-10, 100)]

# Spectral tilt, dB/octave over 200 Hz - 8 kHz. Music is naturally pink-ish;
# both "too dark" and "no bottom end" are penalised.
TILT = [(-20, 0), (-18, 5), (-15, 22), (-12, 45), (-9, 70), (-7, 90),
        (-5, 100), (-3, 95), (-2, 80), (-1, 65), (0, 50)]

# Presence balance: 2-6 kHz minus 250-800 Hz, in dB. UNIMODAL — this is the
# tinniness detector, added after the 1970 Georgia Tech tape scored 86.7 on
# bandwidth alone while being genuinely unpleasant to sit through.
#
# The ear's sensitivity peaks around 2-5 kHz, so a recording with more energy
# there than in the body of the sound is fatiguing no matter how much real
# bandwidth it has. The penalty above -2 dB is steep on purpose: harshness
# drives listeners away faster than dullness does.
PRESENCE = [(-26, 15), (-22, 30), (-18, 60), (-14, 100), (-6, 100), (-4, 85),
            (-2, 60), (0, 35), (2, 15), (4, 5), (8, 0)]

# Low mids relative to the mean of their neighbours. Negative = scooped, the
# hollow "smiley-face EQ" character that travels with tinniness.
SCOOP = [(-10, 3), (-8, 5), (-6, 20), (-4, 40), (-2, 70), (0, 100), (6, 100)]

# ── DYNAMICS ─────────────────────────────────────────────────────────────────
# Peak minus RMS. Low = squashed (cassette AGC, limiting). Very high =
# suspicious: a quiet transfer, or one click inflating the peak.
CREST = [(4, 0), (6, 10), (9, 30), (13, 75), (16, 95), (19, 100), (22, 100),
         (26, 85), (30, 60), (36, 40)]

# Integrated LUFS. Flat plateau in the middle: penalise extremes only, never
# reward loudness, or this becomes a loudness-war scoreboard.
LUFS = [(-40, 25), (-35, 35), (-30, 45), (-26, 70), (-22, 90), (-20, 100),
        (-12, 100), (-10, 88), (-8, 65), (-6, 40), (-4, 20)]

# True peak, dBTP (4x oversampled).
TRUE_PEAK = [(-12, 100), (-3, 100), (-1, 95), (0, 70), (0.5, 50), (1, 30),
             (2, 10), (4, 0)]

# ── DEFECTS ──────────────────────────────────────────────────────────────────
# Run-qualified clipping only (>=10 consecutive full-scale samples). Isolated
# full-scale samples are normalisation, not damage.
CLIPPING = [(0, 100), (0.001, 95), (0.01, 75), (0.1, 45), (1, 15), (3, 0)]

# Clicks/min from LPC residual outliers.
# LOWEST-CONFIDENCE METRIC IN THE SET. Musical transients are also
# unpredictable, so some false-positive rate is unavoidable. Weighted low, and
# the curve is deliberately forgiving below 50/min.
CLICKS = [(0, 100), (20, 92), (50, 80), (100, 62), (200, 40), (400, 18),
          (800, 0)]

# |RMS L - RMS R| in dB.
BALANCE = [(0, 100), (1, 98), (3, 88), (6, 65), (12, 35), (20, 0)]

# L/R Pearson correlation.
PHASE = [(-1.0, 0), (-0.3, 15), (0.0, 50), (0.15, 70), (0.3, 100),
         (0.9, 100), (0.97, 85), (1.0, 85)]

# Digital-silence dropouts >20 ms.
DROPOUTS = [(0, 100), (1, 80), (3, 45), (6, 15), (10, 0)]


# ═════════════════════════════════════════════════════════════════════════════
# Facet scoring
# ═════════════════════════════════════════════════════════════════════════════
def _blend(parts):
    """Weighted mean over (score, weight) pairs, skipping unavailable metrics."""
    live = [(s, w) for s, w in parts if s is not None]
    if not live:
        return None, {}
    tw = sum(w for _, w in live)
    # GEOMETRIC, not arithmetic — same reasoning as the facet-level aggregation:
    # a facet is gated by its worst component. Under an arithmetic blend Georgia
    # Tech's excellent HF extension averaged away its harsh tonal balance and
    # Clarity came out at 96 for a recording that is painful to sit through.
    eps = 1.0
    return float(np.exp(sum(w * np.log(max(s, eps)) for s, w in live) / tw)), {}


def score_noise(f):
    parts = [
        (curve(f.get("mid_snr_db"), MID_SNR), 0.55),
        (curve(f.get("hum_ratio_db"), HUM), 0.30),
        (curve(f.get("rumble_db"), RUMBLE), 0.05),
        (curve(f.get("effective_bit_depth"), BIT_DEPTH), 0.10),
    ]
    # crowd_level_db is extracted but carries weight 0 in v1 pending review
    return _blend(parts)[0]


def score_clarity(f):
    """
    Clarity = bandwidth AND tonal balance.

    v1 weighted bandwidth alone and was structurally unable to notice that a
    recording was harsh. Half the weight now sits on balance measures that
    penalise deviation in either direction.
    """
    # Rev 3 reweighting follows measured STABILITY. Across five tracks of one
    # Hammersmith soundboard, hf_edge varied 7079-15703 Hz (2x) while presence
    # balance moved 1.7 dB and tilt 1.2 dB. The stable measures describe the
    # capture chain; hf_edge substantially describes the song. Weight follows.
    return _blend([
        (curve(f.get("hf_edge_hz"), HF_EDGE), 0.15),
        (curve(f.get("hf_energy_ratio_db"), HF_RATIO), 0.25),
        (curve(f.get("spectral_tilt_db_oct"), TILT), 0.20),
        (curve(f.get("presence_balance_db"), PRESENCE), 0.30),
        (curve(f.get("midrange_scoop_db"), SCOOP), 0.10),
    ])[0]


def score_dynamics(f):
    return _blend([
        (curve(f.get("crest_factor_db"), CREST), 0.60),
        (curve(f.get("lufs_integrated"), LUFS), 0.25),
        (curve(f.get("true_peak_dbtp"), TRUE_PEAK), 0.15),
    ])[0]


def score_defects(f):
    bal = curve(abs(f["channel_balance_db"]), BALANCE) if f.get("channel_balance_db") is not None else None
    ph = curve(f.get("phase_correlation"), PHASE)
    chan, _ = _blend([(bal, 0.6), (ph, 0.4)])

    # Dead channel overrides everything else in this sub-metric
    if f.get("channel_rms_min_db") is not None and f["channel_rms_min_db"] < -60:
        chan = 0.0

    # CLICKS ARE DISABLED IN V1 (weight 0).
    #
    # The metric survived two rebuilds and failed both times. It reported 1646
    # clicks/min on a clean soundboard, then — after the MAD/width fix — 11223
    # clicks/min on the 1996 Plummer Hall recording, which measures 91 on Noise
    # and 96 on Clarity. It also carries a systematic genre bias: median 52
    # clicks/min across the Allman Brothers corpus versus 94 across the acoustic
    # guitar trio, because a pick attack is exactly the sharp unpredictable
    # transient an LPC-residual detector is built to find.
    #
    # The feature is still extracted and stored, so re-enabling it later costs
    # no audio decode. The real fix is to require a click to be spectrally
    # BROADBAND as well as narrow, which separates a digital tick from a pick
    # attack — deferred, not attempted here.
    return _blend([
        (curve(f.get("clipping_pct"), CLIPPING), 0.45),
        (curve(f.get("click_density_per_min"), CLICKS), 0.00),
        (chan, 0.35),
        (curve(f.get("dropout_count"), DROPOUTS), 0.20),
    ])[0]


def score_flags(f):
    """Base 100, deduct for provenance issues. Weight 0 under the Listener preset."""
    s = 100.0
    if f.get("likely_lossy_source"):
        tier = (f.get("lossy_tier_est") or "")
        s -= 15.0 if ("320" in tier or "256" in tier) else 40.0
    if f.get("bit_depth_padded"):
        s -= 20.0
    if f.get("upsampled"):
        s -= 15.0
    dc = abs(f.get("dc_offset") or 0.0)
    if dc > 0.01:
        s -= 20.0
    elif dc > 0.001:
        s -= 8.0
    return float(np.clip(s, 0.0, 100.0))


PRESETS = {
    "listener":  {"noise": 30, "clarity": 30, "dynamics": 15, "defects": 25, "flags": 0},
    "archivist": {"noise": 25, "clarity": 20, "dynamics": 15, "defects": 25, "flags": 15},
    "purist":    {"noise": 20, "clarity": 25, "dynamics": 20, "defects": 20, "flags": 15},
}


def score_recording(f, preset="listener"):
    """Score a raw-feature dict. Returns facet scores + composite."""
    if "error" in f:
        return {"error": f["error"]}

    facets = {
        "noise":    score_noise(f),
        "clarity":  score_clarity(f),
        "dynamics": score_dynamics(f),
        "defects":  score_defects(f),
        "flags":    score_flags(f),
    }
    w = PRESETS[preset]
    live = [(facets[k], w[k]) for k in facets if facets[k] is not None and w[k] > 0]
    tw = sum(x[1] for x in live)

    # WEIGHTED GEOMETRIC MEAN, not arithmetic.
    #
    # Listening experience is gated by the worst problem, not the average of all
    # of them. A recording with pristine dynamics and no treble is not "average";
    # it is unpleasant. Under an arithmetic mean the 1973 Watkins Glen tape —
    # nothing above 4.8 kHz, Clarity 9.5 — scored 54.8, because good dynamics
    # and an absence of clicks propped it up.
    #
    # The geometric mean agrees with the arithmetic mean to within a point when
    # facets are balanced, and diverges sharply only when one collapses. That is
    # exactly the behaviour we want, and it gets it from one line rather than
    # from a table of special-case caps.
    if tw:
        eps = 1.0                       # keeps a zero facet from annihilating the score
        composite = float(np.exp(sum(wt * np.log(max(s, eps)) for s, wt in live) / tw))
    else:
        composite = None

    return {
        **{f"score_{k}": (round(v, 1) if v is not None else None)
           for k, v in facets.items()},
        "score_composite": round(composite, 1) if composite is not None else None,
        "preset_used": preset,
        "score_version": QUALITY_SCORE_VERSION,
    }
