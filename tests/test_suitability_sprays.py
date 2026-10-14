from cropapp.services.datasets import load_calendar
from cropapp.services.fertilizer import compute_fertilizer, compute_vegetable_fertilizer
from cropapp.services.scheduler import _calendar_from_vegetable
from cropapp.services.suitability import evaluate, evaluate_all
from cropapp.services.sprays import plan


# --------------------------------------------------------------------- suitability

def test_suitability_hard_excludes_temperate_crop_in_heat():
    verdict = evaluate("apple", temp_c=32.0, rain_mm=180.0, ph=6.5)
    assert verdict["excluded"] is True
    assert verdict["score"] < 1.0
    assert verdict["reasons"] and verdict["reasons_mr"]
    assert verdict["reasons_short"] and verdict["reasons_short_mr"]


def test_suitability_clean_fit_scores_one():
    verdict = evaluate("rice", temp_c=28.0, rain_mm=200.0, ph=6.5)
    assert verdict["excluded"] is False
    assert verdict["score"] == 1.0
    assert verdict["reasons"] == []


def test_suitability_dry_rain_hard_excludes():
    # rice needs >= 60 mm; anything under 30% of that is treated as a hard fail.
    verdict = evaluate("rice", temp_c=28.0, rain_mm=10.0, ph=7.0)
    assert verdict["excluded"] is True
    assert any("rain" in reason.lower() for reason in verdict["reasons"])


def test_suitability_soft_temp_penalty_not_excluded():
    # 39 C is 1 degree over rice's 38 C window but within the 2 C tolerance.
    verdict = evaluate("rice", temp_c=39.0, rain_mm=200.0, ph=7.0)
    assert verdict["excluded"] is False
    assert verdict["score"] < 1.0


def test_suitability_ph_penalty():
    verdict = evaluate("rice", temp_c=28.0, rain_mm=200.0, ph=8.6)
    assert verdict["excluded"] is False
    assert verdict["score"] < 1.0
    assert any("pH" in reason or "सामू" in reason for reason in verdict["reasons"])


def test_suitability_unknown_crop_is_neutral():
    verdict = evaluate("not_a_crop", temp_c=50.0, rain_mm=0.0, ph=1.0)
    assert verdict["excluded"] is False
    assert verdict["score"] == 1.0
    assert verdict["limits"] is None


def test_evaluate_all_covers_requested_crops():
    report = evaluate_all(temp_c=32.0, rain_mm=180.0, ph=7.0, crops=["apple", "rice", "coffee"])
    assert set(report) == {"apple", "rice", "coffee"}
    assert report["apple"]["excluded"] is True
    assert report["rice"]["excluded"] is False


# --------------------------------------------------------------------- sprays

def test_spray_plan_prices_and_alternatives():
    calendar = load_calendar()["rice"]
    result = plan("rice", calendar, area_acres=2.0)
    assert result["available"] is True
    assert result["has_cost"] is True
    assert len(result["rows"]) == len(calendar["pests"])
    assert result["totals"]["per_acre"] > 0
    assert abs(result["totals"]["total"] - result["totals"]["per_acre"] * 2.0) <= 1.0
    first = result["rows"][0]
    assert first["primary"]["cost_per_acre"] > 0
    assert first["primary"]["name_en"]
    assert result["savings"]["per_acre"] >= 0


def test_spray_plan_marks_cheaper_only_when_truly_cheaper():
    # wheat rust: propiconazole -> sulphur, which is genuinely cheaper.
    calendar = load_calendar()["wheat"]
    result = plan("wheat", calendar, area_acres=1.0)
    rust = next(row for row in result["rows"] if row["alt"] and row["alt_cheaper"])
    assert rust["alt"]["cost_per_ha"] < rust["primary"]["cost_per_ha"]
    assert result["savings"]["per_acre"] > 0


def test_spray_plan_vegetable_has_no_bundled_prices():
    calendar = _calendar_from_vegetable("tomato")
    result = plan("tomato", calendar, area_acres=1.0)
    assert result["available"] is True
    assert result["has_cost"] is False
    assert all(row["primary"] is None for row in result["rows"])


def test_spray_plan_unknown_crop_is_empty():
    result = plan("not_a_crop", {})
    assert result["available"] is False
    assert result["rows"] == []


# --------------------------------------------------------------------- fertilizer cost

def test_fertilizer_cost_options_and_savings():
    dose = compute_fertilizer("rice", {"n": 40.0, "p": 35.0, "k": 45.0}, area_acres=2.5, npk_measured=True)
    cost = dose["cost"]
    assert len(cost["options"]) == 2
    assert sum(1 for option in cost["options"] if option["is_cheapest"]) == 1
    assert cost["standard"]["cost_per_acre"] > 0
    # The economy plan delivers the same nutrients and should never cost more.
    assert cost["economy"]["cost_per_acre"] <= cost["standard"]["cost_per_acre"]
    assert cost["savings"]["per_acre"] == cost["standard"]["cost_per_acre"] - cost["economy"]["cost_per_acre"]
    assert cost["savings"]["total"] == round(cost["savings"]["per_acre"] * 2.5)
    labels = [line["label_en"] for line in cost["standard"]["lines"]]
    assert "Urea (46-0-0)" in labels


def test_vegetable_fertilizer_cost_attached():
    from cropapp.services.datasets import load_vegetables

    tomato = load_vegetables()["tomato"]
    dose = compute_vegetable_fertilizer(tomato, area_acres=1.0)
    assert dose["cost"]["standard"]["cost_per_acre"] > 0
    assert dose["cost"]["savings"]["per_acre"] >= 0
