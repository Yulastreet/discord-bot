import sqlite3
from datetime import datetime

def get_db():
    conn = sqlite3.connect('bot_data.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Table logs des commandes
    c.execute('''CREATE TABLE IF NOT EXISTS command_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        username TEXT,
        command TEXT,
        args TEXT,
        guild_id TEXT,
        channel_id TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Table modération
    c.execute('''CREATE TABLE IF NOT EXISTS mod_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        moderator_id TEXT,
        moderator_name TEXT,
        target_id TEXT,
        target_name TEXT,
        reason TEXT,
        guild_id TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()

def log_command(user_id, username, command, args, guild_id, channel_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO command_logs 
        (user_id, username, command, args, guild_id, channel_id)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (str(user_id), username, command, args, str(guild_id), str(channel_id)))
    conn.commit()
    conn.close()

def log_mod_action(action, moderator_id, moderator_name, target_id, target_name, reason, guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO mod_logs 
        (action, moderator_id, moderator_name, target_id, target_name, reason, guild_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (action, str(moderator_id), moderator_name, str(target_id), target_name, reason, str(guild_id)))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Base de données initialisée !")