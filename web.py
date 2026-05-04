from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import os
from dotenv import load_dotenv
import matplotlib.pyplot as plt
import io
import base64

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
PASSWORD = os.getenv("WEB_PASSWORD")

def get_db():
    conn = sqlite3.connect("bot_database.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_global_stats():
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) as count FROM users").fetchone()["count"]
    total_xp = db.execute("SELECT SUM(xp) as sum FROM users").fetchone()["sum"] or 0
    avg_level = db.execute("SELECT AVG(level) as avg FROM users").fetchone()["avg"] or 0
    top_user = db.execute("SELECT username, xp, level FROM users ORDER BY xp DESC LIMIT 1").fetchone()
    db.close()
    
    return {
        "total_users": total_users,
        "total_xp": total_xp,
        "avg_level": round(avg_level, 2),
        "top_user": dict(top_user) if top_user else None
    }

def generate_chart():
    db = get_db()
    users = db.execute("SELECT username, xp FROM users ORDER BY xp DESC LIMIT 10").fetchall()
    db.close()
    
    if not users:
        return None
    
    usernames = [user["username"] for user in users]
    xp_values = [user["xp"] for user in users]
    
    plt.figure(figsize=(10, 6))
    plt.barh(usernames, xp_values, color='#7289DA')
    plt.xlabel('XP')
    plt.title('Top 10 des utilisateurs')
    plt.tight_layout()
    
    img = io.BytesIO()
    plt.savefig(img, format='png')
    img.seek(0)
    plt.close()
    
    return base64.b64encode(img.getvalue()).decode()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            return redirect("/dashboard")
        return render_template("login.html", error="Mot de passe incorrect")
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect("/")
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY xp DESC").fetchall()
    db.close()
    
    stats = get_global_stats()
    chart = generate_chart()
    
    return render_template("dashboard.html", users=users, stats=stats, chart=chart)

@app.route("/search")
def search_page():
    if not session.get("logged_in"):
        return redirect("/")
    return render_template("search.html")

@app.route("/user/<int:user_id>")
def user_profile(user_id):
    if not session.get("logged_in"):
        return redirect("/")
    return render_template("user.html")

@app.route('/reactions')
def reactions():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT DISTINCT user_id, username FROM users ORDER BY username")
    users = cursor.fetchall()
    
    cursor.execute("SELECT user_id, emoji FROM reactions")
    reactions = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    
    return render_template('reactions.html', users=users, reactions=reactions)

# API Routes
@app.route("/api/search")
def api_search():
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify({"users": []})
    
    db = get_db()
    results = db.execute(
        "SELECT user_id, username, level, xp FROM users WHERE username LIKE ? ORDER BY xp DESC",
        (f"%{query}%",)
    ).fetchall()
    db.close()
    
    return jsonify({
        "users": [dict(u) for u in results]
    })

@app.route("/api/user/<int:user_id>")
def api_user(user_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    
    db = get_db()
    user = db.execute("SELECT user_id, username, level, xp FROM users WHERE user_id = ?", (user_id,)).fetchone()
    db.close()
    
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    
    return jsonify({
        "user": dict(user)
    })

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

@app.route('/reactions', methods=['GET', 'POST'])
def reactions_panel():
    if 'logged_in' not in session:
        return redirect('/login')
    
    # Récupère tous les utilisateurs de la base de données
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_id, username FROM users ORDER BY username")
    users = cursor.fetchall()
    
    # Récupère toutes les réactions actuelles
    cursor.execute("SELECT user_id, emoji FROM reactions")
    reactions = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    
    return render_template('reactions.html', users=users, reactions=reactions)

@app.route('/api/reactions/add', methods=['POST'])
def add_reaction():
    if 'logged_in' not in session:
        return {'error': 'Non authentifié'}, 401
    
    data = request.json
    user_id = data.get('user_id')
    emoji = data.get('emoji')
    
    if not user_id or not emoji:
        return {'error': 'user_id et emoji requis'}, 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO reactions (user_id, emoji) VALUES (?, ?)",
                   (user_id, emoji))
    conn.commit()
    conn.close()
    
    return {'success': True, 'message': f'Réaction ajoutée pour {user_id}'}

@app.route('/api/reactions/remove', methods=['POST'])
def remove_reaction():
    if 'logged_in' not in session:
        return {'error': 'Non authentifié'}, 401
    
    data = request.json
    user_id = data.get('user_id')
    
    if not user_id:
        return {'error': 'user_id requis'}, 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reactions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    return {'success': True, 'message': f'Réaction supprimée pour {user_id}'}