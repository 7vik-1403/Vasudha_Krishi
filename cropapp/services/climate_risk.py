"""Seasonal climate-risk assessment.

Uses the IMD monthly rainfall normal (historical average) and the most recent
observed month from the resolver, plus the crop's own suitability windows, to flag
rainfall deficit/excess, dry spells, heat and cold stress. There is no live
forecast in this deployment, so the module labels every risk with its basis
(normal / recent / derived) and says so rather than inventing a forecast.
"""

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _risk(code, level, basis, title_en, title_mr, detail_en, detail_mr):
    return {
        "code": code, "level": level, "basis": basis,
        "title_en": title_en, "title_mr": title_mr,
        "detail_en": detail_en, "detail_mr": detail_mr,
    }


def assess(resolved, crop=None):
    climate = (resolved or {}).get("climate") or {}
    temp = _num(climate.get("temp_c"))
    humidity = _num(climate.get("humidity_pct"))
    normal = _num(climate.get("rainfall_normal_mm"))
    recent = _num(climate.get("rainfall_actual_mm"))
    state_avg = bool(climate.get("rainfall_normal_is_state_average"))

    risks = []
    missing = []

    if normal is None:
        missing.append(("rainfall_normal", "Monthly rainfall normal is not available for this district.",
                        "या जिल्ह्यासाठी महिन्याचा पाऊस सरासरी उपलब्ध नाही."))
    else:
        if recent is not None and normal > 0:
            ratio = recent / normal
            if ratio < 0.7:
                level = "high" if ratio < 0.5 else "medium"
                risks.append(_risk(
                    "rainfall_deficit", level, "recent",
                    "Rainfall deficit vs normal", "सरासरीपेक्षा कमी पाऊस",
                    f"Recent month had {recent:.0f} mm against a {normal:.0f} mm normal ({ratio*100:.0f}%). "
                    "Irrigated crops need back-up water; rain-fed sowing is risky.",
                    f"अलीकडच्या महिन्यात {recent:.0f} मिमी पाऊस, सरासरी {normal:.0f} मिमी ({ratio*100:.0f}%). "
                    "सिंचित पिकांना पर्यायी पाणी हवे; पर्जन्यावर पेरणी धोकादायक.",
                ))
            elif ratio > 1.5:
                risks.append(_risk(
                    "rainfall_excess", "medium", "recent",
                    "Excessive rainfall vs normal", "सरासरीपेक्षा जास्त पाऊस",
                    f"Recent month had {recent:.0f} mm against a {normal:.0f} mm normal - watch for waterlogging, "
                    "drainage and fungal disease.",
                    f"अलीकडच्या महिन्यात {recent:.0f} मिमी पाऊस, सरासरी {normal:.0f} मिमी - पाणी साचणे, निचरा व बुरशीजन्य रोग यांवर लक्ष ठेवा.",
                ))
        if normal < 25:
            risks.append(_risk(
                "dry_month", "medium", "normal",
                "Dry month on average", "सरासरी कोरडा महिना",
                f"The monthly rainfall normal is only {normal:.0f} mm - plan irrigation and mulch.",
                f"महिन्याचा पाऊस सरासरी फक्त {normal:.0f} मिमी आहे - सिंचन व मल्चिंगची योजना करा.",
            ))

    if temp is None:
        missing.append(("temperature", "Temperature reading is not available.", "तापमान माहिती उपलब्ध नाही."))
    elif temp >= 35:
        risks.append(_risk(
            "heat_stress", "high" if temp >= 38 else "medium", "recent",
            "Heat stress risk", "उष्णतेचा ताण",
            f"Temperature is {temp:.1f}°C - flowering, pollination and fruit set can suffer; irrigate in the evening.",
            f"तापमान {temp:.1f}°C आहे - फुलोरा, परागीभवन व फळधारणेवर परिणाम होऊ शकतो; संध्याकाळी सिंचन करा.",
        ))
    elif temp <= 10:
        risks.append(_risk(
            "cold_stress", "high" if temp <= 5 else "medium", "recent",
            "Cold / frost risk", "थंडी / दंव धोका",
            f"Temperature is {temp:.1f}°C - sensitive crops can be damaged; avoid late irrigation.",
            f"तापमान {temp:.1f}°C आहे - संवेदनशील पिकांचे नुकसान होऊ शकते; उशिरा सिंचन टाळा.",
        ))

    if humidity is not None and humidity >= 85:
        risks.append(_risk(
            "high_humidity", "medium", "recent",
            "High humidity - disease pressure", "जास्त आर्द्रता - रोगाचा धोका",
            f"Humidity is {humidity:.0f}% - fungal and bacterial diseases spread faster; scout regularly.",
            f"आर्द्रता {humidity:.0f}% आहे - बुरशीजन्य व जिवाणूजन्य रोग लवकर पसरतात; नियमित निरीक्षण करा.",
        ))

    crop_risks, missing_crop = _crop_risks(crop, temp, normal, recent)
    risks.extend(crop_risks)
    missing.extend(missing_crop)

    level = "low"
    for risk in risks:
        if LEVEL_ORDER.get(risk["level"], 0) > LEVEL_ORDER[level]:
            level = risk["level"]

    if not risks:
        summary_en = "No major seasonal climate risk flagged for this month from the available data."
        summary_mr = "उपलब्ध माहितीनुसार या महिन्यासाठी मोठा हंगामी हवामान धोका आढळला नाही."
    elif level == "high":
        summary_en = "High climate risk this month - review water and sowing plans carefully."
        summary_mr = "या महिन्यात जास्त हवामान धोका आहे - पाणी व पेरणी योजना काळजीपूर्वक तपासा."
    else:
        summary_en = "Some seasonal climate risks to plan around."
        summary_mr = "काही हंगामी हवामान धोके लक्षात घेऊन नियोजन करा."

    return {
        "risks": risks,
        "level": level,
        "summary_en": summary_en,
        "summary_mr": summary_mr,
        "missing": [{"code": code, "detail_en": en, "detail_mr": mr} for code, en, mr in missing],
        "data_quality": {
            "forecast_available": False,
            "normal_is_state_average": state_avg,
            "note_en": "Based on IMD monthly normals and the most recent observed month - no live forecast is available here.",
            "note_mr": "IMD महिन्याच्या सरासरी व अलीकडच्या निरीक्षणावर आधारित - येथे थेट अंदाज उपलब्ध नाही.",
        },
    }


