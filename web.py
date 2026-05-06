from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import os
from dotenv import load_dotenv
import matplotlib.pyplot as plt
import io
import base64

from database import (
    init_db,
    admin_lister_duel_users, admin_get_full_duel_user, admin_update_duel_profil,
    admin_supprimer_sabre_collection,
    db_get_tous_sabres, db_get_sabre, db_create_sabre, db_update_sabre, db_delete_sabre,
    ajouter_sabre as db_ajouter_sabre_collection,
    get_combat_xp_progress, get_xp_pour_prochain_niveau,
)
from duel_sabres import RARETES

# S'assurer que la DB et le seed sont prêts
init_db()

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
    fig, ax = plt.subplots(figsize=(10, 6), facecolor='#FFFFFF')
    ax.barh(usernames, xp_values, color='#FBE863', edgecolor='#2F2521', linewidth=1.2)
    ax.set_xlabel('XP', color='#2F2521')
    ax.set_title('Top 10 des utilisateurs', color='#2F2521', fontsize=14, fontweight='bold')
    ax.tick_params(colors='#2F2521')
    for spine in ax.spines.values():
        spine.set_color('#2F2521')
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

@app.route("/user/<user_id>")
def user_profile(user_id):
    if not session.get("logged_in"):
        return redirect("/")
    return render_template("user.html")

@app.route("/reactions")
def reactions_panel():
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    users = conn.execute("SELECT DISTINCT user_id, username FROM users ORDER BY username").fetchall()
    reactions_rows = conn.execute("SELECT user_id, emoji FROM reactions").fetchall()
    reactions = {str(row[0]): row[1] for row in reactions_rows}
    conn.close()
    return render_template("reactions.html", users=users, reactions=reactions)

@app.route("/duels")
def duels_page():
    if not session.get("logged_in"):
        return redirect("/")
    return render_template("duels.html", raretes=RARETES)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ===== API DUELS — UTILISATEURS =====
@app.route("/api/duels/users")
def api_duels_users():
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    q = request.args.get("q", "").strip().lower()
    rows = admin_lister_duel_users()
    if q:
        rows = [r for r in rows if q in (r.get("username") or "").lower() or q in str(r.get("user_id"))]
    return jsonify({"users": rows})

@app.route("/api/duels/user/<user_id>")
def api_duels_user(user_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    data = admin_get_full_duel_user(user_id)
    if not data:
        return jsonify({"error": "Profil duel introuvable"}), 404
    # Calculer la progression de combat XP
    profil = data["profil"]
    total_xp = profil.get("combat_xp", 0) or 0
    level, xp_in_level, xp_needed = get_combat_xp_progress(total_xp)
    profil["combat_xp_in_level"] = xp_in_level
    profil["combat_xp_needed"]   = xp_needed
    # Ratio
    v = profil.get("victoires", 0) or 0
    d = profil.get("defaites", 0) or 0
    profil["ratio"] = round(v / d, 2) if d > 0 else (float(v) if v else 0.0)
    return jsonify(data)

@app.route("/api/duels/user/<user_id>/update", methods=["POST"])
def api_duels_user_update(user_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    data = request.json or {}
    ok = admin_update_duel_profil(user_id, data)
    if not ok:
        return jsonify({"error": "Aucune mise à jour"}), 400
    return jsonify({"success": True})

@app.route("/api/duels/user/<user_id>/sabres/add", methods=["POST"])
def api_duels_user_sabre_add(user_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    data = request.json or {}
    sabre_id = data.get("sabre_id")
    if not sabre_id or not db_get_sabre(sabre_id):
        return jsonify({"error": "Sabre inconnu"}), 400
    db_ajouter_sabre_collection(user_id, sabre_id)
    return jsonify({"success": True})

@app.route("/api/duels/user/<user_id>/sabres/remove", methods=["POST"])
def api_duels_user_sabre_remove(user_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    data = request.json or {}
    sabre_id = data.get("sabre_id")
    if not sabre_id:
        return jsonify({"error": "sabre_id requis"}), 400
    admin_supprimer_sabre_collection(user_id, sabre_id)
    return jsonify({"success": True})


# ===== API SABRES (shop) =====
@app.route("/api/sabres")
def api_sabres_list():
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    sabres = db_get_tous_sabres()
    return jsonify({"sabres": list(sabres.values()), "raretes": RARETES})

@app.route("/api/sabres/create", methods=["POST"])
def api_sabres_create():
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    data = request.json or {}
    if not data.get("id") or not data.get("nom") or not data.get("rarete"):
        return jsonify({"error": "id, nom, rarete requis"}), 400
    if data["rarete"] not in RARETES:
        return jsonify({"error": "rareté invalide"}), 400
    ok = db_create_sabre(data)
    if not ok:
        return jsonify({"error": "Un sabre avec cet ID existe déjà"}), 400
    return jsonify({"success": True})

@app.route("/api/sabres/<sabre_id>/update", methods=["POST"])
def api_sabres_update(sabre_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    data = request.json or {}
    if "rarete" in data and data["rarete"] not in RARETES:
        return jsonify({"error": "rareté invalide"}), 400
    ok = db_update_sabre(sabre_id, data)
    if not ok:
        return jsonify({"error": "Aucune mise à jour"}), 400
    return jsonify({"success": True})

@app.route("/api/sabres/<sabre_id>/delete", methods=["POST"])
def api_sabres_delete(sabre_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    if sabre_id == "bleu":
        return jsonify({"error": "Le sabre 'bleu' ne peut pas être supprimé (sabre par défaut)"}), 400
    ok = db_delete_sabre(sabre_id)
    if not ok:
        return jsonify({"error": "Sabre introuvable"}), 404
    return jsonify({"success": True})

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
    return jsonify({"users": [dict(u) for u in results]})

@app.route("/api/user/<user_id>")
def api_user(user_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Non autorisé"}), 401
    db = get_db()
    user = db.execute("SELECT user_id, username, level, xp FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    db.close()
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    return jsonify({"user": dict(user)})

@app.route("/api/reactions/add", methods=["POST"])
def add_reaction():
    if not session.get("logged_in"):
        return jsonify({"error": "Non authentifié"}), 401
    data = request.json
    user_id = str(data.get("user_id"))
    emoji = data.get("emoji")
    print(f"[DEBUG] Ajout réaction: user_id={user_id}, emoji={emoji}")
    if not user_id or not emoji:
        return jsonify({"error": "user_id et emoji requis"}), 400
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO reactions (user_id, emoji) VALUES (?, ?)", (user_id, emoji))
    conn.commit()
    # Vérification immédiate
    row = conn.execute("SELECT * FROM reactions WHERE user_id = ?", (user_id,)).fetchone()
    print(f"[DEBUG] Dans DB après insert: {dict(row) if row else 'RIEN'}")
    conn.close()
    return jsonify({"success": True})

@app.route("/api/reactions/remove", methods=["POST"])
def remove_reaction():
    if not session.get("logged_in"):
        return jsonify({"error": "Non authentifié"}), 401
    data = request.json
    user_id = str(data.get("user_id"))
    print(f"[DEBUG] Suppression réaction: user_id={user_id}")
    if not user_id:
        return jsonify({"error": "user_id requis"}), 400
    conn = get_db()
    conn.execute("DELETE FROM reactions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)