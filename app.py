import os
import sqlite3
from datetime import date
import calendar as pycal

from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")

# Render persistent disk later: set DB_PATH to /var/data/jumper.db
DB_PATH = os.environ.get("DB_PATH", "jumper.db")

# Palette used by batch_view
COLOR_OPTIONS = [
    "#FF6B6B", "#FFA94D", "#FFD43B", "#69DB7C", "#38D9A9",
    "#4DABF7", "#748FFC", "#B197FC", "#F783AC", "#ADB5BD"
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


def _column_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def init_db():
    with connect() as conn:
        # batches now includes spider_count + last_fed_color (your UI wants it)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
        """)

        # Add missing columns safely (works even if DB already exists)
        if not _column_exists(conn, "batches", "spider_count"):
            conn.execute("ALTER TABLE batches ADD COLUMN spider_count INTEGER DEFAULT 0")
        if not _column_exists(conn, "batches", "last_fed_color"):
            conn.execute("ALTER TABLE batches ADD COLUMN last_fed_color TEXT DEFAULT ''")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS spiders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            UNIQUE(batch_id, number)
        )
        """)

        # day-level log (calendar day)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS daylog (
            day TEXT PRIMARY KEY,
            watered INTEGER DEFAULT 0,
            sprays INTEGER DEFAULT 0,
            feeder TEXT DEFAULT '',
            note TEXT DEFAULT ''
        )
        """)

        # spider daily log (per spider per date)
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

        # NEW: highlight colors per spider (what your template expects)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS spider_highlight (
            spider_id INTEGER PRIMARY KEY,
            color TEXT DEFAULT ''
        )
        """)

        conn.commit()


@app.before_request
def _startup():
    init_db()


@app.route("/")
def home():
    return redirect(url_for("batches"))


# ---------- BATCHES LIST ----------

@app.route("/batches", methods=["GET"])
def batches():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC").fetchall()
    return render_template("batches.html", batches=rows)


@app.route("/create_batch", methods=["POST", "GET"])
def create_batch():
    if request.method == "GET":
        return redirect(url_for("batches"))

    name = (request.form.get("name") or "").strip()
    count_raw = (request.form.get("count") or "").strip()

    if not name:
        return redirect(url_for("batches"))

    try:
        count = int(count_raw)
    except:
        count = 0

    if count < 1:
        count = 1

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO batches (name, spider_count, last_fed_color) VALUES (?, ?, ?)",
            (name, count, "")
        )
        batch_id = cur.lastrowid

        for n in range(1, count + 1):
            conn.execute(
                "INSERT OR IGNORE INTO spiders (batch_id, number) VALUES (?, ?)",
                (batch_id, n)
            )
        conn.commit()

    session["last_batch"] = batch_id
    return redirect(url_for("batch_view", batch_id=batch_id))


