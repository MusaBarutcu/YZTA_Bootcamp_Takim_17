import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    email           TEXT UNIQUE,
    password_hash   TEXT,
    full_name       TEXT,
    session_token   TEXT,
    firebase_uid    TEXT,
    daily_budget_kg REAL NOT NULL DEFAULT 15.0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    entry_date TEXT NOT NULL,            -- YYYY-MM-DD
    category   TEXT NOT NULL,            -- 'transport' | 'electricity'
    subtype    TEXT NOT NULL,            -- örn. 'car_petrol' | 'grid'
    amount     REAL NOT NULL,            -- km veya kWh
    unit       TEXT NOT NULL,            -- 'km' | 'kWh'
    co2_kg     REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_user_date ON entries (username, entry_date);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    task_date  TEXT NOT NULL,            -- görevin verildiği gün
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_date ON tasks (username, task_date);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migrate existing users table if missing new auth columns
        for col, col_type in [("email", "TEXT"), ("password_hash", "TEXT"), ("full_name", "TEXT"), ("session_token", "TEXT"), ("firebase_uid", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # column already exists


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return key.hex(), salt


# ---------------------------------------------------------------- users & auth
def register_user(username: str, email: str, password: str, full_name: str = "") -> dict:
    username = username.strip()
    email = email.strip().lower()
    if not username or not email or not password:
        raise ValueError("Kullanıcı adı, e-posta ve şifre zorunludur.")
    if len(username) < 3:
        raise ValueError("Kullanıcı adı en az 3 karakter olmalıdır.")
    if len(password) < 6:
        raise ValueError("Şifre en az 6 karakter olmalıdır.")

    pwd_hash, salt = _hash_password(password)
    stored_pwd = f"{salt}${pwd_hash}"
    token = secrets.token_hex(32)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT username, email FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?",
            (username.lower(), email),
        ).fetchone()
        if existing:
            if existing["username"].lower() == username.lower():
                raise ValueError("Bu kullanıcı adı zaten alınmış.")
            raise ValueError("Bu e-posta adresi zaten kayıtlı.")

        conn.execute(
            """INSERT INTO users (username, email, password_hash, full_name, session_token, daily_budget_kg, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, email, stored_pwd, full_name.strip() or username, token, config.DEFAULT_DAILY_BUDGET_KG, _now()),
        )

    return {
        "username": username,
        "email": email,
        "full_name": full_name.strip() or username,
        "token": token,
        "daily_budget_kg": config.DEFAULT_DAILY_BUDGET_KG,
        "points": get_user_points(username),
    }


def authenticate_user(username_or_email: str, password: str) -> dict:
    identifier = username_or_email.strip()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?",
            (identifier.lower(), identifier.lower()),
        ).fetchone()
        if not row or not row["password_hash"]:
            raise ValueError("Kullanıcı adı/e-posta veya şifre hatalı.")

        stored_pwd = row["password_hash"]
        if "$" not in stored_pwd:
            raise ValueError("Geçersiz şifre formatı.")

        salt, expected_hash = stored_pwd.split("$", 1)
        calc_hash, _ = _hash_password(password, salt)
        if secrets.compare_digest(calc_hash, expected_hash):
            token = secrets.token_hex(32)
            conn.execute(
                "UPDATE users SET session_token = ? WHERE username = ?",
                (token, row["username"]),
            )
            return {
                "username": row["username"],
                "email": row["email"] or "",
                "full_name": row["full_name"] or row["username"],
                "token": token,
                "daily_budget_kg": row["daily_budget_kg"],
                "points": get_user_points(row["username"]),
            }
        else:
            raise ValueError("Kullanıcı adı/e-posta veya şifre hatalı.")


def get_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE session_token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        return {
            "username": row["username"],
            "email": row["email"] or "",
            "full_name": row["full_name"] or row["username"],
            "token": token,
            "daily_budget_kg": row["daily_budget_kg"],
            "points": get_user_points(row["username"]),
        }


def sync_firebase_user(firebase_uid: str, email: str, full_name: str = "", display_username: str = None, password: str = None) -> dict:
    email = (email or "").strip().lower()
    if not email and not firebase_uid:
        raise ValueError("Firebase e-posta veya UID zorunludur.")

    pwd_hash_str = None
    if password:
        pwd_hash, salt = _hash_password(password)
        pwd_hash_str = f"{salt}${pwd_hash}"

    token = secrets.token_hex(32)
    base_uname = (email.split("@")[0] if "@" in email else "").strip().lower()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM users 
               WHERE firebase_uid = ? 
                  OR (email IS NOT NULL AND email != '' AND LOWER(email) = ?)
                  OR (LOWER(username) = ? AND (email IS NULL OR email = ''))""",
            (firebase_uid, email, base_uname),
        ).fetchone()

        if row:
            username = row["username"]
            if display_username and display_username.strip().lower() != username.lower():
                new_uname = display_username.strip()
                if not conn.execute("SELECT 1 FROM users WHERE LOWER(username) = ? AND username != ?", (new_uname.lower(), username)).fetchone():
                    username = new_uname

            conn.execute(
                """UPDATE users SET username = ?, firebase_uid = ?, session_token = ?,
                   password_hash = COALESCE(?, password_hash),
                   full_name = COALESCE(NULLIF(?, ''), full_name)
                   WHERE username = ? OR firebase_uid = ?""",
                (username, firebase_uid, token, pwd_hash_str, full_name.strip(), row["username"], firebase_uid),
            )
            budget = row["daily_budget_kg"]
            resolved_name = full_name.strip() or row["full_name"] or username
        else:
            if not display_username:
                base_name = email.split("@")[0] if "@" in email else f"user_{firebase_uid[:6]}"
                display_username = "".join(c for c in base_name if c.isalnum() or c == "_")
                if len(display_username) < 3:
                    display_username = f"user_{secrets.token_hex(3)}"

            final_user = display_username.strip()
            idx = 1
            while conn.execute("SELECT 1 FROM users WHERE LOWER(username) = ?", (final_user.lower(),)).fetchone():
                final_user = f"{display_username}_{idx}"
                idx += 1
            username = final_user
            resolved_name = full_name.strip() or username
            conn.execute(
                """INSERT INTO users (username, email, firebase_uid, password_hash, full_name, session_token, daily_budget_kg, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (username, email, firebase_uid, pwd_hash_str, resolved_name, token, config.DEFAULT_DAILY_BUDGET_KG, _now()),
            )
            budget = config.DEFAULT_DAILY_BUDGET_KG

    return {
        "username": username,
        "email": email,
        "full_name": resolved_name,
        "token": token,
        "daily_budget_kg": budget,
        "points": get_user_points(username),
    }


def get_email_by_identifier(identifier: str) -> str | None:
    identifier = identifier.strip().lower()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?",
            (identifier, identifier),
        ).fetchone()
        if row and row["email"]:
            return row["email"]
        return None




def logout_user(token: str) -> bool:
    if not token:
        return False
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET session_token = NULL WHERE session_token = ?", (token,)
        )
        return cur.rowcount > 0


def ensure_user(username: str, email: str = None, full_name: str = "") -> dict:
    email_clean = email.strip().lower() if email and email.strip() else None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = ?", (username.lower(),)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (username, email, full_name, daily_budget_kg, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, email_clean, full_name.strip() or username, config.DEFAULT_DAILY_BUDGET_KG, _now()),
            )
            budget = config.DEFAULT_DAILY_BUDGET_KG
            resolved_full_name = full_name.strip() or username
            resolved_email = email_clean or ""
        else:
            if email_clean and not row["email"]:
                conn.execute("UPDATE users SET email = ? WHERE username = ?", (email_clean, row["username"]))
            if full_name and not row["full_name"]:
                conn.execute("UPDATE users SET full_name = ? WHERE username = ?", (full_name.strip(), row["username"]))
            budget = row["daily_budget_kg"]
            resolved_full_name = full_name.strip() or row["full_name"] or username
            resolved_email = row["email"] or email_clean or ""

        return {
            "username": username,
            "email": resolved_email,
            "full_name": resolved_full_name,
            "daily_budget_kg": budget,
            "points": get_user_points(username),
        }


def set_budget(username: str, budget_kg: float) -> None:
    ensure_user(username)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET daily_budget_kg = ? WHERE username = ?",
            (budget_kg, username),
        )


# -------------------------------------------------------------- entries
def add_entry(username: str, entry_date: str, category: str, subtype: str,
              amount: float, unit: str, co2_kg: float) -> int:
    ensure_user(username)
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO entries (username, entry_date, category, subtype,
                                    amount, unit, co2_kg, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, entry_date, category, subtype, amount, unit, co2_kg, _now()),
        )
        return cur.lastrowid


def delete_entry(username: str, entry_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM entries WHERE id = ? AND username = ?", (entry_id, username)
        )
        return cur.rowcount > 0


def _resolve_user(username: str) -> str:
    if not username:
        return "meliscan2002"
    with get_conn() as conn:
        c = conn.execute(
            "SELECT (SELECT COUNT(*) FROM entries WHERE username = ?) + (SELECT COUNT(*) FROM tasks WHERE username = ?)",
            (username, username),
        ).fetchone()[0]
        if c > 0:
            return username
        d = conn.execute("SELECT COUNT(*) FROM entries WHERE username = 'meliscan2002'").fetchone()[0]
        if d > 0:
            return "meliscan2002"
        return username


def entries_between(username: str, start: str, end: str) -> list[dict]:
    target_user = _resolve_user(username)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM entries
               WHERE username = ? AND entry_date BETWEEN ? AND ?
               ORDER BY entry_date DESC, id DESC""",
            (target_user, start, end),
        ).fetchall()
        return [dict(r) for r in rows]


def all_entries(username: str) -> list[dict]:
    target_user = _resolve_user(username)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE username = ? ORDER BY entry_date DESC, id DESC",
            (target_user,),
        ).fetchall()
        return [dict(r) for r in rows]


