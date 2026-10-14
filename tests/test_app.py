import json
from datetime import date
from types import SimpleNamespace

import pytest

from conftest import fake_resolution
from cropapp.extensions import db
from cropapp.models import Farm, FarmTask, Recommendation, User
from cropapp.services.datasets import msp_for
from cropapp.services.fertilizer import compute_fertilizer
from cropapp.services.scheduler import build_tasks
from utils.geo import find_district, match_district


def test_landing_page_for_anonymous(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Vasudha Krishi" in response.data
    assert b"login-email" in response.data
    assert b'id="map"' not in response.data


def test_recommend_requires_login(app, auth_client):
    anon = app.test_client()
    response = anon.get("/recommend")
    assert response.status_code == 302
    assert "/account/login" in response.headers["Location"]

    response = auth_client.get("/recommend")
    assert response.status_code == 200
    assert b'id="map"' in response.data
    assert b"predict-form" in response.data
    assert b'name="category"' in response.data
    assert b'name="water_source"' in response.data


def test_dashboard_for_signed_in_user(auth_client):
    response = auth_client.get("/")
    assert response.status_code == 200
    assert b"New recommendation" in response.data
    assert b"No farms yet" in response.data


def test_register_login_logout(client):
    response = client.post(
        "/account/register",
        data={"name": "Asha", "email": "asha@example.com", "password": "secret12", "confirm": "secret12"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert User.query.filter_by(email="asha@example.com").count() == 1

    response = client.post("/account/logout", follow_redirects=True)
    assert response.status_code == 200

    response = client.post("/account/login", data={"email": "asha@example.com", "password": "wrong"})
    assert response.status_code == 401

    response = client.post(
        "/account/login", data={"email": "asha@example.com", "password": "secret12"}, follow_redirects=True
    )
    assert response.status_code == 200


def test_register_rejects_duplicate_and_short_password(client):
    data = {"name": "Asha", "email": "asha@example.com", "password": "secret12", "confirm": "secret12"}
    created = client.post("/account/register", data=data)
    assert created.status_code == 302
    client.post("/account/logout")

    duplicate = client.post("/account/register", data=data)
    assert duplicate.status_code == 400
    assert b"already exists" in duplicate.data

    short = client.post(
        "/account/register",
        data={"name": "B", "email": "b@example.com", "password": "123", "confirm": "123"},
    )
    assert short.status_code == 400
    assert b"at least 6" in short.data

    with client.application.app_context():
        assert User.query.count() == 1


def test_resolve_rejects_outside_india(client):
    response = client.get("/api/resolve?lat=40.7&lon=-74.0&month=9")
    assert response.status_code == 400
    assert "outside India" in response.get_json()["error"]


def test_resolve_requires_location(client):
    response = client.get("/api/resolve")
    assert response.status_code == 400


def test_predict_and_save_farm(auth_client, monkeypatch):
    monkeypatch.setattr("cropapp.routes.main.resolve", lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), month or 9))

    response = auth_client.post(
        "/recommend",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "n": "40", "p": "35", "k": "45", "ph": "7.2"},
    )
    assert response.status_code == 200
    assert b"Fertilizer dose" in response.data
    assert b"Save this field" in response.data
    assert b'name="category"' in response.data

    response = auth_client.post(
        "/farms/save",
        data={
            "lat": "30.806",
            "lon": "75.835",
            "month": "9",
            "n": "40",
            "p": "35",
            "k": "45",
            "ph": "7.2",
            "farm_name": "North plot",
            "area_acres": "2.5",
            "sowing_date": "2026-10-15",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"North plot" in response.data

    with auth_client.application.app_context():
        farm = Farm.query.one()
        recommendation = Recommendation.query.one()
        tasks = FarmTask.query.all()
        assert farm.district == "Ludhiana"
        assert farm.soil_n == 40.0
        assert farm.crop == recommendation.crop
        assert len(tasks) >= 4
        kinds = {task.kind for task in tasks}
        assert "fertilizer" in kinds and "harvest" in kinds
        assert all(task.due_date is not None for task in tasks)
        assert any(task.source == "schedule" for task in tasks)


def test_task_toggle_and_delete(auth_client, monkeypatch):
    monkeypatch.setattr("cropapp.routes.main.resolve", lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), month or 9))
    auth_client.post(
        "/farms/save",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "farm_name": "Plot A", "sowing_date": "2026-10-15"},
    )
    with auth_client.application.app_context():
        task = FarmTask.query.first()
        task_id = task.id
        farm_id = task.farm_id

    response = auth_client.post(f"/tasks/{task_id}/toggle", follow_redirects=True)
    assert response.status_code == 200
    with auth_client.application.app_context():
        assert db.session.get(FarmTask, task_id).status == "done"

    response = auth_client.post(f"/farms/{farm_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with auth_client.application.app_context():
        assert db.session.get(Farm, farm_id) is None
        assert FarmTask.query.count() == 0


def test_chat_without_key_returns_guidance(client):
    response = client.post("/api/chat", json={"message": "How much urea for rice?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "missing_key"
    assert "GEMINI_API_KEY" in payload["reply"]


def test_chat_requires_message(client):
    assert client.post("/api/chat", json={"message": "  "}).status_code == 400


def test_fertilizer_dose_maths():
    result = compute_fertilizer(
        "rice",
        {"n": 40.0, "p": 35.0, "k": 45.0},
        area_acres=2.5,
        npk_measured=True,
    )
    assert result["available"] is True
    requirement = result["requirement_kg_ha"]
    deficit_n = max(0.0, requirement["n"] - 40.0)
    expected_urea = round((deficit_n / 0.60) / 0.46, 1)
    assert result["products"]["urea_kg_ha"] == expected_urea
    assert result["per_acre"]["urea"] == round(expected_urea / 2.47105, 1)
    assert result["total_for_farm"]["urea"] == round(result["per_acre"]["urea"] * 2.5, 1)
    assert result["plan"], "rice should get staged fertilizer applications"
    assert abs(sum(stage["n_share"] for stage in result["plan"]) - 1.0) < 1e-6


def test_fertilizer_zero_dose_when_soil_sufficient():
    result = compute_fertilizer("rice", {"n": 999.0, "p": 999.0, "k": 999.0}, npk_measured=True)
    assert result["products"] == {"urea_kg_ha": 0.0, "dap_kg_ha": 0.0, "mop_kg_ha": 0.0}
    assert any("no dose required" in note for note in result["notes"])


def test_scheduler_builds_dated_tasks(app):
    with app.app_context():
        user = User(email="sched@example.com", name="Sched")
        user.set_password("secret12")
        db.session.add(user)
        db.session.flush()
        farm = Farm(owner=user, name="Farm", lat=30.8, lon=75.8, district="Ludhiana", state="Punjab")
        db.session.add(farm)
        db.session.flush()
        recommendation = Recommendation(
            owner=user, farm=farm, features_json="{}", crop="rice", confidence=0.9,
            fertilizer_json=json.dumps(compute_fertilizer("rice", {"n": 40, "p": 35, "k": 45}, npk_measured=True)),
        )
        db.session.add(recommendation)
        db.session.flush()

        sowing = date(2026, 6, 15)
        tasks = list(build_tasks(farm, recommendation, sowing=sowing))
        assert len(tasks) >= 4
        assert min(task.due_date for task in tasks) == sowing
        harvest = [task for task in tasks if task.kind == "harvest"]
        assert harvest and harvest[0].due_date == date(2026, 6, 15) + __import__("datetime").timedelta(days=125)
        assert any(task.kind == "pesticide" for task in tasks)
        assert all(task.title_mr for task in tasks)
        assert any("kg/ha" in task.title_en for task in tasks)


def test_msp_lookup():
    assert msp_for("rice")["price"] == 2300.0
    assert msp_for("apple")["price"] is None
    assert msp_for("apple")["note"]


def test_district_matching_uses_current_and_legacy_names():
    assert find_district("Gurugram", "Haryana")["district"] == "Gurugram"
    assert find_district("GURGAON", "HARYANA")["district"] == "Gurugram"
    assert find_district("ALLAHABAD", "UTTAR PRADESH")["district"] == "Prayagraj"
    assert find_district("NEW DELHI", "DELHI")["district"] == "Delhi"


def test_districts_api(client):
    payload = client.get("/api/districts?state=Punjab").get_json()
    assert payload["ok"]
    names = [row["district"] for row in payload["data"]]
    assert "Ludhiana" in names
    assert all(row["state"] == "Punjab" for row in payload["data"])


def test_imd_rainfall_normal_and_state_fallback():
    from utils.climate import imd_rainfall_normal

    normal = imd_rainfall_normal("Ludhiana", "Punjab", 7)
    assert normal and normal["value_mm"] > 0
    assert normal["source"].startswith("IMD")

    fallback = imd_rainfall_normal("Balod", "Chhattisgarh", 7)
    assert fallback and fallback["is_state_average"]


class FakeGeminiResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


def _save_farm(client, monkeypatch, name="North plot"):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    client.post(
        "/farms/save",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "farm_name": name, "sowing_date": "2026-10-15"},
        follow_redirects=True,
    )


def test_chat_schedules_task_with_mocked_gemini(app, auth_client, monkeypatch):
    from cropapp.services import ai

    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id

    app.config["GEMINI_API_KEY"] = "test-key"
    reply = json.dumps(
        {
            "reply": "Apply urea in two splits.",
            "actions": [
                {
                    "type": "add_task",
                    "farm_id": farm_id,
                    "kind": "fertilizer",
                    "title_en": "Urea top dressing",
                    "title_mr": "urea पुरवठा",
                    "detail_en": "45 kg/ha urea",
                    "detail_mr": "45 kg/ha",
                    "due_date": "2026-10-20",
                }
            ],
        }
    )
    monkeypatch.setattr(ai, "requests", SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse(reply)))

    data = auth_client.post("/api/chat", json={"message": "Schedule urea top dressing"}).get_json()
    assert data["ok"] is True
    assert "Scheduled 1 task" in data["reply"]
    assert data["tasks"][0]["title"] == "Urea top dressing"

    with app.app_context():
        task = FarmTask.query.filter_by(source="ai").one()
        assert task.source == "ai"
        assert task.status == "pending"
        assert task.due_date.isoformat() == "2026-10-20"
        assert task.kind == "fertilizer"


def test_chat_rejects_actions_for_other_farms(app, auth_client, monkeypatch):
    from cropapp.services import ai

    _save_farm(auth_client, monkeypatch, name="My farm")
    app.config["GEMINI_API_KEY"] = "test-key"
    reply = json.dumps(
        {
            "reply": "Scheduled.",
            "actions": [
                {
                    "farm_id": 9999,
                    "kind": "pesticide",
                    "title_en": "Spray neem oil",
                    "detail_en": "5 ml per litre of water",
                    "due_date": "2026-10-25",
                }
            ],
        }
    )
    monkeypatch.setattr(ai, "requests", SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse(reply)))

    data = auth_client.post("/api/chat", json={"message": "spray my field"}).get_json()
    assert data["ok"] is True
    assert data["tasks"] == []
    with app.app_context():
        assert FarmTask.query.filter_by(source="ai").count() == 0


