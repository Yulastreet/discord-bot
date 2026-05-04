from flask import Flask, render_template, request, redirect, session
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
PASSWORD = os.getenv("WEB_PASSWORD")

def get_db():
    conn = sqlite3.connect("bot_database.db")  # ← change ici
    conn.row_factory = sqlite3.Row
    return conn

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["password"] == PASSWORD:
            session["logged_in"] = True
            return redirect("/dashboard")
        return render_template("login.html", error="Mot de passe incorrect")
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect("/")
    db = get_db()
    users = db.execute("SELECT user_id, username, xp, level FROM users ORDER BY xp DESC").fetchall()
    return render_template("dashboard.html", users=users)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
