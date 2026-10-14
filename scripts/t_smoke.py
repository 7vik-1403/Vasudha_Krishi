import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cropapp import create_app

app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})

with app.app_context():
    from cropapp.extensions import db

    db.create_all()

client = app.test_client()

results = []


def check(label, response, expect=200):
    ok = response.status_code == expect
    results.append((label, response.status_code, expect, ok))
    print(f"{'PASS' if ok else 'FAIL'} {label}: {response.status_code} (expected {expect})")


r = client.get("/")
check("GET /", r)
print("  contains map:", b'id="map"' in r.data)

r = client.get("/account/register")
check("GET /account/register", r)

r = client.post(
    "/account/register",
    data={"name": "Test Farmer", "email": "farmer@example.com", "password": "secret12", "confirm": "secret12"},
    follow_redirects=True,
)
check("POST /account/register", r)

r = client.post(
    "/account/login",
    data={"email": "farmer@example.com", "password": "secret12"},
    follow_redirects=True,
)
check("POST /account/login", r)

r = client.get("/api/districts?state=Punjab")
print("GET /api/districts?", r.status_code, len(r.get_json().get("data", [])), "districts")

r = client.get("/api/resolve?lat=30.9&lon=75.8&month=9")
payload = r.get_json()
check("GET /api/resolve (lat/lon)", r)
if payload.get("ok"):
    data = payload["data"]
    print("  district:", data["location"]["district"], data["location"]["state"])
    print("  soil ph:", data["soil"]["ph"], "| texture:", data["soil"]["texture_class"])
    print("  climate:", data["climate"]["temp_c"], data["climate"]["humidity_pct"], data["climate"]["rainfall_model_mm"])
else:
    print("  error:", payload)

r = client.get("/api/resolve?district=Thrissur&state=Kerala&month=9")
payload = r.get_json()
check("GET /api/resolve (district)", r)
if payload.get("ok"):
    print("  district:", payload["data"]["location"]["district"], "| res:", payload["data"]["location"]["resolution"])

r = client.get("/api/resolve?lat=28.6&lon=77.2")
print("GET /api/resolve caching round:", r.status_code)

r = client.post(
    "/recommend",
    data={"lat": "30.9", "lon": "75.8", "month": "9", "n": "40", "p": "35", "k": "45", "ph": "7.0",
          "farm_name": "North plot", "area_acres": "2.5"},
)
check("POST /recommend (predict)", r)
print("  plan answer rendered:", b"answer-line" in r.data or b"text-capitalize" in r.data)

r = client.post(
    "/farms/save",
    data={"lat": "30.9", "lon": "75.8", "month": "9", "n": "40", "p": "35", "k": "45", "ph": "7.0",
          "farm_name": "North plot", "area_acres": "2.5", "sowing_date": "2026-10-15"},
    follow_redirects=True,
)
check("POST /farms/save", r)

r = client.get("/farms")
check("GET /farms", r)
print("  has farm:", b"North plot" in r.data)

with app.app_context():
    from cropapp.models import Farm, FarmTask, Recommendation, User

    farms = Farm.query.all()
    recs = Recommendation.query.all()
    tasks = FarmTask.query.all()
    print("DB:", User.query.count(), "users,", len(farms), "farms,", len(recs), "recommendations,", len(tasks), "tasks")
    if farms:
        print("  farm crop:", farms[0].crop, "| district:", farms[0].district, "| tasks:", len(farms[0].tasks))
    if tasks:
        print("  sample task:", tasks[0].title_en[:90], "| due", tasks[0].due_date)

r = client.post(
    "/farms/save",
    data={"district": "Thrissur", "state": "Kerala", "month": "9", "n": "50", "p": "40", "k": "30",
          "farm_name": "Kerala field"},
    follow_redirects=True,
)
check("POST /farms/save (district path)", r)

with app.app_context():
    from cropapp.models import Farm

    print("farms now:", [(f.name, f.district) for f in Farm.query.all()])

r = client.post("/api/chat", json={"message": "What dose of urea for rice?"})
check("POST /api/chat (no key configured)", r)
payload = r.get_json()
print("  ok:", payload.get("ok"), "| error:", payload.get("error"), "| reply:", (payload.get("reply") or "")[:90])

# --- model & analytics pages -------------------------------------------------
r = client.get("/model")
check("GET /model", r)
print("  has confusion matrix:", b"Confusion matrix" in r.data)

demo_client = app.test_client()
r = demo_client.get("/account/demo", follow_redirects=True)
check("GET /account/demo", r)
r = demo_client.get("/analytics")
check("GET /analytics (demo user)", r)
print("  has kpi band:", b"kpi-band" in r.data, "| has chart json:", b"analytics-data" in r.data)

# --- share link --------------------------------------------------------------
with app.app_context():
    from cropapp.models import Farm

    farm = Farm.query.filter_by(name="North plot").first()
    share_id = farm.id if farm else None

if share_id:
    r = client.post(f"/farms/{share_id}/share", follow_redirects=True)
    check(f"POST /farms/{share_id}/share", r)
    with app.app_context():
        token = Farm.query.filter_by(name="North plot").first().share_token
    anon = app.test_client()
    r = anon.get(f"/share/{token}")
    check("GET /share/<token> (anonymous)", r)
    print("  read-only note present:", b"read-only" in r.data)

# --- farm workspace tabs & expense manager -----------------------------------
if share_id:
    r = client.get(f"/farms/{share_id}?tab=overview")
    check("GET /farms/<id> workspace", r)
    print("  has tab bar:", b"ws-tabs" in r.data, "| has calendar:", b'id="cal-root"' in r.data,
          "| has assistant:", b'id="pane-assistant"' in r.data)

    r = client.post(
        f"/farms/{share_id}/expenses",
        data={"category": "seeds", "amount": "2500", "season": "Kharif", "next_tab": "expenses"},
        follow_redirects=True,
    )
    check("POST /farms/<id>/expenses", r)
    print("  expense shown:", b"seeds" in r.data and b"2,500" not in r.data)

    r = client.post(f"/farms/{share_id}/budgets",
                    data={"scope": "farm", "amount": "10000", "next_tab": "expenses"},
                    follow_redirects=True)
    check("POST /farms/<id>/budgets", r)
    print("  budget shown:", b"budget-track" in r.data)

# --- weather & disease (network-tolerant) ------------------------------------
r = client.get("/api/forecast?lat=30.9&lon=75.8")
print("GET /api/forecast ->", r.status_code, "(200 online / 502 offline)")

r = client.post("/api/disease", data={"image": (io.BytesIO(b"fake-bytes"), "leaf.jpg")},
                content_type="multipart/form-data")
print("POST /api/disease ->", r.status_code, "|", (r.get_json() or {}).get("error"))

failed = [item for item in results if not item[3]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
