"""Karbon emisyon katsayıları.

Kaynaklar:
- Elektrik: Türkiye ulusal şebeke emisyon faktörü ~0.478 kg CO2e/kWh (ETKB/EVÇED)
- Ulaşım: DEFRA 2024 sera gazı dönüşüm faktörlerinden yuvarlanmış
  yaklaşık değerler (kg CO2e / yolcu-km).

Not: Elektrikli araç katsayısı, aracın km başına ~0.15 kWh tüketimi ile
Türkiye şebeke faktörünün çarpımından türetilmiştir.
"""

ELECTRICITY_KG_PER_KWH = 0.478

TRANSPORT_FACTORS = {
    # anahtar: (etiket, kg CO2e / km)
    "car_petrol":  {"label": "Otomobil (benzinli)",  "kg_per_km": 0.171},
    "car_diesel":  {"label": "Otomobil (dizel)",     "kg_per_km": 0.160},
    "car_hybrid":  {"label": "Otomobil (hibrit)",    "kg_per_km": 0.110},
    "car_ev":      {"label": "Otomobil (elektrikli)", "kg_per_km": 0.072},
    "motorcycle":  {"label": "Motosiklet",           "kg_per_km": 0.103},
    "bus":         {"label": "Otobüs",               "kg_per_km": 0.096},
    "minibus":     {"label": "Minibüs/Dolmuş",       "kg_per_km": 0.105},
    "metro":       {"label": "Metro/Tramvay",        "kg_per_km": 0.035},
    "train":       {"label": "Tren",                 "kg_per_km": 0.041},
    "plane_domestic": {"label": "Uçak (iç hat)",     "kg_per_km": 0.246},
    "walk_bike":   {"label": "Yürüyüş/Bisiklet",     "kg_per_km": 0.0},
}

# Anlaşılır eşdeğerler — Insight Agent'ın "somutlaştırma" özelliği için
EQUIVALENTS = {
    # 1 olgun ağacın yıllık ortalama CO2 tutumu ~21 kg
    "tree_year_kg": 21.0,
    # 1 km benzinli otomobil ~0.171 kg
    "car_km_kg": 0.171,
    # 1 fincan kahve ~0.28 kg (üretim dahil, yaklaşık)
    "coffee_cup_kg": 0.28,
    # 1 adet hamburger ~2.5 kg (yaklaşık)
    "burger_kg": 2.5,
}

# Türkiye kişi başı günlük ortalama (karşılaştırma için): ~14.8 kg CO2e/gün
TURKEY_DAILY_AVG_KG = 14.8
