import os
import sqlite3
from datetime import datetime, date
import calendar as pycal

from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

DB = "jumper.db"


# ---------------- DB ---------------- #

def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS batches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS spiders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER,
            number INTEGER,
            fed INTEGER DEFAULT 0,
            ate TEXT DEFAULT 'unknown',
            booty INTEGER DEFAULT 3,
            molting INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS daylog(
            day TEXT PRIMARY KEY,
            watered INTEGER DEFAULT 0,
            sprays INTEGER DEFAULT 0,
            feeder TEXT,
            note TEXT
        )""")

        conn.commit()


init_db()


# ---------------- AUTH ---------------- #

@app.before_request
def require_login():
    # allow static + login without auth
    if request.path.startswith("/static"):
        return
    if request.path in ["/login"]:
        return
    if not session.get("logged_in"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password", "") == os.environ.get("APP_PASSWORD", "spiders"):
            session["logged_in"] = True
            return redirect(url_for("today"))
        flash("Wrong password.")
    return render_template("login.html")


# ---------------- BATCHES ---------------- #

@app.route("/batches")
def batches():
    with connect() as conn:
        batches_rows = conn.execute("SELECT * FROM batches ORDER BY created_at DESC").fetchall()
    return render_template("batches.html", batches=batches_rows)


@app.route("/create_batch", methods=["POST"])
def create_batch():
    name = request.form.get("name", "").strip()
    count_str = request.form.get("count", "0").strip()

    if not name:
        flash("Batch name required.")
        return redirect(url_for("batches"))

    try:
        count = int(count_str)
    except ValueError:
        count = 0

    if count <= 0:
        flash("Spider count must be a number > 0.")
        return redirect(url_for("batches"))

    now = datetime.now().isoformat()

    with connect() as conn:
        conn.execute("INSERT INTO batches(name,created_at) VALUES (?,?)", (name, now))
        batch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for i in range(1, count + 1):
            conn.execute(
                "INSERT INTO spiders(batch_id,number,created_at) VALUES (?,?,?)",
                (batch_id, i, now)
            )

        conn.commit()

    session["last_batch"] = batch_id
    return redirect(url_for("batch_view", batch_id=batch_id))


@app.route("/batch/<int:batch_id>")
def batch_view(batch_id):
    session["last_batch"] = batch_id
    with connect() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        spiders = conn.execute(
            "SELECT * FROM spiders WHERE batch_id=? ORDER BY number",
            (batch_id,)
        ).fetchall()

    if not batch:
        flash("Batch not found.")
        return redirect(url_for("batches"))

    return render_template("batch_view.html", batch=batch, spiders=spiders)


@app.route("/delete_batch/<int:batch_id>")
def delete_batch(batch_id):
    with connect() as conn:
        conn.execute("DELETE FROM spiders WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
        conn.commit()
    return redirect(url_for("batches"))


# ---------------- SPIDER UPDATE ---------------- #

@app.route("/update_spider/<int:spider_id>", methods=["POST"])
def update_spider(spider_id):
    fed = 1 if request.form.get("fed") else 0
    molting = 1 if request.form.get("molting") else 0

    ate = request.form.get("ate", "unknown")
    if ate not in ("yes", "no", "unknown"):
        ate = "unknown"

    try:
        booty = int(request.form.get("booty", 3))
    except ValueError:
        booty = 3
    booty = max(1, min(5, booty))

    notes = request.form.get("notes", "")

    with connect() as conn:
        conn.execute("""
            UPDATE spiders
            SET fed=?, ate=?, booty=?, molting=?, notes=?
            WHERE id=?
        """, (fed, ate, booty, molting, notes, spider_id))
        conn.commit()

    return redirect(request.referrer or url_for("today"))


# ---------------- TODAY ---------------- #

@app.route("/")
@app.route("/today")
def today():
    batch_id = session.get("last_batch")

    if not batch_id:
        with connect() as conn:
            row = conn.execute("SELECT id FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            batch_id = row["id"]
            session["last_batch"] = batch_id
        else:
            return redirect(url_for("batches"))

    return redirect(url_for("batch_view", batch_id=batch_id))


# ---------------- CALENDAR ---------------- #
# Calendar month grid + popup API

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

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        weeks=weeks,
        today=today_dt
    )


@app.route("/api/day/<day>")
def api_day(day):
    # returns JSON for the calendar popup
    with connect() as conn:
        row = conn.execute("SELECT * FROM daylog WHERE day=?", (day,)).fetchone()

    if not row:
        return {"day": day, "has_data": False}

    return {
        "day": row["day"],
        "has_data": True,
        "watered": bool(row["watered"]),
        "sprays": row["sprays"],
        "feeder": row["feeder"] or "",
        "note": row["note"] or ""
    }


@app.route("/day/<day>")
def day(day):
    with connect() as conn:
        row = conn.execute("SELECT * FROM daylog WHERE day=?", (day,)).fetchone()
    return render_template("day.html", day=row or {"day": day})


@app.route("/save_day/<day>", methods=["POST"])
def save_day(day):
    watered = 1 if request.form.get("watered") else 0
    sprays = request.form.get("sprays", 0)
    feeder = request.form.get("feeder", "")
    note = request.form.get("note", "")

    with connect() as conn:
        conn.execute("""
            INSERT INTO daylog(day,watered,sprays,feeder,note)
            VALUES (?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET
                watered=?,
                sprays=?,
                feeder=?,
                note=?
        """, (day, watered, sprays, feeder, note,
              watered, sprays, feeder, note))
        conn.commit()

    return redirect(url_for("day", day=day))


# ---------------- RUN ---------------- #

if __name__ == "__main__":
    # For phone testing on same Wi-Fi:
    # http://<your-ip>:5000
    app.run(host="0.0.0.0", port=5000, debug=True)