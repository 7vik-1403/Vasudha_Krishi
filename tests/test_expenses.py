"""Farm expense manager: validation, CRUD, summaries, budgets, reconciliation,
tab workspace rendering and per-user ownership guards."""

from datetime import date, timedelta

from conftest import fake_resolution
from cropapp.extensions import db
from cropapp.models import Farm, FarmBudget, FarmRecord
from cropapp.services import expenses


def _save_farm(client, monkeypatch, name="Expense farm"):
    monkeypatch.setattr(
        "cropapp.routes.main.resolve",
        lambda lat, lon, month=None, **kw: fake_resolution(float(lat), float(lon), int(month or 9)),
    )
    client.post(
        "/farms/save",
        data={"lat": "30.806", "lon": "75.835", "month": "9", "farm_name": name,
              "area_acres": "2", "sowing_date": "2026-10-15"},
        follow_redirects=True,
    )


def _farm_id(app):
    with app.app_context():
        return Farm.query.one().id


# --------------------------------------------------------------------- validation

def test_validate_requires_category_amount_and_past_date():
    cleaned, errors = expenses.validate({})
    assert "category" in errors
    assert "amount" in errors

    cleaned, errors = expenses.validate(
        {"category": "seeds", "amount": "-5", "recorded_on": (date.today() + timedelta(days=1)).isoformat()}
    )
    assert errors["amount"] == "amount must be greater than zero"
    assert errors["recorded_on"] == "date cannot be in the future"

    cleaned, errors = expenses.validate({"category": "not-a-category", "amount": "10"})
    assert "category" in errors


def test_validate_accepts_good_payload():
    cleaned, errors = expenses.validate(
        {"category": "Fertilizers", "amount": "1500.5", "recorded_on": "2026-09-01",
         "crop": "Rice", "season": "Kharif", "vendor": "Agro Store",
         "payment_method": "upi", "quantity": "50", "unit": "kg", "note": "urea"}
    )
    assert errors == {}
    assert cleaned["category"] == "fertilizers"
    assert cleaned["amount"] == 1500.5
    assert cleaned["payment_method"] == "upi"
    assert cleaned["season"] == "Kharif"


# --------------------------------------------------------------------- CRUD routes

