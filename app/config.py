"""CarbOn — uygulama yapılandırması.

Tüm ayarlar ortam değişkenlerinden okunur; hiçbir gizli anahtar koda gömülmez.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

# Veritabanı
DB_PATH = os.getenv("CARBON_DB_PATH", str(BASE_DIR / "carbon.db"))

# LLM sağlayıcı anahtarları (opsiyonel — yoksa kural tabanlı koç devreye girer)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))

# Günlük karbon bütçesi (kg CO2e) — Türkiye kişi başı ortalamasından türetilmiş
# (~5.4 t/yıl ≈ 14.8 kg/gün). Kullanıcı arayüzden değiştirebilir.
DEFAULT_DAILY_BUDGET_KG = float(os.getenv("CARBON_DAILY_BUDGET_KG", "15.0"))

# Firebase Ayarları (Opsiyonel — tanımlı ise ön yüzde Firebase Auth aktifleşir)
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "").strip()
FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "").strip()
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
FIREBASE_MESSAGING_SENDER_ID = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "").strip()
FIREBASE_APP_ID = os.getenv("FIREBASE_APP_ID", "").strip()

# Sunucu
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

