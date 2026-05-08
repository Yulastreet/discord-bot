import sqlite3

def get_db():
    conn = sqlite3.connect("bot_database.db")
    conn.row_factory = sqlite3.Row
    return conn

def _table_columns(c, table):
    """Return list of column names for given table (empty if missing)."""
    try:
        return [r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []

def init_db():
    conn = get_db()
    c = conn.cursor()

    # ===== MIGRATION users : passage au PK composite (guild_id, user_id) =====
    # Détection : si la table existe sans colonne guild_id, drop+recreate.
    users_cols = _table_columns(c, "users")
    if users_cols and "guild_id" not in users_cols:
        print("[MIGRATION] users: schema v1 detecte, wipe + reschema (guild_id, user_id).")
        c.execute("DROP TABLE users")
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        guild_id TEXT NOT NULL,
        user_id  TEXT NOT NULL,
        username TEXT,
        level    INTEGER DEFAULT 0,
        xp       INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )''')

    # ===== MIGRATION reactions : idem =====
    reactions_cols = _table_columns(c, "reactions")
    if reactions_cols and "guild_id" not in reactions_cols:
        print("[MIGRATION] reactions: schema v1 detecte, wipe + reschema (guild_id, user_id).")
        c.execute("DROP TABLE reactions")
    c.execute('''CREATE TABLE IF NOT EXISTS reactions (
        guild_id TEXT NOT NULL,
        user_id  TEXT NOT NULL,
        emoji    TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    )''')

    # ===== Table guilds (registre des serveurs Discord vu par le bot) =====
    c.execute('''CREATE TABLE IF NOT EXISTS guilds (
        guild_id     TEXT PRIMARY KEY,
        name         TEXT,
        icon_url     TEXT,
        member_count INTEGER DEFAULT 0,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        active       INTEGER DEFAULT 1
    )''')

    # ===== Tables musique =====
    c.execute('''CREATE TABLE IF NOT EXISTS music_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id     TEXT NOT NULL,
        position     INTEGER NOT NULL,
        title        TEXT NOT NULL,
        url          TEXT NOT NULL,
        source_url   TEXT,
        duration     INTEGER,
        thumbnail    TEXT,
        requested_by TEXT,
        added_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS music_state (
        guild_id            TEXT PRIMARY KEY,
        voice_channel_id    TEXT,
        voice_channel_name  TEXT,
        current_title       TEXT,
        current_url         TEXT,
        current_thumbnail   TEXT,
        current_duration    INTEGER,
        is_playing          INTEGER DEFAULT 0,
        is_paused           INTEGER DEFAULT 0,
        started_at          TIMESTAMP,
        updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Table bot_commands (queue web -> bot, polling 1.5s) =====
    c.execute('''CREATE TABLE IF NOT EXISTS bot_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id     TEXT NOT NULL,
        cmd          TEXT NOT NULL,
        payload      TEXT,
        status       TEXT DEFAULT 'pending',
        result       TEXT,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP
    )''')

    # ===== Table logs (commandes + actions par serveur) =====
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id     TEXT NOT NULL,
        type         TEXT NOT NULL,
        ts           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_id      TEXT,
        username     TEXT,
        channel_id   TEXT,
        channel_name TEXT,
        content      TEXT,
        meta         TEXT
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_logs_guild_ts   ON logs(guild_id, ts DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_logs_guild_type ON logs(guild_id, type, ts DESC)")

    # ===== Table guild_channels (cache des salons par serveur) =====
    c.execute('''CREATE TABLE IF NOT EXISTS guild_channels (
        guild_id   TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        name       TEXT,
        type       TEXT,
        position   INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, channel_id)
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

    # Table sabres (modifiable via dashboard web)
    c.execute('''CREATE TABLE IF NOT EXISTS sabres (
        id TEXT PRIMARY KEY,
        nom TEXT NOT NULL,
        emoji TEXT,
        rarete TEXT NOT NULL,
        prix INTEGER DEFAULT 0,
        description TEXT,
        speciale_nom TEXT,
        speciale_description TEXT,
        speciale_emoji TEXT,
        speciale_effet TEXT
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

    # Seed de la table sabres si vide (depuis duel_sabres.SABRES_DEFAULT)
    seed_sabres_si_vide()
    print("[OK] Base de donnees initialisee !")


# ===== DUEL - SABRES (DB) =====
def _row_to_sabre(row):
    if not row:
        return None
    d = dict(row)
    return {
        "id":          d["id"],
        "nom":         d["nom"],
        "emoji":       d["emoji"],
        "rarete":      d["rarete"],
        "prix":        d["prix"],
        "description": d["description"],
        "speciale": {
            "nom":         d["speciale_nom"],
            "description": d["speciale_description"],
            "emoji":       d["speciale_emoji"],
            "effet":       d["speciale_effet"],
        }
    }

def db_get_sabre(sabre_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM sabres WHERE id = ?", (sabre_id,))
    row = c.fetchone()
    conn.close()
    return _row_to_sabre(row)

def db_get_tous_sabres():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM sabres")
    rows = c.fetchall()
    conn.close()
    return {row["id"]: _row_to_sabre(row) for row in rows}

def db_get_sabres_par_rarete(rarete):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM sabres WHERE rarete = ?", (rarete,))
    rows = c.fetchall()
    conn.close()
    return {row["id"]: _row_to_sabre(row) for row in rows}

def db_create_sabre(data):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""INSERT INTO sabres
            (id, nom, emoji, rarete, prix, description,
             speciale_nom, speciale_description, speciale_emoji, speciale_effet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["id"], data["nom"], data.get("emoji", ""), data["rarete"],
             int(data.get("prix", 0)), data.get("description", ""),
             data.get("speciale_nom", ""), data.get("speciale_description", ""),
             data.get("speciale_emoji", ""), data.get("speciale_effet", "")))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def db_update_sabre(sabre_id, data):
    champs = ["nom", "emoji", "rarete", "prix", "description",
              "speciale_nom", "speciale_description", "speciale_emoji", "speciale_effet"]
    set_clauses = []
    valeurs    = []
    for f in champs:
        if f in data:
            set_clauses.append(f"{f} = ?")
            valeurs.append(int(data[f]) if f == "prix" else data[f])
    if not set_clauses:
        return False
    valeurs.append(sabre_id)
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE sabres SET {', '.join(set_clauses)} WHERE id = ?", valeurs)
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def db_delete_sabre(sabre_id):
    """Supprime un sabre. Nettoie aussi les collections et reset les sabres équipés."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM duel_collection WHERE sabre_id = ?", (sabre_id,))
    c.execute("UPDATE duel_profil SET sabre_equipe = 'bleu' WHERE sabre_equipe = ?", (sabre_id,))
    c.execute("DELETE FROM sabres WHERE id = ?", (sabre_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def seed_sabres_si_vide():
    """Importe les sabres par défaut depuis duel_sabres.SABRES_DEFAULT si la table est vide."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM sabres")
    if c.fetchone()["n"] > 0:
        conn.close()
        return
    try:
        from duel_sabres import SABRES_DEFAULT
    except ImportError:
        conn.close()
        return
    for sid, s in SABRES_DEFAULT.items():
        sp = s.get("speciale", {})
        c.execute("""INSERT INTO sabres
            (id, nom, emoji, rarete, prix, description,
             speciale_nom, speciale_description, speciale_emoji, speciale_effet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["id"], s["nom"], s.get("emoji", ""), s["rarete"],
             int(s.get("prix", 0)), s.get("description", ""),
             sp.get("nom", ""), sp.get("description", ""),
             sp.get("emoji", ""), sp.get("effet", "")))
    conn.commit()
    conn.close()
    print(f"[OK] Seed sabres : {len(SABRES_DEFAULT)} sabres importes.")


# ===== DUEL - ADMIN (web dashboard) =====
def admin_update_duel_profil(user_id, data):
    """Met à jour les champs autorisés du profil duel via le dashboard."""
    autorises = ["username", "level", "tookcoins", "victoires", "defaites",
                 "sabre_equipe", "combat_xp", "combat_level", "stat_points",
                 "stat_force", "stat_agilite", "stat_defense", "stat_endurance", "stat_chance"]
    set_clauses = []
    valeurs    = []
    for f in autorises:
        if f in data:
            set_clauses.append(f"{f} = ?")
            v = data[f]
            if f not in ("username", "sabre_equipe"):
                v = int(v)
            valeurs.append(v)
    if not set_clauses:
        return False
    valeurs.append(str(user_id))
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE duel_profil SET {', '.join(set_clauses)} WHERE user_id = ?", valeurs)
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def admin_get_full_duel_user(user_id):
    """Retourne profil + collection + historique pour le dashboard."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM duel_profil WHERE user_id = ?", (str(user_id),))
    profil = c.fetchone()
    if not profil:
        conn.close()
        return None
    c.execute("SELECT sabre_id, obtenu_le FROM duel_collection WHERE user_id = ? ORDER BY obtenu_le ASC", (str(user_id),))
    collection = [dict(r) for r in c.fetchall()]
    c.execute("""SELECT * FROM duel_historique
                 WHERE user_id_1 = ? OR user_id_2 = ?
                 ORDER BY date DESC LIMIT 50""", (str(user_id), str(user_id)))
    historique = [dict(r) for r in c.fetchall()]
    conn.close()
    return {
        "profil":     dict(profil),
        "collection": collection,
        "historique": historique,
    }

def admin_supprimer_sabre_collection(user_id, sabre_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM duel_collection WHERE user_id = ? AND sabre_id = ?",
              (str(user_id), sabre_id))
    deleted = c.rowcount > 0
    # Si c'était son sabre équipé, reset à bleu
    c.execute("UPDATE duel_profil SET sabre_equipe = 'bleu' WHERE user_id = ? AND sabre_equipe = ?",
              (str(user_id), sabre_id))
    conn.commit()
    conn.close()
    return deleted

def admin_lister_duel_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT user_id, username, combat_level, tookcoins, victoires, defaites
                 FROM duel_profil ORDER BY combat_level DESC, tookcoins DESC""")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ===== XP MESSAGES (per-guild) =====
def get_level(xp):
    return int(xp ** 0.2)

def get_xp(guild_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT xp FROM users WHERE guild_id = ? AND user_id = ?",
              (str(guild_id), str(user_id)))
    row = c.fetchone()
    conn.close()
    return row["xp"] if row else 0

def set_xp(guild_id, user_id, xp, username=None):
    conn = get_db()
    c = conn.cursor()
    level = get_level(xp)
    if username:
        c.execute("""INSERT INTO users (guild_id, user_id, username, xp, level)
                     VALUES (?, ?, ?, ?, ?)
                     ON CONFLICT(guild_id, user_id) DO UPDATE SET
                       username = excluded.username,
                       xp       = excluded.xp,
                       level    = excluded.level""",
                  (str(guild_id), str(user_id), username, xp, level))
    else:
        c.execute("UPDATE users SET xp = ?, level = ? WHERE guild_id = ? AND user_id = ?",
                  (xp, level, str(guild_id), str(user_id)))
    conn.commit()
    conn.close()

def get_leaderboard(guild_id, limit=10):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT user_id, username, xp, level FROM users
                 WHERE guild_id = ? ORDER BY xp DESC LIMIT ?""",
              (str(guild_id), limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_all_users_for_guild(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE guild_id = ? ORDER BY xp DESC", (str(guild_id),))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

# ===== XP MESSAGES (cross-guild aggregates pour Dashboard general) =====
def get_global_xp_stats():
    """Aggregats cross-guild dedoublonnes par user_id (un meme user dans plusieurs serveurs compte une fois)."""
    conn = get_db()
    c = conn.cursor()
    # Total comptes uniques (un user_id distinct = 1 personne, peu importe combien de serveurs)
    c.execute("SELECT COUNT(DISTINCT user_id) AS n FROM users")
    total_users = c.fetchone()["n"]
    # XP cumule = somme totale (un user actif sur 3 serveurs cumule reellement plus d'XP)
    c.execute("SELECT COALESCE(SUM(xp), 0) AS total_xp FROM users")
    total_xp = c.fetchone()["total_xp"]
    # Niveau moyen calcule sur les XP agreges par user, pas par ligne
    c.execute("""SELECT AVG(lvl_per_user) AS avg_level FROM (
                   SELECT user_id, MAX(level) AS lvl_per_user
                   FROM users GROUP BY user_id
                 )""")
    avg_level = c.fetchone()["avg_level"] or 0
    # Top 10 dedoublonne : on agrege par user_id (somme XP, max niveau, dernier username vu)
    c.execute("""SELECT user_id,
                        MAX(username) AS username,
                        SUM(xp)       AS xp,
                        MAX(level)    AS level
                 FROM users
                 GROUP BY user_id
                 ORDER BY xp DESC LIMIT 10""")
    top10 = [dict(r) for r in c.fetchall()]
    top_user = top10[0] if top10 else None
    conn.close()
    return {
        "total_users": total_users,
        "total_xp":    total_xp or 0,
        "avg_level":   round(avg_level, 2),
        "top_user":    top_user,
        "top10":       top10,
    }


# ===== REACTIONS (per-guild) =====
def get_all_reactions(guild_id):
    """Retourne {user_id: emoji} pour un serveur."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, emoji FROM reactions WHERE guild_id = ?", (str(guild_id),))
    rows = c.fetchall()
    conn.close()
    return {int(r["user_id"]): r["emoji"] for r in rows}

def get_all_reactions_index():
    """Retourne {(guild_id, user_id): emoji} pour le bot (chargement initial)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT guild_id, user_id, emoji FROM reactions")
    rows = c.fetchall()
    conn.close()
    return {(str(r["guild_id"]), int(r["user_id"])): r["emoji"] for r in rows}

def set_reaction(guild_id, user_id, emoji):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO reactions (guild_id, user_id, emoji)
                 VALUES (?, ?, ?)
                 ON CONFLICT(guild_id, user_id) DO UPDATE SET emoji = excluded.emoji""",
              (str(guild_id), str(user_id), emoji))
    conn.commit()
    conn.close()

def remove_reaction(guild_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM reactions WHERE guild_id = ? AND user_id = ?",
              (str(guild_id), str(user_id)))
    conn.commit()
    conn.close()


# ===== GUILDS (registre Discord) =====
def upsert_guild(guild_id, name, icon_url=None, member_count=0):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO guilds (guild_id, name, icon_url, member_count, last_seen_at, active)
                 VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                 ON CONFLICT(guild_id) DO UPDATE SET
                   name = excluded.name,
                   icon_url = excluded.icon_url,
                   member_count = excluded.member_count,
                   last_seen_at = CURRENT_TIMESTAMP,
                   active = 1""",
              (str(guild_id), name, icon_url, int(member_count or 0)))
    conn.commit()
    conn.close()

def mark_guild_left(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE guilds SET active = 0 WHERE guild_id = ?", (str(guild_id),))
    conn.commit()
    conn.close()

def list_guilds(active_only=True):
    conn = get_db()
    c = conn.cursor()
    if active_only:
        c.execute("SELECT * FROM guilds WHERE active = 1 ORDER BY name COLLATE NOCASE")
    else:
        c.execute("SELECT * FROM guilds ORDER BY active DESC, name COLLATE NOCASE")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_guild(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM guilds WHERE guild_id = ?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ===== MUSIQUE — queue & state (DB = source de verite) =====
def music_queue_add(guild_id, title, url, source_url=None, duration=None, thumbnail=None, requested_by=None):
    """Append a track at end of queue. Returns track id."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM music_queue WHERE guild_id = ?", (str(guild_id),))
    pos = c.fetchone()["next_pos"]
    c.execute("""INSERT INTO music_queue
                 (guild_id, position, title, url, source_url, duration, thumbnail, requested_by)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (str(guild_id), pos, title, url, source_url, duration, thumbnail,
               str(requested_by) if requested_by else None))
    track_id = c.lastrowid
    conn.commit()
    conn.close()
    return track_id

def music_queue_pop_next(guild_id):
    """Pop the head of queue (lowest position). Returns track dict or None."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM music_queue WHERE guild_id = ? ORDER BY position ASC LIMIT 1", (str(guild_id),))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    track = dict(row)
    c.execute("DELETE FROM music_queue WHERE id = ?", (track["id"],))
    conn.commit()
    conn.close()
    return track

def music_queue_list(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM music_queue WHERE guild_id = ? ORDER BY position ASC", (str(guild_id),))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def music_queue_clear(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM music_queue WHERE guild_id = ?", (str(guild_id),))
    conn.commit()
    conn.close()

def music_queue_remove(guild_id, track_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM music_queue WHERE guild_id = ? AND id = ?", (str(guild_id), int(track_id)))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def music_state_set(guild_id, **kwargs):
    """Upsert music state. Allowed keys: voice_channel_id, voice_channel_name,
       current_title, current_url, current_thumbnail, current_duration,
       is_playing, is_paused, started_at."""
    allowed = ["voice_channel_id","voice_channel_name","current_title","current_url",
               "current_thumbnail","current_duration","is_playing","is_paused","started_at"]
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    conn = get_db()
    c = conn.cursor()
    # Ensure row exists
    c.execute("INSERT OR IGNORE INTO music_state (guild_id) VALUES (?)", (str(guild_id),))
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys()) + ", updated_at = CURRENT_TIMESTAMP"
    values = list(fields.values()) + [str(guild_id)]
    c.execute(f"UPDATE music_state SET {set_clause} WHERE guild_id = ?", values)
    conn.commit()
    conn.close()

def music_state_get(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM music_state WHERE guild_id = ?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def music_state_clear_current(guild_id):
    music_state_set(guild_id,
                    current_title=None, current_url=None, current_thumbnail=None,
                    current_duration=None, is_playing=0, is_paused=0, started_at=None)

def music_state_disconnect(guild_id):
    music_state_set(guild_id,
                    voice_channel_id=None, voice_channel_name=None,
                    current_title=None, current_url=None, current_thumbnail=None,
                    current_duration=None, is_playing=0, is_paused=0, started_at=None)


# ===== BOT COMMANDS — file d'attente web -> bot =====
def bot_command_enqueue(guild_id, cmd, payload=None):
    import json
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO bot_commands (guild_id, cmd, payload, status)
                 VALUES (?, ?, ?, 'pending')""",
              (str(guild_id), cmd, json.dumps(payload or {})))
    cmd_id = c.lastrowid
    conn.commit()
    conn.close()
    return cmd_id

def bot_command_fetch_pending(limit=10):
    """Atomically fetches pending commands and marks them as processing."""
    import json
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT id, guild_id, cmd, payload FROM bot_commands
                 WHERE status = 'pending' ORDER BY id ASC LIMIT ?""", (limit,))
    rows = c.fetchall()
    if not rows:
        conn.close()
        return []
    ids = [r["id"] for r in rows]
    c.execute(f"UPDATE bot_commands SET status = 'processing' WHERE id IN ({','.join('?'*len(ids))})", ids)
    conn.commit()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        except Exception:
            d["payload"] = {}
        out.append(d)
    return out

def bot_command_finish(cmd_id, status="done", result=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("""UPDATE bot_commands SET status = ?, result = ?, processed_at = CURRENT_TIMESTAMP
                 WHERE id = ?""", (status, result, cmd_id))
    conn.commit()
    conn.close()

def bot_command_get(cmd_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM bot_commands WHERE id = ?", (cmd_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ===== LOGS =====
def add_log(guild_id, type_, user_id=None, username=None,
            channel_id=None, channel_name=None, content=None, meta=None):
    import json
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO logs (guild_id, type, user_id, username, channel_id, channel_name, content, meta)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (str(guild_id), type_,
               str(user_id) if user_id is not None else None,
               username,
               str(channel_id) if channel_id is not None else None,
               channel_name, content,
               json.dumps(meta) if meta else None))
    conn.commit()
    conn.close()

def get_logs(guild_id, type_filter=None, search=None, limit=200):
    """type_filter: 'command' OR 'action_*' OR 'commands' (alias) OR 'actions' (alias) OR None=all."""
    conn = get_db()
    c = conn.cursor()
    where = ["guild_id = ?"]
    args  = [str(guild_id)]
    if type_filter == "commands":
        where.append("type = 'command'")
    elif type_filter == "actions":
        where.append("type LIKE 'action_%'")
    elif type_filter:
        where.append("type = ?")
        args.append(type_filter)
    if search:
        where.append("(LOWER(username) LIKE ? OR LOWER(content) LIKE ? OR LOWER(channel_name) LIKE ?)")
        like = f"%{search.lower()}%"
        args += [like, like, like]
    args.append(int(limit))
    sql = f"SELECT * FROM logs WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?"
    c.execute(sql, args)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def prune_old_logs(guild_id, keep=5000):
    """Limite la table logs a `keep` dernieres entrees par guild (anti-explosion DB)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""DELETE FROM logs WHERE id IN (
                   SELECT id FROM logs WHERE guild_id = ?
                   ORDER BY ts DESC LIMIT -1 OFFSET ?
                 )""", (str(guild_id), keep))
    conn.commit()
    conn.close()


# ===== GUILD CHANNELS (cache pour BotTalk + logs lisibles) =====
def upsert_channel(guild_id, channel_id, name, type_, position=0):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO guild_channels (guild_id, channel_id, name, type, position, updated_at)
                 VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                 ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                   name = excluded.name, type = excluded.type,
                   position = excluded.position, updated_at = CURRENT_TIMESTAMP""",
              (str(guild_id), str(channel_id), name, type_, int(position or 0)))
    conn.commit()
    conn.close()

def remove_channel(guild_id, channel_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM guild_channels WHERE guild_id = ? AND channel_id = ?",
              (str(guild_id), str(channel_id)))
    conn.commit()
    conn.close()

def list_channels(guild_id, type_filter=None):
    conn = get_db()
    c = conn.cursor()
    if type_filter:
        c.execute("""SELECT * FROM guild_channels WHERE guild_id = ? AND type = ?
                     ORDER BY position ASC, name COLLATE NOCASE""",
                  (str(guild_id), type_filter))
    else:
        c.execute("""SELECT * FROM guild_channels WHERE guild_id = ?
                     ORDER BY type, position ASC, name COLLATE NOCASE""",
                  (str(guild_id),))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def replace_guild_channels(guild_id, channels):
    """Remplace en bulk la liste des channels d'un guild. channels = list of dicts."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM guild_channels WHERE guild_id = ?", (str(guild_id),))
    for ch in channels:
        c.execute("""INSERT INTO guild_channels (guild_id, channel_id, name, type, position)
                     VALUES (?, ?, ?, ?, ?)""",
                  (str(guild_id), str(ch["channel_id"]), ch.get("name"),
                   ch.get("type", "text"), int(ch.get("position", 0) or 0)))
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