def test_add_edit_delete_expense_route(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)

    response = auth_client.post(
        f"/farms/{farm_id}/expenses",
        data={"category": "seeds", "amount": "2000", "recorded_on": "2026-09-05",
              "crop": "rice", "season": "Kharif", "vendor": "Store", "payment_method": "cash",
              "next_tab": "expenses"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Expense of" in response.data or "खर्च" in response.data.decode()

    with app.app_context():
        record = FarmRecord.query.filter_by(kind="expense").one()
        assert record.category == "seeds"
        assert record.amount == 2000
        assert record.data.get("category") == "seeds"  # legacy JSON kept in sync
        record_id = record.id

    # edit
    auth_client.post(
        f"/farms/{farm_id}/expenses/{record_id}/edit",
        data={"category": "labor", "amount": "2500", "recorded_on": "2026-09-06", "next_tab": "expenses"},
        follow_redirects=True,
    )
    with app.app_context():
        record = db.session.get(FarmRecord, record_id)
        assert record.category == "labor"
        assert record.amount == 2500

    # delete
    auth_client.post(f"/farms/{farm_id}/expenses/{record_id}/delete",
                     data={"next_tab": "expenses"}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(FarmRecord, record_id) is None


def test_add_expense_rejects_bad_amount(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)

    response = auth_client.post(
        f"/farms/{farm_id}/expenses",
        data={"category": "seeds", "amount": "0", "next_tab": "expenses"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert FarmRecord.query.filter_by(kind="expense").count() == 0


# --------------------------------------------------------------------- summary

def test_summary_groups_by_category_and_month(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)
    today = date.today().isoformat()

    for category, amount in (("seeds", 3000), ("seeds", 1000), ("labor", 2000)):
        auth_client.post(
            f"/farms/{farm_id}/expenses",
            data={"category": category, "amount": str(amount), "recorded_on": today,
                  "season": "Kharif", "crop": "rice", "next_tab": "expenses"},
            follow_redirects=True,
        )

    with app.app_context():
        records = list(FarmRecord.query.filter_by(kind="expense"))
        result = expenses.summary(records)

    assert result["total"] == 6000
    assert result["count"] == 3
    assert result["by_category"]["seeds"] == 4000
    assert result["by_category"]["labor"] == 2000
    assert result["current_month"] == 6000
    assert result["by_season"][0]["season"] == "Kharif"
    assert len(result["by_month"]) == 6


# --------------------------------------------------------------------- budgets

def test_budget_create_status_and_delete(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)

    auth_client.post(
        f"/farms/{farm_id}/budgets",
        data={"scope": "farm", "amount": "10000", "warn_pct": "80", "next_tab": "expenses"},
        follow_redirects=True,
    )
    auth_client.post(
        f"/farms/{farm_id}/expenses",
        data={"category": "seeds", "amount": "8500", "recorded_on": date.today().isoformat(),
              "next_tab": "expenses"},
        follow_redirects=True,
    )

    with app.app_context():
        budget = FarmBudget.query.one()
        assert budget.scope == "farm"
        assert budget.amount == 10000
        records = list(FarmRecord.query.filter_by(kind="expense"))
        result = expenses.summary(records)
        status = expenses.budget_status([budget], result)
        assert status["any_warn"] is True
        assert status["any_over"] is False
        row = status["rows"][0]
        assert row["spent"] == 8500
        assert row["remaining"] == 1500
        assert row["status"] == "warn"
        budget_id = budget.id

    # update (upsert same scope)
    auth_client.post(f"/farms/{farm_id}/budgets",
                     data={"scope": "farm", "amount": "5000", "next_tab": "expenses"},
                     follow_redirects=True)
    with app.app_context():
        assert FarmBudget.query.count() == 1
        assert FarmBudget.query.one().amount == 5000
        status = expenses.budget_status([FarmBudget.query.one()],
                                        expenses.summary(list(FarmRecord.query.filter_by(kind="expense"))))
        assert status["any_over"] is True

    auth_client.post(f"/farms/{farm_id}/budgets/{budget_id}/delete",
                     data={"next_tab": "expenses"}, follow_redirects=True)
    with app.app_context():
        assert FarmBudget.query.count() == 0


def test_budget_scope_validation(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)
    auth_client.post(f"/farms/{farm_id}/budgets",
                     data={"scope": "nonsense", "amount": "1000"}, follow_redirects=True)
    auth_client.post(f"/farms/{farm_id}/budgets",
                     data={"scope": "crop", "amount": "1000"}, follow_redirects=True)  # crop missing
    with app.app_context():
        assert FarmBudget.query.count() == 0


# --------------------------------------------------------------------- reconciliation

def test_reconciliation_replaces_estimate_line_without_double_counting(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)

    auth_client.post(
        f"/farms/{farm_id}/expenses",
        data={"category": "labor", "amount": "5000", "recorded_on": date.today().isoformat(),
              "next_tab": "expenses"},
        follow_redirects=True,
    )

    with app.app_context():
        records = list(FarmRecord.query.filter_by(kind="expense"))
        result = expenses.summary(records)

    estimate = {
        "available": True,
        "area_acres": 2,
        "costs": {"total": 20000},
        "breakdown": [
            {"key": "seed", "label_en": "Seed", "label_mr": "बियाणे", "per_acre": 1000, "total": 2000},
            {"key": "labor", "label_en": "Labour", "label_mr": "मजुरी", "per_acre": 2000, "total": 4000},
            {"key": "other", "label_en": "Other", "label_mr": "इतर", "per_acre": 500, "total": 1000},
        ],
        "revenue": {"total": 30000},
    }
    recon = expenses.actual_vs_estimate(estimate, result, {})

    assert recon["available"] is True
    assert recon["actual_expenses_total"] == 5000
    # labour line replaced by actual 5000, seed/other stay on estimate.
    assert recon["projected_total"] == 2000 + 5000 + 1000
    labour = next(row for row in recon["rows"] if row["key"] == "labor")
    assert labour["source"] == "actual"
    assert labour["used"] == 5000
    # no recorded revenue -> no "true net", but projected net is available.
    assert recon["net"]["available"] is False
    assert recon["net"]["projected"] == 30000 - recon["projected_total"]


# --------------------------------------------------------------------- ownership

def test_expense_routes_reject_other_users(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)
    auth_client.post(f"/farms/{farm_id}/expenses",
                     data={"category": "seeds", "amount": "100", "next_tab": "expenses"},
                     follow_redirects=True)
    with app.app_context():
        record_id = FarmRecord.query.filter_by(kind="expense").one().id

    other = app.test_client()
    other.post("/account/register",
               data={"name": "Other", "email": "other-exp@example.com",
                     "password": "secret12", "confirm": "secret12"},
               follow_redirects=True)
    other.post("/account/login", data={"email": "other-exp@example.com", "password": "secret12"})

    assert other.get(f"/farms/{farm_id}").status_code == 404
    assert other.post(f"/farms/{farm_id}/expenses",
                      data={"category": "seeds", "amount": "10"}).status_code == 404
    assert other.post(f"/farms/{farm_id}/expenses/{record_id}/edit",
                      data={"category": "seeds", "amount": "10"}).status_code == 404
    assert other.post(f"/farms/{farm_id}/expenses/{record_id}/delete").status_code == 404
    assert other.post(f"/farms/{farm_id}/budgets",
                      data={"scope": "farm", "amount": "10"}).status_code == 404


# --------------------------------------------------------------------- tasks

def test_add_cancel_and_edit_task(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)

    auth_client.post(f"/farms/{farm_id}/tasks",
                     data={"title_en": "Top dress urea", "kind": "fertilizer", "due_date": "2026-11-01",
                           "next_tab": "calendar"},
                     follow_redirects=True)
    with app.app_context():
        from cropapp.models import FarmTask
        task = FarmTask.query.filter_by(source="manual").one()
        assert task.title_en == "Top dress urea"
        assert task.status == "pending"
        task_id = task.id

    auth_client.post(f"/tasks/{task_id}/cancel", data={"next_tab": "calendar"}, follow_redirects=True)
    with app.app_context():
        from cropapp.models import FarmTask
        assert db.session.get(FarmTask, task_id).status == "cancelled"

    auth_client.post(f"/tasks/{task_id}/edit",
                     data={"title_en": "Top dress DAP", "kind": "fertilizer", "next_tab": "calendar"},
                     follow_redirects=True)
    with app.app_context():
        from cropapp.models import FarmTask
        assert db.session.get(FarmTask, task_id).title_en == "Top dress DAP"


# --------------------------------------------------------------------- workspace UI

def test_farm_workspace_renders_tabs_and_active_state(app, auth_client, monkeypatch):
    _save_farm(auth_client, monkeypatch)
    farm_id = _farm_id(app)

    response = auth_client.get(f"/farms/{farm_id}?tab=expenses")
    assert response.status_code == 200
    body = response.data
    for tab in (b"overview", b"recommendations", b"soil", b"irrigation",
                b"calendar", b"expenses", b"assistant"):
        assert b'tab=' + tab in body
    assert b"Record an expense" in body
    assert b'id="pane-expenses"' in body
    assert b'id="cal-root"' in body
    assert b'id="farm-tasks"' in body

    # unknown tab falls back to overview
    response = auth_client.get(f"/farms/{farm_id}?tab=does-not-exist")
    assert response.status_code == 200
    assert b'id="pane-overview"' in response.data
