"""Water availability: what the farmer can actually supply vs what the crop needs.

Rainfall figures describe the climate of the place - they do NOT tell us whether a
particular farm has irrigation. The farmer enters their water source and how many
days a week they can irrigate, and this module compares that with the crop's need.
"""

from cropapp.services.datasets import load_vegetables, water_for

NEED_DAYS_PER_WEEK = {"low": 1, "medium": 3, "high": 5}
NEED_LABELS = {"low": "Low", "medium": "Medium", "high": "High"}
NEED_LABELS_MR = {"low": "कमी", "medium": "मध्यम", "high": "जास्त"}

SOURCES = {
    "rainfed": ("Rain-fed only (no irrigation)", "केवळ पाऊसावर (सिंचन नाही)"),
    "well": ("Open well", "उन्हाळी विहीर"),
    "borewell": ("Borewell", "बोअरवेल"),
    "canal": ("Canal", "कालवा"),
    "river": ("River / stream", "नदी / ओढा"),
    "tank": ("Farm pond / tank", "तलाव / टाक"),
    "drip": ("Drip / sprinkler", "ठिबक / फवारणी"),
    "other": ("Other source", "इतर स्रोत"),
}

GENERIC_NOTE_EN = (
    "Rainfall is a climate average for the district - it does not confirm water for your field. "
    "Enter your actual irrigation source and how many days a week you can irrigate."
)
GENERIC_NOTE_MR = (
    "हवामान सरासरी ही जिल्ह्याची माहिती आहे - ती तुमच्या शेतासाठी पाणी ठरवत नाही. "
    "तुमचा खरा सिंचन स्रोत आणि आठवड्यातून किती दिवस सिंचन करता येते ते नोंदवा."
)


def source_label(source, language="en"):
    labels = SOURCES.get(source) or SOURCES["other"]
    return labels[1] if language == "mr" else labels[0]


def evaluate(crop, source=None, days=None, rainfall_normal=None, fallback_need=None, fallback_interval=None):
    """Build the water report for one crop on one farm."""
    water = water_for(crop)
    vegetables = load_vegetables().get((crop or "").strip().lower()) if not water else None
    need = (water or {}).get("need") or (vegetables or {}).get("water_need") or fallback_need or "medium"
    interval = (water or {}).get("interval_days") or (vegetables or {}).get("interval_days") or fallback_interval or 7
    required = NEED_DAYS_PER_WEEK.get(need, 3)

    source = source if source in SOURCES else None
    known = source is not None

    if source is None:
        supplied = None
        sufficient = None
        gap = None
    elif source == "rainfed":
        supplied = 0
        sufficient = required <= 1
        gap = max(0, required - 0)
    else:
        supplied = int(days) if days is not None else required
        supplied = max(0, min(7, supplied))
        sufficient = supplied >= required
        gap = max(0, required - supplied)

    if supplied is None:
        interval_days = interval
        warnings = [GENERIC_NOTE_EN]
        warnings_mr = [GENERIC_NOTE_MR]
        status = "unknown"
        advice_en = GENERIC_NOTE_EN
        advice_mr = GENERIC_NOTE_MR
    elif sufficient:
        interval_days = interval
        warnings = []
        warnings_mr = []
        status = "ok"
        advice_en = (
            f"{source_label(source)} covers this crop's {NEED_LABELS[need].lower()} water need "
            f"(about {required} irrigations a week). Keep roughly {interval_days} days between irrigations."
        )
        advice_mr = (
            f"{source_label(source, 'mr')} या पिकाची {NEED_LABELS_MR[need]} पाण्याची गरज "
            f"(आठवड्यातून अंदाजे {required} वेळा सिंचन) पूर्ण करते. "
            f"सिंचनांत अंदाजे {mr_days(interval_days)} दिवसांचा अंतर ठेवा."
        )
    else:
        # Stretch the irrigation gap to the water actually available.
        interval_days = max(3, round(interval * required / max(supplied, 1)))
        status = "short"
        warnings = [
            f"Water gap: this crop wants about {required} irrigation days a week but you reported "
            f"{supplied} via {source_label(source).lower()}. Irrigations will stretch to about "
            f"{interval_days} days apart - yield may drop."
        ]
        warnings_mr = [
            f"पाण्याची कमतरता: या पिकाला आठवड्यातून अंदाजे {required} सिंचन दिवस हवेत, "
            f"परंतु तुम्ही {source_label(source, 'mr')} मार्गाने {supplied} दिवस देता. "
            f"सिंचन अंदाजे {mr_days(interval_days)} दिवसांनी टाकावे लागेल - उत्पादन कमी होऊ शकते."
        ]
        advice_en = (
            f"Water is tight for this crop. Options: shift to a crop needing less water, "
            f"adopt mulch / drip to stretch {supplied} irrigation days, or arrange extra supply."
        )
        advice_mr = (
            f"या पिकासाठी पाणी कमी पडत आहे. पर्याय: कमी पाणी लागणारे पीक निवडा, "
            f"मल्चिंग/ठिबक सिंचनाने {supplied} दिवस वाढवा किंवा अधिक पाणी उपलब्ध करा."
        )
    if source == "rainfed" and need in ("medium", "high") and rainfall_normal is not None:
        warnings.append(
            f"Rain-fed fields rely entirely on rainfall ({rainfall_normal} mm normal for the month) - "
            "arrange irrigation before taking up a {need}-water crop.".format(need=need)
        )
        warnings_mr.append(
            f"पाऊसावर अवलंबून शेतासाठी पाऊसच (सरासरी {mr_days(rainfall_normal)} मि.मी.) आधार आहे - "
            f"{NEED_LABELS_MR[need]} पाणी लागणारे पीक घेण्यापूर्वी सिंचनाची व्यवस्था करा."
        )

    return {
        "known": known,
        "status": status,
        "source": source,
        "source_label": source_label(source) if source else None,
        "source_label_mr": source_label(source, "mr") if source else None,
        "days_per_week": supplied,
        "need": need,
        "need_label": NEED_LABELS[need],
        "need_label_mr": NEED_LABELS_MR[need],
        "required_days": required,
        "sufficient": sufficient,
        "gap_days": gap,
        "interval_days": interval_days,
        "base_interval_days": interval,
        "season_mm": (water or {}).get("season_mm"),
        "advice_en": advice_en,
        "advice_mr": advice_mr,
        "warnings": warnings,
        "warnings_mr": warnings_mr,
        "generic_note_en": GENERIC_NOTE_EN,
        "generic_note_mr": GENERIC_NOTE_MR,
    }


def penalty_for(report):
    """Probability multiplier used when re-ranking crops by water fit."""
    if not report or not report.get("known"):
        return 1.0
    if report.get("sufficient"):
        return 1.0
    if report.get("days_per_week") == 0:
        return 0.15 if report.get("need") == "high" else 0.4
    return 0.35


def mr_days(value):
    return str(value).translate(str.maketrans("0123456789.", "०१२३४५६७८९."))
