import sqlite3
from datetime import date, timedelta, datetime, timezone
import random

from app import db
from app.emission_factors import TRANSPORT_FACTORS, ELECTRICITY_KG_PER_KWH

users_to_seed = [
    ("meliscan2002", "meliscan2002@gmail.com", "Melis Can"),
    ("demo", "demo@carbon.app", "Demo Kullanıcı")
]

# Ensure users exist and seed entries
with db.get_conn() as conn:
    # Also find any user variants created via Firebase Auth (e.g., meliscan2002_1, meliscan2002@gmail.com)
    existing_rows = conn.execute(
        "SELECT username, email FROM users WHERE LOWER(username) LIKE 'meliscan2002%' OR LOWER(email) LIKE 'meliscan2002%'"
    ).fetchall()
    extra_users = [row["username"] for row in existing_rows if row["username"] not in ["meliscan2002", "demo"]]

all_seed_usernames = ["meliscan2002", "demo"] + extra_users

for uname, email, full_name in users_to_seed:
    db.ensure_user(uname, email, full_name)

today = date.today()

# Sample activities per day pattern
# (distribute across past 20 days)
sample_days = [
    # (days_ago, category, subtype, amount, unit)
    (20, "transport", "bus", 18.0, "km"),
    (20, "electricity", "grid", 6.5, "kWh"),
    (19, "transport", "metro", 22.0, "km"),
    (19, "electricity", "grid", 8.0, "kWh"),
    (18, "transport", "car_petrol", 35.0, "km"),
    (18, "electricity", "grid", 12.0, "kWh"),
    (17, "transport", "bus", 14.0, "km"),
    (17, "electricity", "grid", 7.2, "kWh"),
    (16, "transport", "metro", 20.0, "km"),
    (16, "electricity", "grid", 9.0, "kWh"),
    (15, "transport", "tram", 12.0, "km"),
    (15, "electricity", "grid", 6.0, "kWh"),
    (14, "transport", "car_diesel", 28.0, "km"),
    (14, "electricity", "grid", 11.5, "kWh"),
    (13, "transport", "bus", 15.0, "km"),
    (13, "electricity", "grid", 8.5, "kWh"),
    (12, "transport", "metro", 25.0, "km"),
    (12, "electricity", "grid", 7.8, "kWh"),
    (11, "transport", "car_hybrid", 40.0, "km"),
    (11, "electricity", "grid", 10.0, "kWh"),
    (10, "transport", "bus", 16.0, "km"),
    (10, "electricity", "grid", 8.0, "kWh"),
    (9, "transport", "metro", 18.0, "km"),
    (9, "electricity", "grid", 9.2, "kWh"),
    (8, "transport", "car_electric", 30.0, "km"),
    (8, "electricity", "grid", 14.0, "kWh"),
    (7, "transport", "bus", 20.0, "km"),
    (7, "electricity", "grid", 7.5, "kWh"),
    (6, "transport", "metro", 24.0, "km"),
    (6, "electricity", "grid", 8.8, "kWh"),
    (5, "transport", "car_petrol", 25.0, "km"),
    (5, "electricity", "grid", 10.5, "kWh"),
    (4, "transport", "bus", 18.0, "km"),
    (4, "electricity", "grid", 6.8, "kWh"),
    (3, "transport", "metro", 21.0, "km"),
    (3, "electricity", "grid", 8.2, "kWh"),
    (2, "transport", "tram", 15.0, "km"),
    (2, "electricity", "grid", 9.5, "kWh"),
    (1, "transport", "car_hybrid", 30.0, "km"),
    (1, "electricity", "grid", 11.0, "kWh"),
    (0, "transport", "bus", 22.0, "km"),
    (0, "electricity", "grid", 7.0, "kWh"),
]

for username in all_seed_usernames:
    # Clear existing entries first to avoid duplicates
    with db.get_conn() as conn:
        conn.execute("DELETE FROM entries WHERE username = ?", (username,))
        conn.execute("DELETE FROM tasks WHERE username = ?", (username,))
    
    print(f"Seeding data for {username}...")
    for days_ago, cat, sub, amt, unit in sample_days:
        entry_date = (today - timedelta(days=days_ago)).isoformat()
        if cat == "transport":
            factor = TRANSPORT_FACTORS.get(sub, {}).get("kg_per_km", 0.15)
            co2 = amt * factor
        else:
            co2 = amt * ELECTRICITY_KG_PER_KWH
        
        db.add_entry(username, entry_date, cat, sub, amt, unit, round(co2, 3))
    
    # Add daily tasks for today
    today_str = today.isoformat()
    sample_tasks = [
        "Kısa mesafelerde yürümeyi veya bisiklet kullanmayı tercih edin.",
        "Kullanılmayan elektronik cihazların fişini prizden çekin.",
        "Toplu taşıma (metro/otobüs) tercih ederek bireysel araç kullanımını azaltın.",
        "Evde LED ampul kullanımına geçerek elektrik tüketimini düşürün.",
        "Asansör yerine merdiven tercih edin.",
        "Klima derecesini 1°C artırarak/azaltarak enerji tasarrufu yapın.",
        "Bulaşık makinesini tam dolmadan çalıştırmayın.",
        "Haftada en az 1 gün etsiz beslenmeyi deneyin."
    ]
    
    with db.get_conn() as conn:
        for idx, task_text in enumerate(sample_tasks):
            # Mark 2 tasks as completed to make the dashboard progress look active
            is_done = 1 if idx in (0, 3) else 0
            conn.execute(
                "INSERT INTO tasks (username, text, done, task_date, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, task_text, is_done, today_str, datetime.now(timezone.utc).isoformat())
            )

print("Demo data seeded successfully!")
