import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
from datetime import date
import calendar as pycal


app = Flask(__name__)
app.secret_key = "change-me"  # fine for local use

DB_PATH = os.environ.get("DATABASE_PATH", "jumper.db")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        # batches
        conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
        """)

        # spiders
        conn.execute("""
        CREATE TABLE IF NOT EXISTS spiders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            UNIQUE(batch_id, number)
        )
        """)

        # day-level log
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

        # NEW: highlight colors per spider per batch
        conn.execute("""
        CREATE TABLE IF NOT EXISTS spider_highlight (
            batch_id INTEGER NOT NULL,
            spider_id INTEGER NOT NULL,
            color TEXT DEFAULT '',
            PRIMARY KEY (batch_id, spider_id)
        )
        """)

        # NEW: batch metadata (last fed section)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS batch_meta (
            batch_id INTEGER PRIMARY KEY,
            last_fed_color TEXT DEFAULT ''
        )
        """)

        conn.commit()


@app.before_request
def _startup():
    init_db()


def get_latest_batch_id():
    with connect() as conn:
        row = conn.execute("SELECT id FROM batches ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def get_batch_id_for_spider(spider_id: int):
    with connect() as conn:
        row = conn.execute("SELECT batch_id FROM spiders WHERE id=?", (spider_id,)).fetchone()
    return row["batch_id"] if row else None


# -------------- ROUTES -------------- #

@app.route("/")
def home():
    return redirect(url_for("today"))


@app.route("/today")
def today():
    batch_id = session.get("last_batch")
    if not batch_id:
        batch_id = get_latest_batch_id()
        if batch_id:
            session["last_batch"] = batch_id

    if not batch_id:
        return redirect(url_for("batches"))

    return redirect(url_for("batch_view", batch_id=batch_id))


@app.route("/batches")
def batches():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC").fetchall()
    return render_template("batches.html", batches=rows)


@app.route("/batch/<int:batch_id>")
def batch_view(batch_id: int):
    today_iso = date.today().isoformat()
    session["last_batch"] = batch_id

    # 26+ colors (neutral-friendly but distinct)
    color_options = [
        "#2563EB", "#1D4ED8", "#0EA5E9", "#06B6D4", "#14B8A6", "#22C55E", "#16A34A",
        "#84CC16", "#EAB308", "#F59E0B", "#F97316", "#EF4444", "#DC2626", "#FB7185",
        "#A855F7", "#7C3AED", "#6366F1", "#4F46E5", "#0F766E", "#15803D", "#3F6212",
        "#92400E", "#7F1D1D", "#6B7280", "#111827", "#9333EA"
    ]

    with connect() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        spiders = conn.execute(
            "SELECT * FROM spiders WHERE batch_id=? ORDER BY number ASC",
            (batch_id,)
        ).fetchall()

        # today logs for popups
        logs = conn.execute("""
            SELECT spider_id, fed, ate, watered, molting, molts_count, notes
            FROM spiderlog
            WHERE day=? AND spider_id IN (
                SELECT id FROM spiders WHERE batch_id=?
            )
        """, (today_iso, batch_id)).fetchall()
        log_map = {r["spider_id"]: r for r in logs}

        # highlights
        hrows = conn.execute("""
            SELECT spider_id, color FROM spider_highlight
            WHERE batch_id=?
        """, (batch_id,)).fetchall()
        highlight_map = {r["spider_id"]: (r["color"] or "") for r in hrows}

        # last fed color
        mrow = conn.execute("SELECT last_fed_color FROM batch_meta WHERE batch_id=?", (batch_id,)).fetchone()
        last_fed_color = (mrow["last_fed_color"] if mrow else "") or ""

    return render_template(
        "batch_view.html",
        batch=batch,
        spiders=spiders,
        day=today_iso,
        log_map=log_map,
        highlight_map=highlight_map,
        last_fed_color=last_fed_color,
        color_options=color_options
    )


@app.route("/spiderlog/<int:spider_id>/<day>", methods=["POST"])
def save_spiderlog(spider_id: int, day: str):
    fed = request.form.get("fed", "no")
    ate = request.form.get("ate", "no")
    watered = request.form.get("watered", "no")
    molting = request.form.get("molting", "no")

    molts_count_raw = request.form.get("molts_count", "0")
    notes = request.form.get("notes", "")
    apply_all = request.form.get("apply_all", "0")  # "1" means apply to all spiders

    try:
        molts_count = int(molts_count_raw)
    except:
        molts_count = 0

    # enforce allowed values
    if fed not in ("yes", "no"): fed = "no"
    if ate not in ("yes", "no"): ate = "no"
    if watered not in ("yes", "no"): watered = "no"
    if molting not in ("yes", "no"): molting = "no"

    def upsert_one(conn, sid):
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
        if apply_all == "1":
            batch_id = get_batch_id_for_spider(spider_id)
            if batch_id:
                srows = conn.execute("SELECT id FROM spiders WHERE batch_id=? ORDER BY number ASC", (batch_id,)).fetchall()
                for r in srows:
                    upsert_one(conn, r["id"])
            else:
                upsert_one(conn, spider_id)
        else:
            upsert_one(conn, spider_id)

        conn.commit()

    back = request.form.get("back", "")
    if back == "calendar":
        y = int(day[:4])
        m = int(day[5:7])
        return redirect(url_for("calendar", year=y, month=m))

    return redirect(url_for("today"))


@app.route("/set_highlight", methods=["POST"])
def set_highlight():
    spider_id = int(request.form.get("spider_id", "0") or 0)
    color = (request.form.get("color", "") or "").strip()

    if spider_id <= 0:
        return jsonify({"ok": False}), 400

    batch_id = get_batch_id_for_spider(spider_id)
    if not batch_id:
        return jsonify({"ok": False}), 400

    with connect() as conn:
        conn.execute("""
            INSERT INTO spider_highlight(batch_id, spider_id, color)
            VALUES(?,?,?)
            ON CONFLICT(batch_id, spider_id) DO UPDATE SET color=excluded.color
        """, (batch_id, spider_id, color))
        conn.commit()

    return jsonify({"ok": True})


@app.route("/set_last_fed", methods=["POST"])
def set_last_fed():
    batch_id = int(request.form.get("batch_id", "0") or 0)
    color = (request.form.get("last_fed_color", "") or "").strip()

    if batch_id <= 0:
        return jsonify({"ok": False}), 400

    with connect() as conn:
        conn.execute("""
            INSERT INTO batch_meta(batch_id, last_fed_color)
            VALUES(?,?)
            ON CONFLICT(batch_id) DO UPDATE SET last_fed_color=excluded.last_fed_color
        """, (batch_id, color))
        conn.commit()

    return jsonify({"ok": True})


@app.route("/calendar")
@app.route("/calendar/<int:year>/<int:month>")
def calendar(year=None, month=None):
    today_dt = date.today()
    if year is None:
        year = today_dt.year
    if month is None:
        month = today_dt.month

    cal = pycal.Calendar(firstweekday=6)  # Sunday start
    weeks = cal.monthdatescalendar(year, month)
    month_name = pycal.month_name[month]

    start = weeks[0][0].isoformat()
    end = weeks[-1][-1].isoformat()

    batch_id = session.get("last_batch") or get_latest_batch_id()
    if batch_id:
        session["last_batch"] = batch_id

    with connect() as conn:
        day_rows = conn.execute("""
            SELECT * FROM daylog
            WHERE day BETWEEN ? AND ?
        """, (start, end)).fetchall()

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
        month_name=month_name,
        weeks=weeks,
        today=today_dt,
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

    y = int(day[:4])
    m = int(day[5:7])
    return redirect(url_for("calendar", year=y, month=m))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=false)