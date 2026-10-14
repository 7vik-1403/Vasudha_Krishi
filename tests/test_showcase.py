import io

import pytest

from conftest import fake_resolution
from cropapp import create_app
from cropapp.extensions import db
from cropapp.models import Farm, FarmNote


def _demo_app(**overrides):
    config = {
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "GEMINI_API_KEY": "",
        "DEMO_MODE_ENABLED": True,
    }
    config.update(overrides)
    application = create_app(config)
    with application.app_context():
        db.create_all()
    return application


def test_model_page_renders(client):
    response = client.get("/model")
    assert response.status_code == 200
    assert b"Confusion matrix" in response.data
    assert b"Candidate models" in response.data
    assert b"python train.py" in response.data


def test_analytics_requires_login(app):
    anon = app.test_client()
    response = anon.get("/analytics")
    assert response.status_code == 302
    assert "/account/login" in response.headers["Location"]


def test_analytics_empty_state(auth_client):
    response = auth_client.get("/analytics")
    assert response.status_code == 200
    assert b"Nothing to analyse yet" in response.data


def test_demo_login_seeds_two_farms():
    app = _demo_app()
    client = app.test_client()
    response = client.get("/account/demo", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        farm_count = Farm.query.count()
        assert farm_count == 2

    analytics = client.get("/analytics")
    assert analytics.status_code == 200
    assert b"kpi-band" in analytics.data
    assert b"analytics-data" in analytics.data


def test_demo_login_disabled_flag():
    app = _demo_app(DEMO_MODE_ENABLED=False)
    client = app.test_client()
    response = client.get("/account/demo", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert Farm.query.count() == 0


def test_forecast_requires_coordinates(client):
    response = client.get("/api/forecast")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_forecast_returns_service_payload(client, monkeypatch):
    from cropapp.services import weather

    monkeypatch.setattr(
        weather,
        "forecast",
        lambda lat, lon, days=7: {"ok": True, "days": [{"date": "2026-10-14", "temp_max": 31}], "source": "Open-Meteo"},
    )
    response = client.get("/api/forecast?lat=30.8&lon=75.8")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["source"] == "Open-Meteo"


def test_forecast_reports_failure(client, monkeypatch):
    from cropapp.services import weather

    monkeypatch.setattr(weather, "forecast", lambda lat, lon, days=7: {"ok": False, "error": "network"})
    response = client.get("/api/forecast?lat=30.8&lon=75.8")
    assert response.status_code == 502
    assert response.get_json()["ok"] is False


def test_disease_requires_an_image(client):
    response = client.post("/api/disease", data={})
    assert response.status_code == 400
    assert response.get_json()["error"] == "an image file is required"


def test_disease_disabled_returns_404():
    app = _demo_app(DISEASE_DETECTION_ENABLED=False)
    client = app.test_client()
    response = client.post("/api/disease", data={"image": (io.BytesIO(b"x"), "leaf.jpg")})
    assert response.status_code == 404
    assert response.get_json()["error"] == "disabled"


def test_disease_saves_note_for_own_farm(app, auth_client, monkeypatch):
    from cropapp.services import disease

    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    auth_client.post(
        "/farms/save",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "farm_name": "Leaf farm", "sowing_date": "2026-10-15"},
        follow_redirects=True,
    )
    with app.app_context():
        farm_id = Farm.query.one().id

    monkeypatch.setattr(
        disease,
        "diagnose",
        lambda data, mime, crop=None, language="en": {"ok": True, "reply": "Brown spot detected.", "diagnosis": {"name": "Brown spot"}},
    )
    response = auth_client.post(
        "/api/disease",
        data={"image": (io.BytesIO(b"fake-bytes"), "leaf.jpg"), "farm_id": str(farm_id)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["note_id"]

    with app.app_context():
        note = FarmNote.query.one()
        assert note.source == "disease"
        assert note.farm_id == farm_id


def _save_farm(client, monkeypatch, name="Share farm"):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    client.post(
        "/farms/save",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "farm_name": name, "sowing_date": "2026-10-15"},
        follow_redirects=True,
    )


def test_share_farm_creates_public_link(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id
        assert Farm.query.one().share_token is None

    response = auth_client.post(f"/farms/{farm_id}/share", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        token = Farm.query.one().share_token
        assert token

    public = app.test_client()
    page = public.get(f"/share/{token}")
    assert page.status_code == 200
    assert b"Shared field plan" in page.data
    assert b"read-only" in page.data


def test_shared_link_unknown_token_404(client):
    assert client.get("/share/does-not-exist").status_code == 404


def test_unshare_revokes_link(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id

    auth_client.post(f"/farms/{farm_id}/share", follow_redirects=True)
    with app.app_context():
        token = Farm.query.one().share_token

    auth_client.post(f"/farms/{farm_id}/unshare", follow_redirects=True)
    with app.app_context():
        assert Farm.query.one().share_token is None
    assert app.test_client().get(f"/share/{token}").status_code == 404


def test_share_requires_owner(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    with app.app_context():
        farm_id = Farm.query.one().id

    other = app.test_client()
    other.post(
        "/account/register",
        data={"name": "Other", "email": "other-share@example.com", "password": "secret12", "confirm": "secret12"},
        follow_redirects=True,
    )
    other.post("/account/login", data={"email": "other-share@example.com", "password": "secret12"})
    assert other.post(f"/farms/{farm_id}/share").status_code == 404


def test_disease_reports_missing_key(app, auth_client, monkeypatch):
    from cropapp.services import disease

    monkeypatch.setattr(
        disease,
        "diagnose",
        lambda data, mime, crop=None, language="en": {"ok": False, "error": "missing_key", "reply": "Add GEMINI_API_KEY"},
    )
    response = auth_client.post(
        "/api/disease",
        data={"image": (io.BytesIO(b"fake-bytes"), "leaf.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 502
    assert response.get_json()["error"] == "missing_key"
