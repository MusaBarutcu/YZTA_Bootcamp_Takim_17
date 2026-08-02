"""Coach Agent (Koç Ajanı).

Insight Agent'ın analizini alır ve kullanıcıya 3 maddelik, kısa,
uygulanabilir "yeşil görev" listesi üretir.

Sağlayıcı önceliği:
1. Gemini (GEMINI_API_KEY tanımlıysa)
2. OpenAI (OPENAI_API_KEY tanımlıysa)
3. Kural tabanlı yerel koç (anahtar yoksa veya API hata verirse)

LLM hangi sebeple olursa olsun başarısız olursa uygulama ASLA bozulmaz;
kural tabanlı koç her zaman geçerli üç öneri döndürür.
"""
import json
import re

import httpx

from .. import config, db
from ..emission_factors import TRANSPORT_FACTORS

SYSTEM_PROMPT = """Sen CarbOn adlı bir karbon ayak izi koçusun. Görevin,
kullanıcının haftalık verilerine bakarak TAM 8 adet kısa, somut ve bugün
uygulanabilir öneri üretmek.

Kurallar:
- Türkçe yaz. Samimi, motive edici, jargonsuz bir dil kullan.
- Her öneri tek cümle olsun ve mümkünse tahmini kazanımı kg CO₂e olarak belirt.
- Kullanıcının EN ÇOK etkilendiği alana odaklan.
- Suçlayıcı olma; küçük ve gerçekçi adımlar öner.
- SADECE şu JSON formatında yanıt ver, başka hiçbir şey yazma:
{"tips": ["öneri 1", "öneri 2", ..., "öneri 8"]}"""


# ------------------------------------------------------------------ LLM
def _extract_tips(text: str) -> list[str] | None:
    """Model çıktısından JSON tips listesini güvenle ayıkla."""
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        data = json.loads(text)
        tips = data.get("tips")
        if isinstance(tips, list) and len(tips) >= 3:
            return [str(t).strip() for t in tips]
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def _user_prompt(insight: dict) -> str:
    return (
        "Kullanıcının haftalık özeti:\n"
        f"- Toplam: {insight['week_total_kg']} kg CO₂e "
        f"(günlük ort. {insight['daily_avg_kg']} kg)\n"
        f"- Kategori kırılımı: {insight['by_category']}\n"
        f"- Alt kırılım: {insight['by_subtype']}\n"
        f"- Haftalık değişim: {insight['week_change_pct']}%\n"
        f"- İçgörü özeti: {insight['summary']}\n"
        "8 kişisel öneri üret."
    )


def _call_gemini(insight: dict) -> list[str] | None:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": _user_prompt(insight)}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 400},
    }
    resp = httpx.post(url, json=body, timeout=config.LLM_TIMEOUT_SECONDS)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _extract_tips(text)


def _call_openai(insight: dict) -> list[str] | None:
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={
            "model": config.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(insight)},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        },
        timeout=config.LLM_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _extract_tips(text)


def _call_groq(insight: dict) -> list[str] | None:
    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": config.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(insight)},
            ],
            "temperature": 0.7,
            "max_tokens": 500,
        },
        timeout=config.LLM_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _extract_tips(text)