@app.route("/delete_batch/<int:batch_id>", methods=["GET", "POST"])
def delete_batch(batch_id: int):
    with connect() as conn:
        spider_ids = conn.execute("SELECT id FROM spiders WHERE batch_id=?", (batch_id,)).fetchall()
        spider_ids = [r["id"] for r in spider_ids]

        if spider_ids:
            q = ",".join(["?"] * len(spider_ids))
            conn.execute(f"DELETE FROM spiderlog WHERE spider_id IN ({q})", spider_ids)
            conn.execute(f"DELETE FROM spider_highlight WHERE spider_id IN ({q})", spider_ids)

        conn.execute("DELETE FROM spiders WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
        conn.commit()

    if session.get("last_batch") == batch_id:
        session.pop("last_batch", None)

    return redirect(url_for("batches"))


# ---------- BATCH VIEW ----------

@app.route("/batch/<int:batch_id>")
def batch_view(batch_id: int):
    today = date.today().isoformat()
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
            SELECT spider_id, fed, ate, watered, molting, molts_count, notes
            FROM spiderlog
            WHERE day=? AND spider_id IN (SELECT id FROM spiders WHERE batch_id=?)
        """, (today, batch_id)).fetchall()

        # highlight colors
        hl_rows = conn.execute("""
            SELECT sh.spider_id, sh.color
            FROM spider_highlight sh
            JOIN spiders s ON s.id = sh.spider_id
            WHERE s.batch_id=?
        """, (batch_id,)).fetchall()

    log_map = {r["spider_id"]: r for r in logs}
    highlight_map = {r["spider_id"]: (r["color"] or "") for r in hl_rows}

    return render_template(
        "batch_view.html",
        batch=batch,
        spiders=spiders,
        day=today,
        log_map=log_map,
        highlight_map=highlight_map,
        color_options=COLOR_OPTIONS,
        last_fed_color=(batch["last_fed_color"] or "")
    )


# ---------- HIGHLIGHT + LAST FED ROUTES (your template calls these) ----------

@app.route("/set_highlight", methods=["POST"])
def set_highlight():
    spider_id = request.form.get("spider_id", "").strip()
    color = request.form.get("color", "").strip()

    try:
        spider_id_int = int(spider_id)
    except:
        return ("bad spider_id", 400)

    with connect() as conn:
        # Upsert
        conn.execute("""
            INSERT INTO spider_highlight (spider_id, color)
            VALUES (?, ?)
            ON CONFLICT(spider_id) DO UPDATE SET color=excluded.color
        """, (spider_id_int, color))
        conn.commit()

    return ("ok", 200)


@app.route("/set_last_fed", methods=["POST"])
def set_last_fed():
    batch_id = request.form.get("batch_id", "").strip()
    color = request.form.get("last_fed_color", "").strip()

    try:
        batch_id_int = int(batch_id)
    except:
        return ("bad batch_id", 400)

    with connect() as conn:
        conn.execute("UPDATE batches SET last_fed_color=? WHERE id=?", (color, batch_id_int))
        conn.commit()

    return ("ok", 200)


# ---------- SPIDER LOG (supports apply_all) ----------

@app.route("/spiderlog/<int:spider_id>/<day>", methods=["POST"])
def save_spiderlog(spider_id: int, day: str):
    fed = request.form.get("fed", "no")
    ate = request.form.get("ate", "no")
    watered = request.form.get("watered", "no")
    molting = request.form.get("molting", "no")
    molts_count = request.form.get("molts_count", "0")
    notes = request.form.get("notes", "")

    apply_all = (request.form.get("apply_all", "0") == "1")

    try:
        molts_count = int(molts_count)
    except:
        molts_count = 0

    def upsert_one(conn, sid: int):
        conn.execute("""
            INSERT INTO spiderlog (spider_id, day, fed, ate, watered, molting, molts_count, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(spider_id, day) DO UPDATE SET
              fed=excluded.fed,
              ate=excluded.ate,
              watered=excluded.watered,
              molting=excluded.molting,
              molts_count=excluded.molts_count,
              notes=excluded.notes
        """, (sid, day, fed, ate, watered, molting, molts_count, notes))

    with connect() as conn:
        if apply_all:
            # find the batch for this spider, then apply to every spider in that batch
            row = conn.execute("""
                SELECT batch_id FROM spiders WHERE id=?
            """, (spider_id,)).fetchone()
            if not row:
                abort(404)
            batch_id = row["batch_id"]

            srows = conn.execute("SELECT id FROM spiders WHERE batch_id=?", (batch_id,)).fetchall()
            for r in srows:
                upsert_one(conn, r["id"])
        else:
            upsert_one(conn, spider_id)

        conn.commit()

    back = request.form.get("back", "")
    if back == "calendar":
        return redirect(url_for("calendar", year=int(day[:4]), month=int(day[5:7])))

    # go back to last batch view
    return redirect(url_for("batch_view", batch_id=session.get("last_batch", 1)))


# ---------- CALENDAR (unchanged) ----------

@app.route("/calendar")
@app.route("/calendar/<int:year>/<int:month>")
def calendar(year=None, month=None):
    today = date.today()
    year = year or today.year
    month = month or today.month

    cal = pycal.Calendar(firstweekday=6)  # Sunday start
    weeks = cal.monthdatescalendar(year, month)

    start = weeks[0][0].isoformat()
    end = weeks[-1][-1].isoformat()

    with connect() as conn:
        day_rows = conn.execute("""
            SELECT * FROM daylog
            WHERE day BETWEEN ? AND ?
        """, (start, end)).fetchall()

        batch_id = session.get("last_batch")
        spiders = []
        if batch_id:
            spiders = conn.execute(
                "SELECT * FROM spiders WHERE batch_id=? ORDER BY number ASC",
                (batch_id,)
            ).fetchall()

        spider_logs = []
        if batch_id and spiders:
            spider_ids = [s["id"] for s in spiders]
            q_marks = ",".join(["?"] * len(spider_ids))
            spider_logs = conn.execute(f"""
                SELECT * FROM spiderlog
                WHERE day BETWEEN ? AND ?
                AND spider_id IN ({q_marks})
            """, (start, end, *spider_ids)).fetchall()

    day_map = {r["day"]: r for r in day_rows}
    slog_map = {(r["spider_id"], r["day"]): r for r in spider_logs}

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        weeks=weeks,
        today=today,
        day_map=day_map,
        spiders=spiders,
        slog_map=slog_map
    )


@app.route("/day/<day>")
def day(day: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM daylog WHERE day=?", (day,)).fetchone()
    return render_template("day.html", day=row or {"day": day})


@app.route("/save_day/<day>", methods=["POST"])
def save_day(day: str):
    watered = 1 if request.form.get("watered") else 0
    sprays = request.form.get("sprays", 0)
    feeder = request.form.get("feeder", "")
    note = request.form.get("note", "")

    with connect() as conn:
        conn.execute("""
            INSERT INTO daylog(day, watered, sprays, feeder, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
              watered=excluded.watered,
              sprays=excluded.sprays,
              feeder=excluded.feeder,
              note=excluded.note
        """, (day, watered, sprays, feeder, note))
        conn.commit()

    return redirect(url_for("calendar", year=int(day[:4]), month=int(day[5:7])))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)