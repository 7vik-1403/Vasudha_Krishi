"""Agro-climatic suitability gate.

The ML classifier only sees N/P/K/temperature/humidity/pH/rainfall - it has no idea
*where* the field is, so it can rank a temperate crop such as apple for a hot place
like Pune. This module adds explicit temperature / rainfall / pH windows per crop and
hard-excludes crops whose climate need is clearly not met.
"""

from cropapp.services.datasets import calendar_for, load_suitability, suitability_for

# How far outside a window a value may sit before it is treated as a hard "cannot grow" fail.
TEMP_TOLERANCE = 2.0
RAIN_DRY_FACTOR = 0.3
PH_TOLERANCE = 1.5
RAIN_WET_FACTOR = 1.6


def _label(crop, lang="en"):
    calendar = calendar_for(crop) or {}
    if lang == "mr":
        return calendar.get("label_mr") or crop
    return calendar.get("label_en") or crop


def _membership(x, lo, hi, low_soft, high_soft):
    """Smooth 0..1 fit: 1 inside the [lo, hi] plateau, tapering to 0 across the soft margin."""
    if x < lo:
        if low_soft <= 0:
            return 0.0, "low"
        return max(0.0, min(1.0, (x - (lo - low_soft)) / low_soft)), "low"
    if x > hi:
        if high_soft <= 0:
            return 0.0, "high"
        return max(0.0, min(1.0, ((hi + high_soft) - x) / high_soft)), "high"
    return 1.0, None


def evaluate(crop, temp_c=None, rain_mm=None, ph=None):
    """Return a suitability verdict for one crop at the resolved climate/soil.

    excluded -> True when the crop cannot realistically grow (temperature hard fail,
                or rainfall so far below the crop's minimum that even irrigation is
                unrealistic for the season).
    score    -> 1.0 for a clean fit, reduced for soft mismatches; used as a ranking
                penalty so the best-fitting crops rise to the top.
    """
    limits = suitability_for(crop)
    if not limits:
        return {
            "crop": crop, "excluded": False, "score": 1.0,
            "reasons": [], "reasons_mr": [], "reasons_short": [], "reasons_short_mr": [], "limits": None,
        }

    reasons, reasons_mr = [], []
    reasons_short, reasons_short_mr = [], []
    score = 1.0
    excluded = False

    tmin, tmax = limits.get("temp_min"), limits.get("temp_max")
    if temp_c is not None and tmin is not None and tmax is not None:
        if temp_c < tmin - TEMP_TOLERANCE or temp_c > tmax + TEMP_TOLERANCE:
            excluded = True
            reasons.append(
                f"{_label(crop)} needs {tmin}-{tmax}\u00b0C ({limits.get('climate_en')}); "
                f"your month averages {temp_c}\u00b0C - cannot grow here."
            )
            reasons_mr.append(
                f"{_label(crop, 'mr')}साठी {tmin}-{tmax}\u00b0C ({limits.get('climate_mr')}) लागतात; "
                f"तुमच्या महिन्याचे सरासरी {temp_c}\u00b0C आहे - येथे पिकणार नाही."
            )
            reasons_short.append(
                f"needs {tmin}-{tmax}\u00b0C, you have {temp_c}\u00b0C"
            )
            reasons_short_mr.append(
                f"{tmin}-{tmax}\u00b0C लागतात, तुमच्याकडे {temp_c}\u00b0C"
            )
        else:
            factor, _side = _membership(temp_c, tmin, tmax, TEMP_TOLERANCE, TEMP_TOLERANCE)
            if factor < 1.0:
                score *= factor
                reasons.append(
                    f"{_label(crop)} prefers {tmin}-{tmax}\u00b0C ({limits.get('climate_en')}); "
                    f"this month is {temp_c}\u00b0C."
                )
                reasons_mr.append(
                    f"{_label(crop, 'mr')}साठी {tmin}-{tmax}\u00b0C चांगले ({limits.get('climate_mr')}); "
                    f"या महिन्यात {temp_c}\u00b0C आहे."
                )

    rmin, rmax = limits.get("rain_min"), limits.get("rain_max")
    if rain_mm is not None and rmin is not None:
        if rain_mm < rmin * RAIN_DRY_FACTOR:
            excluded = True
            reasons.append(
                f"{_label(crop)} needs about {rmin}-{rmax} mm rain; this month has only {rain_mm} mm."
            )
            reasons_mr.append(
                f"{_label(crop, 'mr')}ला सुमारे {rmin}-{rmax} मिमी पाऊस लागतो; या महिन्यात फक्त {rain_mm} मिमी आहे."
            )
            reasons_short.append(f"needs {rmin} mm+ rain, you have {rain_mm} mm")
            reasons_short_mr.append(f"{rmin} मिमी+ पाऊस लागतो, तुमच्याकडे {rain_mm} मिमी")
        elif rmax is not None:
            low_soft = max(rmin * (1 - RAIN_DRY_FACTOR), 0.0)
            high_soft = max(rmax * (RAIN_WET_FACTOR - 1), 0.0)
            factor, _side = _membership(rain_mm, rmin, rmax, low_soft, high_soft)
            if factor < 1.0:
                score *= factor
                reasons.append(
                    f"Rainfall {rain_mm} mm is outside the {rmin}-{rmax} mm comfort range for {_label(crop)}."
                )
                reasons_mr.append(
                    f"पाऊस {rain_mm} मिमी हा {_label(crop, 'mr')}साठीच्या {rmin}-{rmax} मिमी मर्यादेबाहेर आहे."
                )

    pmin, pmax = limits.get("ph_min"), limits.get("ph_max")
    if ph is not None and pmin is not None and pmax is not None:
        factor, _side = _membership(ph, pmin, pmax, PH_TOLERANCE, PH_TOLERANCE)
        if factor < 1.0:
            score *= factor
            reasons.append(f"Soil pH {ph} is outside the {pmin}-{pmax} range {_label(crop)} likes.")
            reasons_mr.append(f"मातीचा सामू {ph} हा {_label(crop, 'mr')}ला आवडणाऱ्या {pmin}-{pmax} मर्यादेबाहेर आहे.")

    return {
        "crop": crop,
        "excluded": excluded,
        "score": 0.0 if excluded else round(score, 3),
        "reasons": reasons,
        "reasons_mr": reasons_mr,
        "reasons_short": reasons_short,
        "reasons_short_mr": reasons_short_mr,
        "limits": limits,
    }


def evaluate_all(temp_c=None, rain_mm=None, ph=None, crops=None):
    """{crop: verdict} for the given crops (default: every crop with a suitability entry)."""
    from cropapp.services.datasets import load_categories

    if crops is None:
        crops = []
        for category in load_categories().values():
            crops.extend(category.get("crops", []))
    return {crop: evaluate(crop, temp_c=temp_c, rain_mm=rain_mm, ph=ph) for crop in crops}
