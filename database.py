import sqlite3

def get_db():
    conn = sqlite3.connect("bot_database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # Table users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        level INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0
    )''')

    # Table réactions
    c.execute('''CREATE TABLE IF NOT EXISTS reactions (
        user_id INTEGER PRIMARY KEY,
        emoji TEXT
    )''')

    # Table welcome
    c.execute('''CREATE TABLE IF NOT EXISTS welcome (
        guild_id TEXT PRIMARY KEY,
        channel_id INTEGER
    )''')

    # Table profil duel
    c.execute('''CREATE TABLE IF NOT EXISTS duel_profil (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        level INTEGER DEFAULT 1,
        tookcoins INTEGER DEFAULT 0,
        victoires INTEGER DEFAULT 0,
        defaites INTEGER DEFAULT 0,
        sabre_equipe TEXT DEFAULT 'bleu',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Table collection de sabres
    c.execute('''CREATE TABLE IF NOT EXISTS duel_collection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        sabre_id TEXT,
        obtenu_le TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, sabre_id),
        FOREIGN KEY(user_id) REFERENCES duel_profil(user_id)
    )''')

    # Table historique des duels
    c.execute('''CREATE TABLE IF NOT EXISTS duel_historique (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id_1 TEXT,
        user_id_2 TEXT,
        gagnant_id TEXT,
        tookcoins_gagnant INTEGER,
        tookcoins_perdant INTEGER,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id_1) REFERENCES duel_profil(user_id),
        FOREIGN KEY(user_id_2) REFERENCES duel_profil(user_id),
        FOREIGN KEY(gagnant_id) REFERENCES duel_profil(user_id)
    )''')

    # Migration : nouvelles colonnes système de combat
    nouvelles_colonnes = [
        ("combat_xp",      "INTEGER DEFAULT 0"),
        ("combat_level",   "INTEGER DEFAULT 1"),
        ("stat_points",    "INTEGER DEFAULT 0"),
        ("stat_force",     "INTEGER DEFAULT 0"),
        ("stat_agilite",   "INTEGER DEFAULT 0"),
        ("stat_defense",   "INTEGER DEFAULT 0"),
        ("stat_endurance", "INTEGER DEFAULT 0"),
        ("stat_chance",    "INTEGER DEFAULT 0"),
    ]
    for col, definition in nouvelles_colonnes:
        try:
            c.execute(f"ALTER TABLE duel_profil ADD COLUMN {col} {definition}")
        except Exception:
            pass  # Colonne déjà existante

    conn.commit()
    conn.close()
    print("✅ Base de données initialisée !")


# ===== XP MESSAGES =====
def get_level(xp):
    return int(xp ** 0.2)

def get_xp(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT xp FROM users WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    conn.close()
    return row["xp"] if row else 0

def set_xp(user_id, xp, username=None):
    conn = get_db()
    c = conn.cursor()
    level = get_level(xp)
    if username:
        c.execute("INSERT OR REPLACE INTO users (user_id, username, xp, level) VALUES (?, ?, ?, ?)",
                  (str(user_id), username, xp, level))
    else:
        c.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, str(user_id)))
    conn.commit()
    conn.close()

def get_leaderboard():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, xp FROM users ORDER BY xp DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    return [(row["user_id"], row["xp"]) for row in rows]


# ===== REACTIONS =====
def get_all_reactions():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, emoji FROM reactions")
    rows = c.fetchall()
    conn.close()
    return {int(row["user_id"]): row["emoji"] for row in rows}

def set_reaction(user_id, emoji):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO reactions (user_id, emoji) VALUES (?, ?)", (user_id, emoji))
    conn.commit()
    conn.close()