def daily_totals(username: str, days: int = 30) -> list[dict]:
    target_user = _resolve_user(username)
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT entry_date, category, SUM(co2_kg) AS total
               FROM entries
               WHERE username = ? AND entry_date >= ?
               GROUP BY entry_date, category
               ORDER BY entry_date""",
            (target_user, start),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- tasks
def save_tasks(username: str, texts: list[str], task_date: str) -> list[dict]:
    ensure_user(username)
    saved = []
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM tasks WHERE username = ? AND task_date = ? AND done = 0",
            (username, task_date),
        )
        for t in texts:
            cur = conn.execute(
                "INSERT INTO tasks (username, text, done, task_date, created_at) VALUES (?, ?, 0, ?, ?)",
                (username, t, task_date, _now()),
            )
            saved.append({"id": cur.lastrowid, "text": t, "done": 0})
    return saved


def tasks_for_day(username: str, task_date: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, done FROM tasks WHERE username = ? AND task_date = ? ORDER BY id",
            (username, task_date),
        ).fetchall()
        if not rows and username != "meliscan2002":
            user_exists = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not user_exists:
                rows = conn.execute(
                    "SELECT id, text, done FROM tasks WHERE username = 'meliscan2002' AND task_date = ? ORDER BY id",
                    (task_date,),
                ).fetchall()
        return [dict(r) for r in rows]


def complete_task(username: str, task_id: int, done: int = 1) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE tasks SET done = ? WHERE id = ? AND username = ?",
            (done, task_id, username),
        )
        return cur.rowcount > 0


def delete_task(username: str, task_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE id = ? AND username = ?",
            (task_id, username),
        )
        return cur.rowcount > 0


def reset_tasks_for_day(username: str, task_date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET done = 0 WHERE username = ? AND task_date = ?",
            (username, task_date),
        )


def streak_days(username: str) -> int:
    """Ardışık kaç gündür en az bir görev tamamlanmış veya veri girilmiş."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT d FROM (
                   SELECT entry_date AS d FROM entries WHERE username = ?
                   UNION
                   SELECT task_date AS d FROM tasks WHERE username = ? AND done = 1
               ) ORDER BY d DESC""",
            (username, username),
        ).fetchall()
    days = [r["d"] for r in rows]
    streak, cursor = 0, date.today()
    day_set = set(days)
    while cursor.isoformat() in day_set:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def get_user_points(username: str) -> int:
    with get_conn() as conn:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE username = ? AND done = 1",
            (username,)
        ).fetchone()[0]
        entry_count = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE username = ?",
            (username,)
        ).fetchone()[0]
    
    return 100 + (task_count * 15) + (entry_count * 10)


def seed_user_if_empty(username: str) -> None:
    from .emission_factors import TRANSPORT_FACTORS, ELECTRICITY_KG_PER_KWH
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE username = ?", (username,)
        ).fetchone()[0]
        if count == 0:
            today = date.today()
            sample_days = [
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
            for days_ago, cat, sub, amt, unit in sample_days:
                entry_date = (today - timedelta(days=days_ago)).isoformat()
                if cat == "transport":
                    factor = TRANSPORT_FACTORS.get(sub, {}).get("kg_per_km", 0.15)
                    co2 = amt * factor
                else:
                    co2 = amt * ELECTRICITY_KG_PER_KWH
                add_entry(username, entry_date, cat, sub, amt, unit, round(co2, 3))
