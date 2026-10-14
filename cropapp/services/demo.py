"""One-click demo account.

Seeds a self-contained, offline demo: a shared read-only login with two farms,
full recommendations, scheduled tasks and a little field history. All of it is
built from bundled datasets + the trained model, so it works without network.
"""

import json
from datetime import date, timedelta

from cropapp.extensions import db
from cropapp.models import Farm, FarmRecord, Recommendation, User

DEMO_EMAIL = "demo@vasudha.local"
DEMO_NAME = "Demo Farmer"
DEMO_PASSWORD = "vasudha-demo"


def _soil(ph, n, p, k, texture, clay=None, sand=None, silt=None, soc=None, tn=None):
    return {
        "ph": ph,
        "ph_source": "SoilGrids 250 m (ISRIC) - modelled global soil map",
        "organic_carbon_g_kg": soc,
        "total_nitrogen_g_kg": tn,
        "clay_pct": clay,
        "sand_pct": sand,
        "silt_pct": silt,
        "texture_class": texture,
        "available": {
            "n": float(n), "p": float(p), "k": float(k), "unit": "kg/ha",
            "source": "Farmer-entered Soil Health Card values",
            "measured": True,
        },
        "sources": {"grid": "SoilGrids 250 m (ISRIC, rest.isric.org)", "available_npk": "Soil Health Card"},
        "warnings": [],
    }


def _resolved(lat, lon, month, district, state, village, pincode, soil, climate):
    climate = dict(climate)
    climate.setdefault("fallbacks", [])
    return {
        "lat": lat, "lon": lon, "month": month,
        "location": {
            "district": district, "state": state, "dt_code": None,
            "village": village, "pincode": pincode,
            "display_name": f"{village}, {district}, {state}",
            "resolution": "map_pin",
            "sources": {"district_polygon": "bundled", "place_name": "demo"},
        },
        "soil": soil,
        "climate": climate,
        "warnings": [],
    }


def _farm_specs():
    """Two representative farms: an irrigated Punjab cotton field and a Deccan kharif field."""
    return [
        {
            "name": "Canal plot - Ludhiana",
            "area": 5.0,
            "month": 7,
            "water_source": "canal",
            "water_days": 2,
            "water_note": "Canal turn every 8-10 days in kharif.",
            "sowing": (date.today() - timedelta(days=45)).isoformat(),
            "form": {"n": "80", "p": "45", "k": "20", "ph": "7.2", "area_acres": "5", "month": "7",
                     "water_source": "canal", "water_days": "2"},
            "resolved": _resolved(
                30.901, 75.857, 7, "Ludhiana", "Punjab", "Demo Kalan", "141001",
                _soil(7.2, 80, 45, 20, "Loam", clay=24.0, sand=48.0, silt=28.0, soc=6.1, tn=0.9),
                {"temp_c": 30.4, "humidity_pct": 66.0, "rainfall_normal_mm": 150.0, "rainfall_model_mm": 120.0},
            ),
            "records": [
                ("soil", 60, {"ph": 7.2, "n": 80, "p": 45, "k": 20}, "Soil Health Card after kharif sowing."),
                ("expense", 40, {"amount": 6400}, "DAP + urea first split."),
                ("irrigation", 20, {"hours": 4}, "First canal irrigation."),
            ],
        },
        {
            "name": "Borewell field - Nashik",
            "area": 3.0,
            "month": 6,
            "water_source": "borewell",
            "water_days": 3,
            "water_note": "Borewell runs 3 days a week.",
            "sowing": (date.today() - timedelta(days=28)).isoformat(),
            "form": {"n": "120", "p": "20", "k": "20", "ph": "6.8", "area_acres": "3", "month": "6",
                     "water_source": "borewell", "water_days": "3"},
            "resolved": _resolved(
                20.007, 73.790, 6, "Nashik", "Maharashtra", "Demo Budruk", "422001",
                _soil(6.8, 120, 20, 20, "Clay loam", clay=31.0, sand=38.0, silt=31.0, soc=5.4, tn=0.8),
                {"temp_c": 27.0, "humidity_pct": 75.0, "rainfall_normal_mm": 120.0, "rainfall_model_mm": 90.0},
            ),
            "records": [
                ("soil", 30, {"ph": 6.8, "n": 120, "p": 20, "k": 20}, "Baseline soil test."),
                ("expense", 14, {"amount": 3800}, "Basal fertilizer and seed."),
            ],
        },
    ]


def get_or_create_demo_user():
    user = User.query.filter_by(email=DEMO_EMAIL).first()
    if user is not None:
        return user, False
    user = User(email=DEMO_EMAIL, name=DEMO_NAME, language="en")
    user.set_password(DEMO_PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user, True


def seed_demo(app, user):
    """Populate the demo user's farms. No-op if any farm already exists."""
    if user.farms:
        return False

    from flask import request, session

    from cropapp.routes.main import _prediction_dict, build_recommendation
    from cropapp.services.scheduler import build_tasks

    language = user.language or "en"
    for spec in _farm_specs():
        resolved = spec["resolved"]
        with app.test_request_context(method="POST", data=spec["form"]):
            session["language"] = language
            payload = build_recommendation(resolved, request.form)

            farm = Farm.from_resolution(
                owner=user,
                name=spec["name"],
                area_acres=spec["area"],
                resolved=resolved,
                npk=(float(spec["form"]["n"]), float(spec["form"]["p"]), float(spec["form"]["k"])),
                ph=float(spec["form"]["ph"]),
            )
            farm.crop_category = payload["category"] or None
            farm.water_source = spec["water_source"]
            farm.water_days = spec["water_days"]
            farm.water_note = spec["water_note"]
            db.session.add(farm)
            db.session.flush()

            prediction = _prediction_dict(payload)
            farm.crop = prediction["crop"]
            recommendation = Recommendation(
                owner=user, farm=farm,
                features_json=json.dumps(payload["features"]),
                top3_json=json.dumps(prediction["top"]),
                crop=prediction["crop"],
                crop_category=payload["category"] or None,
                confidence=prediction.get("confidence"),
                fertilizer_json=json.dumps(payload["fertilizer"]),
                msp_json=json.dumps(payload["market"]),
                water_json=json.dumps(payload["water"] or {}),
                warnings_json=json.dumps(payload["warnings"]),
                spray_json=json.dumps(payload.get("spray") or {}),
                excluded_json=json.dumps(payload.get("excluded") or []),
            )
            db.session.add(recommendation)
            db.session.flush()

            tasks = list(build_tasks(farm, recommendation, sowing=spec["sowing"]))
            db.session.add_all(tasks)

            for kind, days_ago, data, note in spec["records"]:
                db.session.add(FarmRecord(
                    user_id=user.id, farm_id=farm.id, kind=kind,
                    recorded_on=date.today() - timedelta(days=days_ago),
                    crop=farm.crop, amount=data.get("amount"), note=note,
                    data_json=json.dumps(data),
                ))

    db.session.commit()
    return True