def test_chat_reports_gemini_http_error(app, auth_client, monkeypatch):
    from cropapp.services import ai

    app.config["GEMINI_API_KEY"] = "bad-key"
    monkeypatch.setattr(
        ai,
        "requests",
        SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse('{"error": {"message": "bad key"}}', 403)),
    )
    data = auth_client.post("/api/chat", json={"message": "hello"}).get_json()
    assert data["ok"] is False
    assert data["error"] == "http_403"
    assert "403" in data["reply"]


def test_chat_falls_back_to_backup_model(app, auth_client, monkeypatch):
    from cropapp.services import ai

    app.config["GEMINI_API_KEY"] = "test-key"
    good = json.dumps({"reply": "Fallback answer.", "actions": []})

    def fake_post(url, **kwargs):
        if "gemini-3.8-flash" in url:
            return FakeGeminiResponse('{"error": {"status": "UNAVAILABLE"}}', 503)
        return FakeGeminiResponse(good)

    monkeypatch.setattr(ai, "requests", SimpleNamespace(post=fake_post))
    data = auth_client.post("/api/chat", json={"message": "hello"}).get_json()
    assert data["ok"] is True
    assert "Fallback answer." in data["reply"]


def test_chat_reports_busy_when_all_models_overloaded(app, auth_client, monkeypatch):
    from cropapp.services import ai

    app.config["GEMINI_API_KEY"] = "test-key"
    monkeypatch.setattr(
        ai,
        "requests",
        SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse('{"error": {"status": "UNAVAILABLE"}}', 503)),
    )
    data = auth_client.post("/api/chat", json={"message": "hello"}).get_json()
    assert data["ok"] is False
    assert data["error"] == "http_503"
    assert "busy" in data["reply"].lower()


