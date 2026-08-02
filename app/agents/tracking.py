"""Tracking Agent (Takip Ajanı).

Kullanıcının ulaşım (km + araç tipi) ve elektrik (kWh) girdilerini alır,
anlık karbon ayak izini hesaplar ve hafızaya (SQLite) yazar.

Formüller:
- Ulaşım:  km  × araç katsayısı (kg CO2e/km)
- Elektrik: kWh × 0.478 kg CO2e/kWh (Türkiye ulusal şebeke — ETKB/EVÇED)

İsteğe bağlı: CLIMATIQ_API_KEY tanımlıysa ulaşım katsayısı Climatiq
API'sinden canlı çekilebilir; hata durumunda yerel DEFRA tablosuna düşer.
"""
import os
from datetime import date

import httpx

from .. import db
from ..emission_factors import ELECTRICITY_KG_PER_KWH, TRANSPORT_FACTORS

CLIMATIQ_API_KEY = os.getenv("CLIMATIQ_API_KEY", "").strip()


class TrackingError(ValueError):
    """Geçersiz kullanıcı girdisi."""


def _validate_amount(amount: float, unit: str) -> float:
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise TrackingError(f"Geçersiz miktar: sayı bekleniyor ({unit}).")
    if amount <= 0:
        raise TrackingError(f"Miktar pozitif olmalı ({unit}).")
    if unit == "km" and amount > 20000:
        raise TrackingError("Tek girişte 20.000 km üstü kabul edilmiyor.")
    if unit == "kWh" and amount > 100000:
        raise TrackingError("Tek girişte 100.000 kWh üstü kabul edilmiyor.")
    return amount


def _climatiq_factor(subtype: str) -> float | None:
    """Climatiq'ten katsayı dene; başarısızsa None döner (yerel tabloya düşülür)."""
    if not CLIMATIQ_API_KEY:
        return None
    try:
        resp = httpx.post(
            "https://api.climatiq.io/data/v1/estimate",
            headers={"Authorization": f"Bearer {CLIMATIQ_API_KEY}"},
            json={
                "emission_factor": {
                    "activity_id": f"passenger_vehicle-vehicle_type_{subtype}",
                    "data_version": "^21",
                },
                "parameters": {"distance": 1, "distance_unit": "km"},
            },
            timeout=8,
        )
        resp.raise_for_status()
        return float(resp.json()["co2e"])
    except Exception:
        return None


def track_transport(username: str, subtype: str, km: float,
                    entry_date: str | None = None) -> dict:
    if subtype not in TRANSPORT_FACTORS:
        raise TrackingError(f"Bilinmeyen araç tipi: {subtype}")
    km = _validate_amount(km, "km")
    entry_date = entry_date or date.today().isoformat()

    factor = _climatiq_factor(subtype) or TRANSPORT_FACTORS[subtype]["kg_per_km"]
    co2 = round(km * factor, 3)
    entry_id = db.add_entry(username, entry_date, "transport", subtype, km, "km", co2)
    return {
        "id": entry_id,
        "category": "transport",
        "subtype": subtype,
        "label": TRANSPORT_FACTORS[subtype]["label"],
        "amount": km,
        "unit": "km",
        "factor": factor,
        "co2_kg": co2,
        "entry_date": entry_date,
    }


def track_electricity(username: str, kwh: float,
                      entry_date: str | None = None) -> dict:
    kwh = _validate_amount(kwh, "kWh")
    entry_date = entry_date or date.today().isoformat()
    co2 = round(kwh * ELECTRICITY_KG_PER_KWH, 3)
    entry_id = db.add_entry(username, entry_date, "electricity", "grid", kwh, "kWh", co2)
    return {
        "id": entry_id,
        "category": "electricity",
        "subtype": "grid",
        "label": "Elektrik (şebeke)",
        "amount": kwh,
        "unit": "kWh",
        "factor": ELECTRICITY_KG_PER_KWH,
        "co2_kg": co2,
        "entry_date": entry_date,
    }
