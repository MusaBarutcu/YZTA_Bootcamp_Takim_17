"""CarbOn — FastAPI uygulaması.

Çalıştırma:  uvicorn app.main:app --reload
Arayüz:      http://localhost:8000
API doküman: http://localhost:8000/docs
"""
import csv
import io
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db
from .agents import insight, orchestrator
from .agents.tracking import TrackingError
from .emission_factors import (ELECTRICITY_KG_PER_KWH, TRANSPORT_FACTORS,
                               TURKEY_DAILY_AVG_KG)

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="CarbOn API",
    description="Çok ajanlı yapay zeka destekli karbon ayak izi koçu",
    version="1.0.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"


# ------------------------------------------------------------- şemalar
class RegisterIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: str = Field(..., min_length=5, max_length=128)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str | None = None


class LoginIn(BaseModel):
    username_or_email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class FirebaseAuthIn(BaseModel):
    firebase_uid: str
    email: str | None = None
    full_name: str | None = None
    username: str | None = None
    password: str | None = None


class EntryIn(BaseModel):
    user: str = Field(..., min_length=1, max_length=64)
    category: str  # 'transport' | 'electricity'
    subtype: str | None = None
    amount: float
    entry_date: str | None = None  # YYYY-MM-DD


class BudgetIn(BaseModel):
    user: str
    daily_budget_kg: float = Field(..., gt=0, le=1000)


class TaskDoneIn(BaseModel):
    user: str
    task_id: int
    done: int = 1


# ------------------------------------------------------------- auth uçları
@app.get("/api/config/firebase")
def get_firebase_config():
    return {
        "apiKey": config.FIREBASE_API_KEY,
        "authDomain": config.FIREBASE_AUTH_DOMAIN,
        "projectId": config.FIREBASE_PROJECT_ID,
        "storageBucket": config.FIREBASE_STORAGE_BUCKET,
        "messagingSenderId": config.FIREBASE_MESSAGING_SENDER_ID,
        "appId": config.FIREBASE_APP_ID,
        "enabled": bool(config.FIREBASE_API_KEY and config.FIREBASE_PROJECT_ID),
    }


@app.get("/api/auth/resolve-email")
def resolve_email(identifier: str = Query(...)):
    email = db.get_email_by_identifier(identifier)
    return {"email": email}


@app.post("/api/auth/firebase")
def firebase_auth(body: FirebaseAuthIn):
    try:
        return db.sync_firebase_user(
            firebase_uid=body.firebase_uid,
            email=body.email or "",
            full_name=body.full_name or "",
            display_username=body.username or None,
            password=body.password or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/auth/register")
def register(body: RegisterIn):
    try:
        return db.register_user(
            username=body.username,
            email=body.email,
            password=body.password,
            full_name=body.full_name or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/auth/login")
def login(body: LoginIn):
    try:
        return db.authenticate_user(
            username_or_email=body.username_or_email,
            password=body.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.get("/api/auth/me")
def me(authorization: str | None = Header(None), token: str | None = Query(None)):
    auth_token = None
    if authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split(" ", 1)[1]
    elif token:
        auth_token = token

    if not auth_token:
        raise HTTPException(status_code=401, detail="Oturum jetonu bulunamadı.")

    user = db.get_user_by_token(auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Geçersiz veya süresi dolmuş oturum.")
    return user


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(None), token: str | None = Query(None)):
    auth_token = None
    if authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split(" ", 1)[1]
    elif token:
        auth_token = token

    if auth_token:
        db.logout_user(auth_token)
    return {"status": "logged_out"}


# ------------------------------------------------------------- genel uçlar
@app.get("/api/factors")
def factors():
    return {
        "transport": TRANSPORT_FACTORS,
        "electricity_kg_per_kwh": ELECTRICITY_KG_PER_KWH,
        "turkey_daily_avg_kg": TURKEY_DAILY_AVG_KG,
    }


@app.post("/api/entries")
def create_entry(body: EntryIn):
    """Orkestratör pipeline'ı: Tracking → Insight → Coach."""
    try:
        return orchestrator.handle_entry(
            body.user.strip(),
            body.category,
            {"subtype": body.subtype, "amount": body.amount,
             "entry_date": body.entry_date},
        )
    except TrackingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/api/entries/{entry_id}")
def remove_entry(entry_id: int, user: str = Query(...)):
    if not db.delete_entry(user, entry_id):
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı.")
    return {"deleted": entry_id}


@app.get("/api/entries")
def list_entries(user: str = Query(...), limit: int = Query(50, le=500)):
    return db.all_entries(user)[:limit]


@app.get("/api/insight")
def get_insight(user: str = Query(...)):
    return insight.analyze(user)


@app.post("/api/coach/refresh")
def refresh_coach(body: BudgetIn | None = None, user: str = Query(...)):
    return orchestrator.refresh_coaching(user)


@app.get("/api/dashboard")
def dashboard(user: str = Query(...)):
    """Arayüzün tek çağrıda ihtiyaç duyduğu her şey."""
    u = db.ensure_user(user)
    analysis = insight.analyze(user)
    today = date.today().isoformat()
    tasks = db.tasks_for_day(user, today)
    if not tasks:
        orchestrator.refresh_coaching(user)
        tasks = db.tasks_for_day(user, today)
    return {
        "user": u,
        "insight": analysis,
        "tasks": tasks,
        "recent_entries": db.all_entries(user)[:10],
    }


@app.post("/api/budget")
def set_budget(body: BudgetIn):
    db.set_budget(body.user, body.daily_budget_kg)
    return {"user": body.user, "daily_budget_kg": body.daily_budget_kg}


class TaskDeleteIn(BaseModel):
    user: str
    task_id: int


@app.post("/api/tasks/complete")
def complete_task(body: TaskDoneIn):
    if not db.complete_task(body.user, body.task_id, body.done):
        raise HTTPException(status_code=404, detail="Görev bulunamadı.")
    return {"done": body.task_id, "done_status": body.done, "streak_days": db.streak_days(body.user)}


@app.post("/api/tasks/delete")
def delete_task(body: TaskDeleteIn):
    if not db.delete_task(body.user, body.task_id):
        raise HTTPException(status_code=404, detail="Görev bulunamadı.")
    return {"status": "success", "task_id": body.task_id}


@app.post("/api/tasks/reset")
def reset_tasks(user: str = Query(...)):
    today = date.today().isoformat()
    db.reset_tasks_for_day(user, today)
    return {"status": "ok"}


@app.get("/api/export")
def export(user: str = Query(...), fmt: str = Query("csv", pattern="^(csv|json)$")):
    rows = db.all_entries(user)
    if fmt == "json":
        return rows
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=["id", "entry_date", "category", "subtype",
                         "amount", "unit", "co2_kg", "created_at"],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=carbon_{user}.csv"},
    )


@app.get("/api/health")
def health():
    llm_prov = "rule_based"
    if config.GROQ_API_KEY:
        llm_prov = "groq"
    elif config.GEMINI_API_KEY:
        llm_prov = "gemini"
    elif config.OPENAI_API_KEY:
        llm_prov = "openai"
    return {
        "status": "ok",
        "llm": llm_prov,
    }


# ------------------------------------------------------------- arayüz
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