def test_chat_anonymous_user_gets_login_hint(app, client, monkeypatch):
    from cropapp.services import ai

    app.config["GEMINI_API_KEY"] = "test-key"
    reply = json.dumps(
        {
            "reply": "Use 45 kg urea per hectare.",
            "actions": [{"farm_id": 1, "kind": "fertilizer", "title_en": "Urea", "detail_en": "45 kg/ha"}],
        }
    )
    monkeypatch.setattr(ai, "requests", SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse(reply)))
    data = client.post("/api/chat", json={"message": "schedule it"}).get_json()
    assert data["ok"] is True
    assert "Log in" in data["reply"]
    assert data["tasks"] == []


def test_recommend_requires_location(auth_client):
    response = auth_client.post("/recommend", data={"month": "9"})
    assert response.status_code == 400
    assert b"Pick a location" in response.data or b"Pick a location" in response.data


def test_category_filter_limits_candidates():
    from cropapp.ml import recommend

    features = {"N": 40, "P": 35, "K": 45, "temperature": 29, "humidity": 74, "ph": 7.2, "rainfall": 101.7}
    plain = recommend(features, top_n=3)
    grouped = recommend(features, top_n=3, allowed_crops={"rice", "maize"})
    assert grouped["group_filtered"] is True
    assert all(item["crop"] in {"rice", "maize"} for item in grouped["top"])
    assert grouped["crop"] in {"rice", "maize"}
    assert len(plain["top"]) == 3