# --------------------------------------------------------- kural tabanlı
def _rule_based(insight: dict) -> list[str]:
    """API anahtarı yokken/hata anında çalışan deterministik koç."""
    tips: list[str] = []
    subs = insight.get("by_subtype", {})
    top_cat = insight.get("top_category")
    week_total = insight.get("week_total_kg", 0)

    car_kg = sum(v for k, v in subs.items() if k.startswith("car_") or k == "motorcycle")
    if car_kg > 0:
        save = round(car_kg * 0.3, 1)
        tips.append(f"Bu hafta 2 kısa araba yolculuğunu toplu taşıma veya "
                    f"bisikletle değiştirin — yaklaşık {save} kg CO₂e kazanç.")
    if top_cat == "electricity" or subs.get("grid", 0) > 0:
        tips.append("Kullanmadığınız cihazları bekleme modunda bırakmayın; "
                    "prizden çekmek aylık 8–12 kWh (≈4–6 kg CO₂e) tasarruf sağlar.")
    if "plane_domestic" in subs:
        tips.append("Bir sonraki şehirlerarası yolculuk için treni değerlendirin — "
                    "km başına uçağa göre yaklaşık 6 kat daha az emisyon.")
    if week_total == 0:
        tips = [
            "Toplu Taşıma Kahramanı — Bugün gideceğiniz mesafede şahsi araç yerine otobüs veya metroyu tercih edin — yaklaşık 4.2 kg CO₂e kazanç.",
            "Cihazları Prizden Çek — Evde bekleme modunda (standby) kalan cihazları kullanmadığınız süre boyunca prizden çıkarın — yaklaşık 1.8 kg CO₂e kazanç.",
            "Atıkları Ayrıştırın — Plastik, kağıt ve metal ambalaj atıklarınızı çöpe atmak yerine geri dönüşüm kutularında biriktirin — yaklaşık 2.5 kg CO₂e kazanç.",
        ]
    
    defaults = [
        "Çamaşır makinesini 30°C'de ve tam dolu çalıştırın — yıkama başına ~0.6 kg CO₂e kazanç.",
        "Haftada bir günü 'arabasız gün' ilan edin; küçük rutinler büyük fark yaratır — yaklaşık 5.0 kg CO₂e kazanç.",
        "Aydınlatmada akkor ampul yerine LED tercih edin — ampul başına yılda ~25 kg CO₂e kazanç.",
        "Kombiyi/klimayı 1 derece kısmak yıllık enerji tüketimini %5–8 azaltır — yaklaşık 1.2 kg CO₂e kazanç.",
        "Gereksiz e-postaları silerek dijital karbon ayak izinizi azaltın — her 100 e-posta için yaklaşık 0.3 kg CO₂e kazanç.",
        "Daha kısa duş alarak hem su hem enerji tasarrufu yapın — duş başına yaklaşık 0.8 kg CO₂e kazanç.",
        "Kırmızı et tüketimini haftada bir öğün azaltıp sebze tercih edin — öğün başına yaklaşık 1.9 kg CO₂e kazanç.",
        "Bulaşıkları elde yıkamak yerine bulaşık makinesinde yıkayın — yıkama başına yaklaşık 1.5 kg CO₂e kazanç.",
        "Gıda israfını önlemek için haftalık alışveriş planı yapın — haftalık yaklaşık 3.5 kg CO₂e kazanç.",
    ]
    for d in defaults:
        if len(tips) >= 8:
            break
        if d not in tips:
            tips.append(d)
    return tips[:8]


# ------------------------------------------------------------- public API
def generate_tips(username: str, insight: dict) -> dict:
    """8 öneri üretir, günün görevleri olarak hafızaya yazar."""
    provider = "rule_based"
    tips: list[str] | None = None

    if config.GROQ_API_KEY:
        try:
            tips = _call_groq(insight)
            provider = "groq"
        except Exception:
            tips = None
    if tips is None and config.GEMINI_API_KEY:
        try:
            tips = _call_gemini(insight)
            provider = "gemini"
        except Exception:
            tips = None
    if tips is None and config.OPENAI_API_KEY:
        try:
            tips = _call_openai(insight)
            provider = "openai"
        except Exception:
            tips = None
    if tips is None:
        tips = _rule_based(insight)
        provider = "rule_based"

    # Ensure we have exactly 8 tips
    if len(tips) < 8:
        defaults = [
            "Çamaşır makinesini 30°C'de ve tam dolu çalıştırın — yıkama başına ~0.6 kg CO₂e kazanç.",
            "Haftada bir günü 'arabasız gün' ilan edin; küçük rutinler büyük fark yaratır — yaklaşık 5.0 kg CO₂e kazanç.",
            "Aydınlatmada akkor ampul yerine LED tercih edin — ampul başına yılda ~25 kg CO₂e kazanç.",
            "Kombiyi/klimayı 1 derece kısmak yıllık enerji tüketimini %5–8 azaltır — yaklaşık 1.2 kg CO₂e kazanç.",
            "Gereksiz e-postaları silerek dijital karbon ayak izinizi azaltın — her 100 e-posta için yaklaşık 0.3 kg CO₂e kazanç.",
            "Daha kısa duş alarak hem su hem enerji tasarrufu yapın — duş başına yaklaşık 0.8 kg CO₂e kazanç.",
            "Kırmızı et tüketimini haftada bir öğün azaltıp sebze tercih edin — öğün başına yaklaşık 1.9 kg CO₂e kazanç.",
            "Bulaşıkları elde yıkamak yerine bulaşık makinesinde yıkayın — yıkama başına yaklaşık 1.5 kg CO₂e kazanç.",
        ]
        for d in defaults:
            if len(tips) >= 8:
                break
            if d not in tips:
                tips.append(d)
    else:
        tips = tips[:8]

    from datetime import date
    saved = db.save_tasks(username, tips, date.today().isoformat())
    return {"provider": provider, "tips": saved}
