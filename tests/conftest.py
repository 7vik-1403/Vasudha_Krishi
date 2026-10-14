import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cropapp import create_app
from cropapp.extensions import db


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "GEMINI_API_KEY": "",
        }
    )
    with application.app_context():
        db.create_all()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app, client):
    credentials = {"email": "farmer@example.com", "password": "secret12"}
    client.post(
        "/account/register",
        data={**credentials, "name": "Test Farmer", "confirm": credentials["password"]},
        follow_redirects=True,
    )
    client.post("/account/login", data=credentials, follow_redirects=True)
    return client


def fake_resolution(lat=30.806, lon=75.835, month=9):
    """A resolver payload shaped exactly like utils.resolver.resolve() (no network)."""
    return {
        "lat": lat,
        "lon": lon,
        "month": month,
        "location": {
            "district": "Ludhiana",
            "state": "Punjab",
            "dt_code": "026",
            "village": None,
            "pincode": None,
            "display_name": None,
            "resolution": "map_pin",
            "sources": {"district_polygon": "Survey of India district polygons (bundled GeoJSON)", "place_name": None},
        },
        "soil": {
            "ph": 7.2,
            "ph_source": "SoilGrids 250 m (ISRIC) - modelled global soil map",
            "organic_carbon_g_kg": 9.5,
            "total_nitrogen_g_kg": 1.1,
            "clay_pct": 24.0,
            "sand_pct": 43.0,
            "silt_pct": 33.0,
            "texture_class": "Loam",
            "available": {
                "n": 40.0,
                "p": 35.0,
                "k": 45.0,
                "unit": "kg/ha",
                "source": "Farmer-entered Soil Health Card values",
                "measured": True,
            },
            "sources": {"grid": "SoilGrids 250 m (ISRIC, rest.isric.org)", "available_npk": "farmer"},
            "warnings": [],
        },
        "climate": {
            "month": month,
            "temp_c": 29.0,
            "humidity_pct": 74.0,
            "rainfall_actual_mm": 196.0,
            "rainfall_normal_mm": 101.7,
            "sources": {"temperature": "Open-Meteo", "humidity": "Open-Meteo", "rainfall_normal": "IMD"},
            "district_matched": "LUDHIANA",
            "rainfall_normal_is_state_average": False,
            "rainfall_model_mm": 101.7,
            "fallbacks": [],
        },
        "warnings": [],
    }
