"""Orkestratör.

Kullanıcı girdisi geldiğinde ajanların çalışma sırasını yönetir:

    Tracking  →  Insight  →  Coach

Her adım kendi hata sınırında çalışır; bir ajan başarısız olursa akış
kesilmez, o adımın çıktısı 'error' alanıyla işaretlenir ve kalan adımlar
mümkün olduğunca tamamlanır (fallback mekanizması).
"""
import logging

from . import coach, insight, tracking
from .tracking import TrackingError

log = logging.getLogger("carbon.orchestrator")


def handle_entry(username: str, category: str, payload: dict) -> dict:
    """Tam pipeline: girdiyi işle → analiz et → koç önerisi üret."""
    result: dict = {"pipeline": []}

    # 1) Tracking — girdi geçersizse akış burada durur (kullanıcı hatası)
    if category == "transport":
        entry = tracking.track_transport(
            username, payload.get("subtype", ""), payload.get("amount"),
            payload.get("entry_date"),
        )
    elif category == "electricity":
        entry = tracking.track_electricity(
            username, payload.get("amount"), payload.get("entry_date"),
        )
    else:
        raise TrackingError(f"Bilinmeyen kategori: {category}")
    result["entry"] = entry
    result["pipeline"].append({"agent": "tracking", "status": "ok"})

    # 2) Insight — hata olursa boş analizle devam et
    try:
        analysis = insight.analyze(username)
        result["insight"] = analysis
        result["pipeline"].append({"agent": "insight", "status": "ok"})
    except Exception as exc:  # pragma: no cover
        log.exception("Insight agent hatası")
        analysis = {"week_total_kg": 0, "daily_avg_kg": 0, "by_category": {},
                    "by_subtype": {}, "week_change_pct": None,
                    "summary": "Analiz şu an hazırlanamadı."}
        result["insight"] = {"error": str(exc), **analysis}
        result["pipeline"].append({"agent": "insight", "status": "fallback"})

    # 3) Coach — LLM hatasında kural tabanlı koça düşer (coach içinde)
    try:
        tips = coach.generate_tips(username, analysis)
        result["coach"] = tips
        result["pipeline"].append(
            {"agent": "coach", "status": "ok", "provider": tips["provider"]}
        )
    except Exception as exc:  # pragma: no cover
        log.exception("Coach agent hatası")
        result["coach"] = {"error": str(exc), "tips": []}
        result["pipeline"].append({"agent": "coach", "status": "error"})

    return result


def refresh_coaching(username: str) -> dict:
    """Veri girmeden koç önerilerini yenile (Insight → Coach)."""
    analysis = insight.analyze(username)
    tips = coach.generate_tips(username, analysis)
    return {"insight": analysis, "coach": tips}
