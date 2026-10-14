from datetime import date
from types import SimpleNamespace

from conftest import fake_resolution

from cropapp.extensions import db
from cropapp.models import Farm, FarmRecord
from cropapp.services import climate_risk, economics, history, irrigation, ranking


# --------------------------------------------------------------------- helpers

def _save_farm(client, monkeypatch, name="Feature farm"):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    client.post(
        "/farms/save",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "farm_name": name, "sowing_date": "2026-10-15"},
        follow_redirects=True,
    )


def _record(kind, recorded_on, data=None, **kwargs):
    return SimpleNamespace(
        kind=kind, recorded_on=recorded_on, data=data or {},
        amount=kwargs.get("amount"), quantity=kwargs.get("quantity"),
        unit=kwargs.get("unit"), crop=kwargs.get("crop"),
        note=kwargs.get("note"), id=kwargs.get("id"),
    )


# --------------------------------------------------------------------- ranking

def test_ranking_separates_model_and_agronomic_scores():
    features = {"N": 90, "P": 42, "K": 43, "temperature": 28, "humidity": 70, "ph": 6.5, "rainfall": 180}
    rows = ranking.build(features, top_n=5)
    assert rows
    scores = [row["combined_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    for row in rows:
        assert 0.0 <= row["model_probability"] <= 1.0
        assert 0.0 <= row["agronomic_score"] <= 1.0
        assert row["water_penalty"] > 0
        assert abs(row["combined_score"] - round(
            row["model_probability"] * row["agronomic_score"] * row["water_penalty"], 4)) < 1e-6
        assert row["explanation"]["model_note_mr"]


def test_ranking_ranks_are_sequential_and_respect_exclude():
    features = {"N": 90, "P": 42, "K": 43, "temperature": 28, "humidity": 70, "ph": 6.5, "rainfall": 180}
    rows = ranking.build(features, top_n=5)
    assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    blocked = rows[0]["crop"]
    filtered = ranking.build(features, exclude={blocked}, top_n=5)
    assert blocked not in {row["crop"] for row in filtered}


def test_ranking_water_shortage_reduces_penalty():
    features = {"N": 90, "P": 42, "K": 43, "temperature": 28, "humidity": 70, "ph": 6.5, "rainfall": 180}
    crop = "rice"
    report = {"known": True, "sufficient": False, "days_per_week": 1, "need": "high", "source_label": "Open well"}
    rows = ranking.build(features, allowed={crop}, water_reports={crop: report}, top_n=1)
    assert rows[0]["water_penalty"] < 1.0
    assert rows[0]["explanation"]["risks_en"]


# --------------------------------------------------------------------- economics

def test_profitability_revenue_and_net_relationships():
    market = {"price": 2300, "revenue": {"yield_t_ha": 4.0, "area_acres": 2.0}}
    est = economics.profitability("rice", area_acres=2.0, market=market, scenario="expected")
    assert est["available"] is True
    # revenue per ha = price(Rs/qtl) x yield(t/ha) x 10 quintals/t
    assert est["revenue"]["per_ha"] == 92000
    assert est["costs"]["per_ha"] > 0
    assert est["net"]["per_ha"] == est["revenue"]["per_ha"] - est["costs"]["per_ha"]
    assert est["net"]["total"] == round(est["net"]["per_acre"] * 2.0)
    assert est["disclaimer_mr"] and est["assumptions_en"]


def test_profitability_unavailable_without_price():
    est = economics.profitability("not_a_crop", market={})
    assert est["available"] is False
    assert est["reason_mr"]


def test_scenarios_ordered_worst_expected_best():
    market = {"price": 2300, "revenue": {"yield_t_ha": 4.0}}
    est = economics.scenarios_for("rice", market=market)
    assert set(est) >= {"worst", "expected", "best"}
    assert est["worst"]["net"]["per_ha"] <= est["expected"]["net"]["per_ha"] <= est["best"]["net"]["per_ha"]


def test_compare_profitability_sorted_by_net_per_acre():
    contexts = {
        "rice": {"market": {"price": 2300, "revenue": {"yield_t_ha": 4.0}}},
        "cotton": {"market": {"price": 7000, "revenue": {"yield_t_ha": 1.5}}},
    }
    rows = economics.compare_profitability(["rice", "cotton"], area_acres=1.0, contexts=contexts)
    nets = [row["net"]["per_acre"] for row in rows if row["available"]]
    assert nets == sorted(nets, reverse=True)


# --------------------------------------------------------------------- irrigation

def test_irrigation_plan_events_and_cost():
    water = {"known": True, "sufficient": True, "interval_days": 7, "need": "high", "source_label": "Open well"}
    plan = irrigation.plan("rice", water=water, area_acres=2.0, rainfall_normal_mm=100, source="well")
    assert plan["available"] is True
    assert plan["events"] > 0
    assert plan["cost"]["per_acre"] > 0
    assert plan["cost"]["total"] == round(plan["cost"]["per_acre"] * 2.0)
    assert plan["suggestions_mr"]


def test_irrigation_compare_lowest_water_first():
    rows = irrigation.compare(["rice", "wheat", "maize"])
    # Crops with known water data come first, ordered by lowest seasonal need.
    known = [row["season_mm"] for row in rows if row["available"]]
    assert known == sorted(known)
    assert all(row["available"] or not row["season_mm"] for row in rows)


# --------------------------------------------------------------------- climate risk

def test_climate_risk_flags_deficit_and_heat():
    resolved = {"climate": {
        "temp_c": 39.0, "humidity_pct": 88.0,
        "rainfall_normal_mm": 120.0, "rainfall_actual_mm": 30.0,
        "rainfall_normal_is_state_average": False,
    }}
    report = climate_risk.assess(resolved, "rice")
    codes = {risk["code"] for risk in report["risks"]}
    assert "rainfall_deficit" in codes
    assert "heat_stress" in codes
    assert report["level"] == "high"
    assert report["data_quality"]["forecast_available"] is False


def test_climate_risk_missing_data_is_graceful():
    report = climate_risk.assess({"climate": {}}, "rice")
    assert report["risks"] == []
    assert report["missing"]
    assert report["level"] == "low"


# --------------------------------------------------------------------- history

def test_history_soil_expense_harvest_summaries():
    records = [
        _record("soil", date(2026, 6, 1), {"ph": 6.8, "n": 40, "p": 35, "k": 45}),
        _record("soil", date(2026, 8, 1), {"ph": 7.1, "n": 50, "p": 30, "k": 40}),
        _record("expense", date(2026, 6, 5), {"category": "seed"}, amount=4000),
        _record("expense", date(2026, 7, 5), {"category": "labour"}, amount=6000),
        _record("harvest", date(2026, 11, 1), crop="rice", quantity=2500, unit="kg", amount=58000),
    ]
    soil = history.soil_series(records)
    assert [point["ph"] for point in soil] == [6.8, 7.1]
    expenses = history.expense_summary(records)
    assert expenses["total"] == 10000
    assert expenses["by_category"]["seed"] == 4000
    harvest = history.harvest_summary(records)
    assert harvest["count"] == 1
    assert harvest["total_quantity"] == 2500
    assert harvest["total_revenue"] == 58000


def test_history_actual_vs_predicted():
    recommendation = SimpleNamespace(
        crop="rice",
        msp={"price": 2300, "revenue": {"yield_t_ha": 4.0, "total": 92000, "area_acres": 1.0}},
    )
    harvest = {"revenue": 80000, "quantity": 3200, "sell_price": 2100}
    comparison = history.actual_vs_predicted(recommendation, harvest)
    assert comparison["available"] is True
    assert comparison["revenue"]["delta_pct"] < 0
    assert comparison["yield_t_ha"]["actual"] == 3.2


# --------------------------------------------------------------------- routes

def test_add_and_delete_record_route(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id

    auth_client.post(
        f"/farms/{farm_id}/records",
        data={"kind": "soil", "recorded_on": "2026-09-01", "ph": "6.4", "n": "38", "p": "30", "k": "42"},
        follow_redirects=True,
    )
    with app.app_context():
        record = FarmRecord.query.one()
        assert record.kind == "soil"
        assert record.data["ph"] == 6.4
        # A soil record updates the farm's stored soil snapshot.
        assert db.session.get(Farm, farm_id).soil_ph == 6.4
        record_id = record.id

    auth_client.post(f"/farms/{farm_id}/records/{record_id}/delete", follow_redirects=True)
    with app.app_context():
        assert FarmRecord.query.count() == 0


def test_records_are_isolated_per_user(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id

    other = app.test_client()
    other.post("/account/register", data={"email": "other@example.com", "password": "secret12",
                                          "name": "Other", "confirm": "secret12"}, follow_redirects=True)
    other.post("/account/login", data={"email": "other@example.com", "password": "secret12"}, follow_redirects=True)
    response = other.get(f"/farms/{farm_id}")
    assert response.status_code == 404


def test_simulate_route_renders(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id

    response = auth_client.get(f"/farms/{farm_id}/simulate")
    assert response.status_code == 200
    assert b"Modified" in response.data

    response = auth_client.post(
        f"/farms/{farm_id}/simulate",
        data={"rainfall_mm": "20", "water_days": "1", "price_factor": "0.8", "yield_factor": "0.7",
              "fertilizer_cost_factor": "1.2", "scenario": "worst"},
    )
    assert response.status_code == 200
