"""CarbOn test paketi — ajanlar, orkestratör ve API uçları."""
import os
import tempfile
from datetime import date, timedelta

import pytest

# testler geçici veritabanı kullanır
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["CARBON_DB_PATH"] = _tmp.name

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.agents import insight, orchestrator, tracking  # noqa: E402
from app.agents.coach import _extract_tips, _rule_based  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
db.init_db()


# ------------------------------------------------------- Tracking Agent
def test_transport_calculation():
    r = tracking.track_transport("test_u", "car_petrol", 100)
    assert r["co2_kg"] == pytest.approx(17.1, abs=0.01)
    assert r["unit"] == "km"


def test_electricity_calculation():
    r = tracking.track_electricity("test_u", 200)
    assert r["co2_kg"] == pytest.approx(95.6, abs=0.01)  # 200 * 0.478


def test_invalid_amount_rejected():
    with pytest.raises(tracking.TrackingError):
        tracking.track_transport("test_u", "bus", -5)
    with pytest.raises(tracking.TrackingError):
        tracking.track_transport("test_u", "spaceship", 10)


# -------------------------------------------------------- Insight Agent
def test_insight_totals_and_top_category():
    import uuid
    u = f"insight_u_{uuid.uuid4().hex[:6]}"
    tracking.track_transport(u, "car_petrol", 50)   # 8.55 kg
    tracking.track_electricity(u, 10)               # 4.78 kg
    a = insight.analyze(u)
    assert a["week_total_kg"] == pytest.approx(13.33, abs=0.05)
    assert a["top_category"] == "transport"
    assert a["equivalents"]["car_km"] > 0
    assert isinstance(a["trend"], list)


def test_week_change_detection():
    u = "trend_u"
    old = (date.today() - timedelta(days=8)).isoformat()
    tracking.track_transport(u, "bus", 100, entry_date=old)   # geçen hafta
    tracking.track_transport(u, "bus", 200)                   # bu hafta
    a = insight.analyze(u)
    assert a["week_change_pct"] == pytest.approx(100.0, abs=1)


# ---------------------------------------------------------- Coach Agent
def test_rule_based_coach_always_eight_tips():
    tips = _rule_based({"by_subtype": {"car_petrol": 20.0}, "top_category": "transport",
                        "week_total_kg": 20.0})
    assert len(tips) == 8
    tips_empty = _rule_based({"by_subtype": {}, "top_category": None, "week_total_kg": 0})
    assert len(tips_empty) == 8


def test_tip_json_extraction():
    raw = '```json\n{"tips": ["a", "b", "c"]}\n```'
    assert _extract_tips(raw) == ["a", "b", "c"]
    assert _extract_tips("bozuk çıktı") is None


# ---------------------------------------------------------- Orkestratör
def test_full_pipeline():
    out = orchestrator.handle_entry("pipe_u", "transport",
                                    {"subtype": "metro", "amount": 30})
    agents = [p["agent"] for p in out["pipeline"]]
    assert agents == ["tracking", "insight", "coach"]
    assert out["entry"]["co2_kg"] == pytest.approx(1.05, abs=0.01)
    assert len(out["coach"]["tips"]) == 8
    # LLM anahtarı varsa LLM sağlayıcısı, yoksa kural tabanlı fallback döner
    assert out["coach"]["provider"] in ["rule_based", "groq", "gemini", "openai"]