def _crop_risks(crop, temp, normal, recent):
    from cropapp.services.datasets import suitability_for

    risks = []
    missing = []
    limits = suitability_for(crop) if crop else None
    if not limits:
        if crop:
            missing.append(("crop_window", "No crop-specific climate window on file; generic risk only.",
                            "या पिकासाठी विशिष्ट हवामान मर्यादा नाही; फक्त सामान्य धोका."))
        return risks, missing

    label = (limits.get("climate_en") or "").strip()
    tmin, tmax = _num(limits.get("temp_min")), _num(limits.get("temp_max"))
    if temp is not None and tmin is not None and tmax is not None and (temp < tmin or temp > tmax):
        risks.append(_risk(
            "crop_temperature", "medium", "derived",
            "Temperature outside the crop's comfort window", "तापमान पिकाच्या अनुकूल मर्यादेबाहेर",
            f"This crop prefers {tmin:.0f}-{tmax:.0f}°C; this month is {temp:.0f}°C. {label}.",
            f"हे पीक {tmin:.0f}-{tmax:.0f}°C पसंत करते; या महिन्यात {temp:.0f}°C आहे.",
        ))
    rain_value = recent if recent is not None else normal
    rmin, rmax = _num(limits.get("rain_min")), _num(limits.get("rain_max"))
    if rain_value is not None and rmin is not None and rmax is not None and (rain_value < rmin or rain_value > rmax):
        risks.append(_risk(
            "crop_rainfall", "medium", "derived",
            "Rainfall outside the crop's comfort window", "पाऊस पिकाच्या अनुकूल मर्यादेबाहेर",
            f"This crop prefers about {rmin:.0f}-{rmax:.0f} mm; available figure is {rain_value:.0f} mm.",
            f"हे पीक सुमारे {rmin:.0f}-{rmax:.0f} मिमी पसंत करते; उपलब्ध आकडा {rain_value:.0f} मिमी आहे.",
        ))
    return risks, missing