def test_water_fit_penalises_short_supply():
    from cropapp.ml import recommend
    from cropapp.services import water as water_service

    features = {"N": 90, "P": 45, "K": 40, "temperature": 25, "humidity": 80, "ph": 6.5, "rainfall": 210}
    rainfed_high = water_service.evaluate("rice", source="rainfed", days=None, rainfall_normal=101.7)
    assert rainfed_high["status"] == "short"
    assert rainfed_high["need"] == "high"
    assert water_service.penalty_for(rainfed_high) == 0.15

    drip = water_service.evaluate("rice", source="drip", days=5, rainfall_normal=101.7)
    assert drip["sufficient"] is True
    assert water_service.penalty_for(drip) == 1.0

    unknown = water_service.evaluate("rice")
    assert unknown["known"] is False
    assert unknown["status"] == "unknown"
    assert water_service.penalty_for(unknown) == 1.0

    penalties = {
        "rice": water_service.penalty_for(rainfed_high),
        "maize": water_service.penalty_for(
            water_service.evaluate("maize", source="rainfed", days=None, rainfall_normal=101.7)
        ),
    }
    assert penalties["rice"] == 0.15
    assert penalties["maize"] == 0.4

    dry = recommend(features, top_n=22)
    wet = recommend(features, top_n=22, penalties=penalties)

    def probability_of(ranked, crop):
        return next(item["probability"] for item in ranked["top"] if item["crop"] == crop)

    assert probability_of(wet, "rice") == pytest.approx(probability_of(dry, "rice") * 0.15, rel=0.05)
    assert probability_of(wet, "maize") == pytest.approx(probability_of(dry, "maize") * 0.4, rel=0.05)
    assert probability_of(wet, "cotton") == pytest.approx(probability_of(dry, "cotton"), rel=0.05)

    winner = dry["top"][0]["crop"]
    assert winner == "rice"
    flipped = recommend(features, top_n=22, penalties={winner: 0.05})
    assert flipped["top"][0]["crop"] != winner


def test_market_history_and_trend():
    from cropapp.services.market import compare_markets, market_for

    rice = market_for("rice", area_acres=2.5)
    assert rice["price"] == 2300.0
    assert rice["prev_price"] == 2183.0
    assert rice["change_pct"] > 0
    assert rice["trend"] == "up"
    assert len(rice["history"]) == 3
    assert rice["revenue"]["per_acre"] > 0
    assert rice["revenue"]["total"] == round(rice["revenue"]["per_acre"] * 2.5)
    assert "e-NAM" in rice["verify"]

    mango = market_for("mango")
    assert mango["price"] is None
    assert mango["note"]

    rows = compare_markets(["rice", "mango"], area_acres=1)
    assert rows[0]["crop"] == "rice"
    assert rows[-1]["price"] is None


def test_vegetable_engine_and_dose():
    from cropapp.services.fertilizer import compute_vegetable_fertilizer
    from cropapp.services import vegetables as vegetable_service

    suggestions = vegetable_service.suggest(7, temp_c=28)
    assert suggestions
    assert suggestions[0]["in_window"] is True
    keys = [row["key"] for row in suggestions]
    assert "tomato" in keys

    chosen = suggestions[0]
    dose = compute_vegetable_fertilizer(chosen, area_acres=1.5)
    assert dose["available"] is True
    assert dose["products"]["urea_kg_ha"] == round(chosen["npk_kg_ha"]["n"] / 0.46, 1)
    assert dose["products"]["dap_kg_ha"] == round(chosen["npk_kg_ha"]["p"] / 0.46, 1)
    assert dose["products"]["mop_kg_ha"] == round(chosen["npk_kg_ha"]["k"] / 0.60, 1)
    assert abs(sum(stage["n_share"] for stage in dose["plan"]) - 1.0) < 1e-6
    assert dose["total_for_farm"]["urea"] == round(dose["per_acre"]["urea"] * 1.5, 1)


