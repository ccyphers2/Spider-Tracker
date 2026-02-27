import os
import sqlite3
from datetime import date, datetime
import calendar as pycal

from flask import Flask, render_template, request, redirect, url_for, session, abort

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")

# On Render: set DB_PATH to /var/data/jumper.db if you attach a persistent disk
DB_PATH = os.environ.get("DB_PATH", "jumper.db")

COLOR_OPTIONS = [
    "#ef4444", "#f97316", "#f59e0b", "#22c55e", "#06b6d4",
    "#3b82f6", "#8b5cf6", "#ec4899", "#111827", "#94a3b8",
]


def _ensure_db_dir():
    folder = os.path.dirname(DB_PATH)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)


def connect():
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _col_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def init_db():
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            last_fed_color TEXT DEFAULT ''
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS spiders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            UNIQUE(batch_id, number)
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS highlights (
            spider_id INTEGER PRIMARY KEY,
            color TEXT DEFAULT ''
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS daylog (
            day TEXT PRIMARY KEY,
            watered INTEGER DEFAULT 0,
            sprays INTEGER DEFAULT 0,
            feeder TEXT DEFAULT '',
            note TEXT DEFAULT ''
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS spiderlog (
            spider_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            fed TEXT DEFAULT 'no',
            ate TEXT DEFAULT 'no',
            watered TEXT DEFAULT 'no',
            molting TEXT DEFAULT 'no',
            molts_count INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            PRIMARY KEY (spider_id, day)
        )
        """)

        # Add abdomen/booty field safely if DB already exists
        if not _col_exists(conn, "spiderlog", "booty"):
            conn.execute("ALTER TABLE spiderlog ADD COLUMN booty INTEGER DEFAULT 3")

        conn.commit()


@app.before_request
def _startup():
    init_db()


def _get_last_batch_id():
    lb = session.get("last_batch")
    if lb:
        return lb
    with connect() as conn:
        row = conn.execute("SELECT id FROM batches ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


@app.route("/")
def home():
    return redirect(url_for("batches"))


# ✅ Today = spider grid for today
@app.route("/today")
def today_route():
    batch_id = _get_last_batch_id()
    if not batch_id:
        return redirect(url_for("batches"))
    return redirect(url_for("batch_view_day", batch_id=batch_id, day=date.today().isoformat()))


# ---------- BATCHES ----------

@app.route("/batches", methods=["GET"])
def batches():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC").fetchall()
        counts = conn.execute("""
            SELECT batch_id, COUNT(*) AS c
            FROM spiders
            GROUP BY batch_id
        """).fetchall()
    count_map = {r["batch_id"]: r["c"] for r in counts}
    return render_template("batches.html", batches=rows, count_map=count_map)


@app.route("/create_batch", methods=["POST"])
def create_batch():
    name = (request.form.get("name") or "").strip()
    count_raw = (request.form.get("count") or "").strip()

    if not name:
        return redirect(url_for("batches"))

    try:
        count = int(count_raw)
    except:
        count = 0

    if count < 1:
        return redirect(url_for("batches"))

    with connect() as conn:
        cur = conn.execute("INSERT INTO batches (name, last_fed_color) VALUES (?, '')", (name,))
        batch_id = cur.lastrowid
        for n in range(1, count + 1):
            conn.execute(
                "INSERT OR IGNORE INTO spiders (batch_id, number) VALUES (?, ?)",
                (batch_id, n)
            )
        conn.commit()

    session["last_batch"] = batch_id
    return redirect(url_for("batch_view", batch_id=batch_id))


@app.route("/delete_batch/<int:batch_id>", methods=["GET"])
def delete_batch(batch_id: int):
    with connect() as conn:
        spider_ids = conn.execute("SELECT id FROM spiders WHERE batch_id=?", (batch_id,)).fetchall()
        spider_ids = [r["id"] for r in spider_ids]

        if spider_ids:
            q = ",".join(["?"] * len(spider_ids))
            conn.execute(f"DELETE FROM spiderlog WHERE spider_id IN ({q})", spider_ids)
            conn.execute(f"DELETE FROM highlights WHERE spider_id IN ({q})", spider_ids)

        conn.execute("DELETE FROM spiders WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
        conn.commit()

    if session.get("last_batch") == batch_id:
        session.pop("last_batch", None)

    return redirect(url_for("batches"))


# ---------- BATCH VIEW ----------

@app.route("/batch/<int:batch_id>")
def batch_view(batch_id: int):
    # default to today
    return redirect(url_for("batch_view_day", batch_id=batch_id, day=date.today().isoformat()))


@app.route("/batch/<int:batch_id>/<day>")
def batch_view_day(batch_id: int, day: str):
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except:
        abort(404)

    session["last_batch"] = batch_id

    with connect() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            abort(404)

        spiders = conn.execute(
            "SELECT * FROM spiders WHERE batch_id=? ORDER BY number ASC",
            (batch_id,)
        ).fetchall()

        logs = conn.execute("""
            SELECT spider_id, fed, ate, watered, molting, molts_count, notes, booty
            FROM spiderlog
            WHERE day=? AND spider_id IN (SELECT id FROM spiders WHERE batch_id=?)
        """, (day, batch_id)).fetchall()

        hl_rows = conn.execute("""
            SELECT h.spider_id, h.color
            FROM highlights h
            JOIN spiders s ON s.id = h.spider_id
            WHERE s.batch_id=?
        """, (batch_id,)).fetchall()

    log_map = {r["spider_id"]: r for r in logs}
    highlight_map = {r["spider_id"]: (r["color"] or "") for r in hl_rows}

    return render_template(
        "batch_view.html",
        batch=batch,
        spiders=spiders,
        day=day,
        log_map=log_map,
        highlight_map=highlight_map,
        color_options=COLOR_OPTIONS,
        last_fed_color=(batch["last_fed_color"] or "")
    )


# ---------- SAVE SPIDER LOG (supports apply_all) ----------

@app.route("/spiderlog/<int:spider_id>/<day>", methods=["POST"])
def save_spiderlog(spider_id: int, day: str):
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except:
        abort(404)

    fed = request.form.get("fed", "no")
    ate = request.form.get("ate", "no")
    watered = request.form.get("watered", "no")
    molting = request.form.get("molting", "no")
    notes = request.form.get("notes", "")

    try:
        molts_count = int(request.form.get("molts_count", "0"))
    except:
        molts_count = 0

    try:
        booty = int(request.form.get("booty", "3"))
    except:
        booty = 3
    booty = max(1, min(5, booty))

    apply_all = request.form.get("apply_all", "0") == "1"

    with connect() as conn:
        row = conn.execute("SELECT batch_id FROM spiders WHERE id=?", (spider_id,)).fetchone()
        if not row:
            abort(404)
        batch_id = row["batch_id"]

        if apply_all:
            spider_rows = conn.execute("SELECT id FROM spiders WHERE batch_id=?", (batch_id,)).fetchall()
            spider_ids = [r["id"] for r in spider_rows]
        else:
            spider_ids = [spider_id]

        for sid in spider_ids:
            conn.execute("""
                INSERT INTO spiderlog (spider_id, day, fed, ate, watered, molting, molts_count, notes, booty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spider_id, day) DO UPDATE SET
                    fed=excluded.fed,
                    ate=excluded.ate,
                    watered=excluded.watered,
                    molting=excluded.molting,
                    molts_count=excluded.molts_count,
                    notes=excluded.notes,
                    booty=excluded.booty
            """, (sid, day, fed, ate, watered, molting, molts_count, notes, booty))

        conn.commit()

    back = request.form.get("back", "batch")
    if back == "calendar":
        return redirect(url_for("calendar_view", year=int(day[:4]), month=int(day[5:7])))

    return redirect(url_for("batch_view_day", batch_id=batch_id, day=day))


# ---------- HIGHLIGHT + LAST FED ----------

@app.route("/set_highlight", methods=["POST"])
def set_highlight():
    try:
        spider_id = int(request.form.get("spider_id", "0"))
    except:
        return ("bad spider_id", 400)

    color = (request.form.get("color") or "").strip()
    if color and color not in COLOR_OPTIONS:
        return ("bad color", 400)

    with connect() as conn:
        conn.execute("""
            INSERT INTO highlights (spider_id, color)
            VALUES (?, ?)
            ON CONFLICT(spider_id) DO UPDATE SET color=excluded.color
        """, (spider_id, color))
        conn.commit()

    return ("ok", 200)


@app.route("/set_last_fed", methods=["POST"])
def set_last_fed():
    try:
        batch_id = int(request.form.get("batch_id", "0"))
    except:
        return ("bad batch_id", 400)

    color = (request.form.get("last_fed_color") or "").strip()
    if color and color not in COLOR_OPTIONS:
        return ("bad color", 400)

    with connect() as conn:
        conn.execute("UPDATE batches SET last_fed_color=? WHERE id=?", (color, batch_id))
        conn.commit()

    return ("ok", 200)


# ---------- CALENDAR (Month + Year visible; click day opens spider grid) ----------

@app.route("/calendar")
@app.route("/calendar/<int:year>/<int:month>")
def calendar_view(year=None, month=None):
    today = date.today()
    year = year or today.year
    month = month or today.month

    cal = pycal.Calendar(firstweekday=6)  # Sunday start
    weeks = cal.monthdatescalendar(year, month)

    month_name = pycal.month_name[month]

    prev_y, prev_m = year, month - 1
    next_y, next_m = year, month + 1
    if prev_m == 0:
        prev_m = 12
        prev_y -= 1
    if next_m == 13:
        next_m = 1
        next_y += 1

    batch_id = _get_last_batch_id()

    # day_map is optional (kept for future use)
    with connect() as conn:
        start = weeks[0][0].isoformat()
        end = weeks[-1][-1].isoformat()
        day_rows = conn.execute("SELECT * FROM daylog WHERE day BETWEEN ? AND ?", (start, end)).fetchall()
    day_map = {r["day"]: r for r in day_rows}

    return render_template(
        "calendar.html",
        today=today,
        year=year,
        month=month,
        month_name=month_name,
        weeks=weeks,
        day_map=day_map,
        batch_id=batch_id,
        prev_y=prev_y, prev_m=prev_m,
        next_y=next_y, next_m=next_m
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)