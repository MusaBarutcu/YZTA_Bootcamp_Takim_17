"""Insight Agent (İçgörü Ajanı).

Kullanıcının geçmiş verilerini analiz eder:
- Haftalık/aylık CO2 trendi (grafik verisi)
- Kategori kırılımı ve "en çok nereden etkileniyorsunuz" özeti
- Bir önceki haftayla karşılaştırma
- Somutlaştırma: ağaç / araba-km / kahve eşdeğerleri
- Türkiye ortalamasıyla kıyas
"""
from datetime import date, timedelta

from .. import db
from ..emission_factors import (EQUIVALENTS, TRANSPORT_FACTORS,
                                TURKEY_DAILY_AVG_KG)

_LABELS = {"transport": "ulaşım", "electricity": "elektrik"}


def _sum(entries: list[dict]) -> float:
    return round(sum(e["co2_kg"] for e in entries), 2)


def _by_category(entries: list[dict]) -> dict:
    out: dict[str, float] = {}
    for e in entries:
        out[e["category"]] = out.get(e["category"], 0.0) + e["co2_kg"]
    return {k: round(v, 2) for k, v in out.items()}


def _by_subtype(entries: list[dict]) -> dict:
    out: dict[str, float] = {}
    for e in entries:
        out[e["subtype"]] = out.get(e["subtype"], 0.0) + e["co2_kg"]
    return {k: round(v, 2) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def equivalents(total_kg: float) -> dict:
    """CO2 miktarını gündelik eşdeğerlere çevirir."""
    return {
        "trees_year": round(total_kg / EQUIVALENTS["tree_year_kg"], 1),
        "car_km": round(total_kg / EQUIVALENTS["car_km_kg"]),
        "coffee_cups": round(total_kg / EQUIVALENTS["coffee_cup_kg"]),
    }


def analyze(username: str) -> dict:
    """Kullanıcının son 30 gününü analiz eder; özet metin + grafik verisi döner."""
    today = date.today()
    week_start = (today - timedelta(days=6)).isoformat()
    prev_week_start = (today - timedelta(days=13)).isoformat()
    prev_week_end = (today - timedelta(days=7)).isoformat()
    month_start = (today - timedelta(days=29)).isoformat()

    week = db.entries_between(username, week_start, today.isoformat())
    prev_week = db.entries_between(username, prev_week_start, prev_week_end)
    month = db.entries_between(username, month_start, today.isoformat())
    today_entries = db.entries_between(username, today.isoformat(), today.isoformat())

    week_total, prev_total = _sum(week), _sum(prev_week)
    month_total, today_total = _sum(month), _sum(today_entries)
    cats = _by_category(week)
    subs = _by_subtype(week)

    # trend serisi (son 14 gün, kesintisiz gün bazında kategori kırılımı)
    raw = db.daily_totals(username, days=30)
    series: dict[str, dict[str, float]] = {}
    for r in raw:
        series.setdefault(r["entry_date"], {})[r["category"]] = round(r["total"], 2)

    trend = []
    for i in reversed(range(14)):
        d_str = (today - timedelta(days=i)).isoformat()
        v = series.get(d_str, {})
        t_val = v.get("transport", 0.0)
        e_val = v.get("electricity", 0.0)
        trend.append({
            "date": d_str,
            "transport": t_val,
            "electricity": e_val,
            "total": round(t_val + e_val, 2),
        })

    # haftalık değişim
    change_pct = None
    if prev_total > 0:
        change_pct = round((week_total - prev_total) / prev_total * 100, 1)

    # en büyük etken
    top_category = max(cats, key=cats.get) if cats else None
    top_subtype = next(iter(subs), None)

    # özet metin (sade, jargonsuz)
    if not week:
        summary = ("Bu hafta henüz veri girmediniz. Ulaşım veya elektrik "
                   "girişi yaptığınızda size özel içgörüler burada görünecek.")
    else:
        parts = [f"Bu hafta toplam {week_total} kg CO₂e ürettiniz."]
        if top_category:
            share = round(cats[top_category] / week_total * 100) if week_total else 0
            label = _LABELS.get(top_category, top_category)
            parts.append(f"En büyük payı %{share} ile {label} alıyor.")
            if top_subtype and top_subtype in TRANSPORT_FACTORS:
                parts.append(
                    f"Öne çıkan kalem: {TRANSPORT_FACTORS[top_subtype]['label'].lower()}."
                )
        if change_pct is not None:
            if change_pct > 5:
                parts.append(f"Geçen haftaya göre %{abs(change_pct)} artış var — küçük bir rota değişikliği iyi gelebilir.")
            elif change_pct < -5:
                parts.append(f"Geçen haftaya göre %{abs(change_pct)} azalttınız, harika gidiyorsunuz! 🌱")
            else:
                parts.append("Geçen haftayla benzer seviyedesiniz.")
        summary = " ".join(parts)

    daily_avg = round(week_total / 7, 2)
    vs_turkey_pct = round(daily_avg / TURKEY_DAILY_AVG_KG * 100) if daily_avg else 0

    return {
        "summary": summary,
        "today_total_kg": today_total,
        "week_total_kg": week_total,
        "prev_week_total_kg": prev_total,
        "month_total_kg": month_total,
        "week_change_pct": change_pct,
        "daily_avg_kg": daily_avg,
        "turkey_daily_avg_kg": TURKEY_DAILY_AVG_KG,
        "vs_turkey_pct": vs_turkey_pct,
        "by_category": cats,
        "by_subtype": subs,
        "top_category": top_category,
        "top_subtype": top_subtype,
        "trend": trend,
        "equivalents": equivalents(week_total),
        "streak_days": db.streak_days(username),
    }