def remove_reaction(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM reactions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ===== WELCOME =====
def get_welcome(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT channel_id FROM welcome WHERE guild_id = ?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    return row["channel_id"] if row else None

def set_welcome(guild_id, channel_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO welcome (guild_id, channel_id) VALUES (?, ?)", (str(guild_id), channel_id))
    conn.commit()
    conn.close()


# ===== DUEL - PROFIL =====
def get_duel_profil(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM duel_profil WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def creer_duel_profil(user_id, username):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR IGNORE INTO duel_profil
                 (user_id, username, level, tookcoins, victoires, defaites, sabre_equipe,
                  combat_xp, combat_level, stat_points,
                  stat_force, stat_agilite, stat_defense, stat_endurance, stat_chance)
                 VALUES (?, ?, 1, 0, 0, 0, 'bleu', 0, 1, 0, 0, 0, 0, 0, 0)""",
              (str(user_id), username))
    conn.commit()
    conn.close()

def ajouter_tookcoins(user_id, montant):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE duel_profil SET tookcoins = tookcoins + ? WHERE user_id = ?", (montant, str(user_id)))
    conn.commit()
    conn.close()

def ajouter_victoire(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE duel_profil SET victoires = victoires + 1 WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()

def ajouter_defaite(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE duel_profil SET defaites = defaites + 1 WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()

def changer_sabre_equipe(user_id, sabre_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE duel_profil SET sabre_equipe = ? WHERE user_id = ?", (sabre_id, str(user_id)))
    conn.commit()
    conn.close()


# ===== DUEL - COLLECTION =====
def ajouter_sabre(user_id, sabre_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO duel_collection (user_id, sabre_id) VALUES (?, ?)", (str(user_id), sabre_id))
    conn.commit()
    conn.close()

def get_collection_sabres(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT sabre_id FROM duel_collection WHERE user_id = ? ORDER BY obtenu_le ASC", (str(user_id),))
    rows = c.fetchall()
    conn.close()
    return [row["sabre_id"] for row in rows]

def possede_sabre(user_id, sabre_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM duel_collection WHERE user_id = ? AND sabre_id = ?", (str(user_id), sabre_id))
    exists = c.fetchone() is not None
    conn.close()
    return exists


# ===== DUEL - HISTORIQUE =====
def sauvegarder_duel(user_id_1, user_id_2, gagnant_id, tookcoins_gagnant, tookcoins_perdant):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO duel_historique
                 (user_id_1, user_id_2, gagnant_id, tookcoins_gagnant, tookcoins_perdant)
                 VALUES (?, ?, ?, ?, ?)""",
              (str(user_id_1), str(user_id_2), str(gagnant_id), tookcoins_gagnant, tookcoins_perdant))
    conn.commit()
    conn.close()

def get_historique(user_id, limit=10):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT * FROM duel_historique
                 WHERE user_id_1 = ? OR user_id_2 = ?
                 ORDER BY date DESC LIMIT ?""",
              (str(user_id), str(user_id), limit))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ===== DUEL - XP DE COMBAT =====
STAT_COLUMNS = {
    "force":     "stat_force",
    "agilite":   "stat_agilite",
    "defense":   "stat_defense",
    "endurance": "stat_endurance",
    "chance":    "stat_chance",
}

def get_xp_pour_prochain_niveau(level):
    """XP requis pour passer du niveau `level` au suivant."""
    return int(100 * (level ** 1.3))

def get_combat_xp_progress(total_xp):
    """Retourne (level, xp_dans_niveau_actuel, xp_requis_prochain_niveau)."""
    level = 1
    remaining = total_xp
    while True:
        needed = get_xp_pour_prochain_niveau(level)
        if remaining < needed:
            return level, remaining, needed
        remaining -= needed
        level += 1
        if level >= 50:
            return level, 0, 0

def add_combat_xp_db(user_id, montant):
    """Ajoute de l'XP de combat. Retourne (nouveau_niveau, a_monte_de_niveau)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT combat_xp, combat_level, stat_points FROM duel_profil WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    if not row:
        conn.close()
        return 1, False

    old_xp    = row["combat_xp"]    or 0
    old_level = row["combat_level"] or 1
    new_xp    = old_xp + montant
    new_level, _, _ = get_combat_xp_progress(new_xp)
    leveled_up     = new_level > old_level
    levels_gained  = max(0, new_level - old_level)
    new_stat_pts   = (row["stat_points"] or 0) + levels_gained

    c.execute("""UPDATE duel_profil
                 SET combat_xp = ?, combat_level = ?, stat_points = ?
                 WHERE user_id = ?""",
              (new_xp, new_level, new_stat_pts, str(user_id)))
    conn.commit()
    conn.close()
    return new_level, leveled_up

def attribuer_stat_db(user_id, stat):
    """Attribue 1 point à une stat. Retourne True si succès."""
    if stat not in STAT_COLUMNS:
        return False
    col = STAT_COLUMNS[stat]
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT stat_points FROM duel_profil WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    if not row or (row["stat_points"] or 0) <= 0:
        conn.close()
        return False
    c.execute(
        f"UPDATE duel_profil SET stat_points = stat_points - 1, {col} = {col} + 1 WHERE user_id = ?",
        (str(user_id),)
    )
    conn.commit()
    conn.close()
    return True


if __name__ == "__main__":
    init_db()