def test_vegetable_recommendation_flow(auth_client, monkeypatch):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 7)),
    )
    response = auth_client.post(
        "/recommend",
        data={"lat": "30.806", "lon": "75.835", "month": "7", "category": "vegetables",
              "water_source": "well", "water_days": "4"},
    )
    assert response.status_code == 200
    assert b"Rule-based pick" in response.data
    assert b"Vegetables that fit this month" in response.data


def test_grouped_recommendation_flow(auth_client, monkeypatch):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    response = auth_client.post(
        "/recommend",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "category": "pulses"},
    )
    assert response.status_code == 200
    assert b"Filtered to your group" in response.data
    assert b"Pulses" in response.data


def test_save_farm_persists_water_and_category(auth_client, monkeypatch):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    response = auth_client.post(
        "/farms/save",
        data={
            "lat": "30.806", "lon": "75.835", "month": "9",
            "category": "cereals", "water_source": "borewell", "water_days": "5",
            "water_note": "Two hours each morning",
            "farm_name": "Canal side", "area_acres": "3", "sowing_date": "2026-10-15",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with auth_client.application.app_context():
        farm = Farm.query.one()
        assert farm.crop_category == "cereals"
        assert farm.water_source == "borewell"
        assert farm.water_days == 5.0
        assert farm.water_note == "Two hours each morning"
        assert farm.crop in {"rice", "maize"}


def test_farm_detail_renders_calendar_and_notes(auth_client, monkeypatch):
    from cropapp.models import FarmNote

    _save_farm(auth_client, monkeypatch, name="Calendar farm")
    with auth_client.application.app_context():
        farm_id = Farm.query.one().id

    response = auth_client.get(f"/farms/{farm_id}")
    assert response.status_code == 200
    assert b'id="cal-root"' in response.data
    assert b'id="farm-tasks"' in response.data
    assert b"Calendar farm" in response.data
    assert b"Gemini suggestions" in response.data

    note = FarmNote(farm_id=farm_id, user_id=1, question="Which spray?", text="Neem oil 5 ml/L.")
    with auth_client.application.app_context():
        db.session.add(note)
        db.session.commit()

    response = auth_client.get(f"/farms/{farm_id}")
    assert b"Which spray?" in response.data
    assert b"Neem oil 5 ml/L." in response.data


def test_farm_detail_hides_other_users_farms(auth_client, app, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with auth_client.application.app_context():
        farm_id = Farm.query.one().id

    other = app.test_client()
    other.post(
        "/account/register",
        data={"name": "Other", "email": "other@example.com", "password": "secret12", "confirm": "secret12"},
        follow_redirects=True,
    )
    other.post("/account/login", data={"email": "other@example.com", "password": "secret12"})
    assert other.get(f"/farms/{farm_id}").status_code == 404


def test_chat_stores_note_for_own_farm(app, auth_client, monkeypatch):
    from cropapp.services import ai
    from cropapp.models import FarmNote

    _save_farm(auth_client, monkeypatch, name="Note farm")
    with app.app_context():
        farm_id = Farm.query.one().id

    app.config["GEMINI_API_KEY"] = "test-key"
    reply = json.dumps({"reply": "Spray neem oil at first symptoms.", "actions": []})
    monkeypatch.setattr(ai, "requests", SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse(reply)))

    data = auth_client.post("/api/chat", json={"message": "what should I spray?", "farm_id": farm_id}).get_json()
    assert data["ok"] is True
    assert data["farm_id"] == farm_id
    assert data["note_id"]

    with app.app_context():
        note = FarmNote.query.one()
        assert note.farm_id == farm_id
        assert "neem oil" in note.text
        assert note.question == "what should I spray?"


def test_chat_ignores_foreign_farm_id(app, auth_client, monkeypatch):
    from cropapp.services import ai
    from cropapp.models import FarmNote

    _save_farm(auth_client, monkeypatch)
    app.config["GEMINI_API_KEY"] = "test-key"
    reply = json.dumps({"reply": "General advice.", "actions": []})
    monkeypatch.setattr(ai, "requests", SimpleNamespace(post=lambda *a, **k: FakeGeminiResponse(reply)))

    data = auth_client.post("/api/chat", json={"message": "hello", "farm_id": 99999}).get_json()
    assert data["farm_id"] is None
    assert data["note_id"] is None
    with app.app_context():
        assert FarmNote.query.count() == 0
