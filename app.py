import os
import sqlite3
from datetime import date
import calendar as pycal

from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")  # set on Render later

# Allow Render to store DB on a persistent disk (recommended)
DB_PATH = os.environ.get("DB_PATH", "jumper.db")


def _ensure_db_dir():
    folder = os.path.dirname(DB_PATH)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)


def connect():
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
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
        conn.commit()


@app.before_request
def _startup():
    init_db()


@app.route("/")
def home():
    return redirect(url_for("batches"))


@app.route("/batches", methods=["GET"])
def batches():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC").fetchall()
    return render_template("batches.html", batches=rows)


# --- CREATE BATCH: add multiple aliases so your template can't "miss" the route ---
@app.route("/create_batch", methods=["POST"])
@app.route("/add_batch", methods=["POST"])
@app.route("/batches/create", methods=["POST"])
def create_batch():
    name = (request.form.get("name") or request.form.get("batch_name") or "").strip()
    if not name:
        # If user submits blank, just go back without crashing
        return redirect(url_for("batches"))

    with connect() as conn:
        cur = conn.execute("INSERT INTO batches (name) VALUES (?)", (name,))
        conn.commit()
        batch_id = cur.lastrowid

    # optional: create spiders 1..N if form includes count
    count_raw = request.form.get("count") or request.form.get("spider_count") or ""
    try:
        count = int(count_raw) if str(count_raw).strip() else 0
    except:
        count = 0

    if count > 0:
        with connect() as conn:
            for n in range(1, count + 1):
                conn.execute(
                    "INSERT OR IGNORE INTO spiders (batch_id, number) VALUES (?, ?)",
                    (batch_id, n)
                )
            conn.commit()

    session["last_batch"] = batch_id
    return redirect(url_for("batch_view", batch_id=batch_id))


@app.route("/batch/<int:batch_id>")
def batch_view(batch_id: int):
    today = date.today().isoformat()
    session["last_batch"] = batch_id

    with connect() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        spiders = conn.execute(
            "SELECT * FROM spiders WHERE batch_id=? ORDER BY number ASC",
            (batch_id,)
        ).fetchall()

        logs = conn.execute("""
            SELECT spider_id, fed, ate, watered, molting, molts_count, notes
            FROM spiderlog
            WHERE day=? AND spider_id IN (SELECT id FROM spiders WHERE batch_id=?)
        """, (today, batch_id)).fetchall()

    log_map = {r["spider_id"]: r for r in logs}
    return render_template("batch_view.html", batch=batch, spiders=spiders, day=today, log_map=log_map)


@app.route("/spiderlog/<int:spider_id>/<day>", methods=["POST"])
def save_spiderlog(spider_id: int, day: str):
    fed = request.form.get("fed", "no")
    ate = request.form.get("ate", "no")
    watered = request.form.get("watered", "no")
    molting = request.form.get("molting", "no")
    molts_count = request.form.get("molts_count", "0")
    notes = request.form.get("notes", "")

    try:
        molts_count = int(molts_count)
    except:
        molts_count = 0

    with connect() as conn:
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
        """, (spider_id, day, fed, ate, watered, molting, molts_count, notes))
        conn.commit()

    back = request.form.get("back", "")
    if back == "calendar":
        return redirect(url_for("calendar", year=int(day[:4]), month=int(day[5:7])))
    return redirect(url_for("batch_view", batch_id=session.get("last_batch", 1)))


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
            spider_ids = tuple([s["id"] for s in spiders])
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
    app.run(host="0.0.0.0", port=5000, debug=True)