# ------------------------------------------------------------------ API
def test_api_entry_and_dashboard():
    r = client.post("/api/entries", json={
        "user": "api_u", "category": "electricity", "amount": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["entry"]["co2_kg"] == pytest.approx(23.9, abs=0.01)

    d = client.get("/api/dashboard", params={"user": "api_u"}).json()
    assert d["insight"]["week_total_kg"] >= 23.9
    assert "tasks" in d


def test_api_validation_error():
    r = client.post("/api/entries", json={
        "user": "api_u", "category": "transport", "subtype": "bus", "amount": -1})
    assert r.status_code == 422


def test_api_export_csv():
    r = client.get("/api/export", params={"user": "api_u", "fmt": "csv"})
    assert r.status_code == 200
    assert "entry_date" in r.text


def test_task_completion_and_streak():
    client.post("/api/entries", json={"user": "task_u", "category": "transport",
                                      "subtype": "bus", "amount": 5})
    d = client.get("/api/dashboard", params={"user": "task_u"}).json()
    task_id = d["tasks"][0]["id"]
    r = client.post("/api/tasks/complete", json={"user": "task_u", "task_id": task_id})
    assert r.status_code == 200
    assert r.json()["streak_days"] >= 1


def test_budget_update():
    r = client.post("/api/budget", json={"user": "api_u", "daily_budget_kg": 12})
    assert r.status_code == 200
    d = client.get("/api/dashboard", params={"user": "api_u"}).json()
    assert d["user"]["daily_budget_kg"] == 12


def test_task_reset():
    user = "reset_u"
    client.post("/api/entries", json={"user": user, "category": "transport",
                                      "subtype": "bus", "amount": 5})
    d = client.get("/api/dashboard", params={"user": user}).json()
    task_id = d["tasks"][0]["id"]
    
    # complete task
    rc = client.post("/api/tasks/complete", json={"user": user, "task_id": task_id})
    assert rc.status_code == 200
    
    # verify it is completed
    d2 = client.get("/api/dashboard", params={"user": user}).json()
    assert d2["tasks"][0]["done"] == 1
    
    # reset tasks
    rr = client.post("/api/tasks/reset", params={"user": user})
    assert rr.status_code == 200
    
    # verify it is reset
    d3 = client.get("/api/dashboard", params={"user": user}).json()
    assert d3["tasks"][0]["done"] == 0


def test_task_deletion():
    import uuid
    user = f"del_user_{uuid.uuid4().hex[:6]}"
    today = date.today().isoformat()
    tasks = db.save_tasks(user, ["Task to delete"], today)
    t_id = tasks[0]["id"]

    # Delete task
    del_res = client.post("/api/tasks/delete", json={"user": user, "task_id": t_id})
    assert del_res.status_code == 200

    # Verify task deleted
    day_tasks = db.tasks_for_day(user, today)
    assert len(day_tasks) == 0


def test_user_registration_and_login():
    import uuid
    uid = uuid.uuid4().hex[:6]
    uname = f"auth_user_{uid}"
    uemail = f"test_{uid}@carbon.app"

    # Register user
    reg_data = {
        "username": uname,
        "email": uemail,
        "password": "securepassword123",
        "full_name": "Test User"
    }
    r = client.post("/api/auth/register", json=reg_data)
    assert r.status_code == 200
    res = r.json()
    assert res["username"] == uname
    assert "token" in res
    token = res["token"]

    # Check /api/auth/me with Bearer token
    r_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r_me.status_code == 200
    assert r_me.json()["email"] == uemail

    # Login user
    login_data = {
        "username_or_email": uemail,
        "password": "securepassword123"
    }
    r_log = client.post("/api/auth/login", json=login_data)
    assert r_log.status_code == 200
    login_token = r_log.json()["token"]

    # Logout
    r_out = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {login_token}"})
    assert r_out.status_code == 200


def test_firebase_config_and_auth():
    # Config endpoint
    r_conf = client.get("/api/config/firebase")
    assert r_conf.status_code == 200
    assert "enabled" in r_conf.json()

    # Firebase Auth Sync
    fb_data = {
        "firebase_uid": "fb_uid_12345",
        "email": "fb_user@carbon.app",
        "full_name": "Firebase User"
    }
    r_fb = client.post("/api/auth/firebase", json=fb_data)
    assert r_fb.status_code == 200
    res = r_fb.json()
    assert res["email"] == "fb_user@carbon.app"
    assert "token" in res


