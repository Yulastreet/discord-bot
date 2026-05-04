import sqlite3

def get_db():
    conn = sqlite3.connect("bot.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Table XP
    c.execute('''CREATE TABLE IF NOT EXISTS xp (
        user_id TEXT PRIMARY KEY,
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
    
    conn.commit()
    conn.close()
    print("✅ Base de données initialisée !")

# ===== XP =====
def get_xp(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT xp FROM xp WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    conn.close()
    return row["xp"] if row else 0

def set_xp(user_id, xp):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO xp (user_id, xp) VALUES (?, ?)", (str(user_id), xp))
    conn.commit()
    conn.close()

def get_leaderboard():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, xp FROM xp ORDER BY xp DESC LIMIT 10")
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
    return {row["user_id"]: row["emoji"] for row in rows}

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

if __name__ == "__main__":
    init_db()
