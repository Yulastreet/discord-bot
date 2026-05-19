import sqlite3
from typing import Optional

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

    # ===== Table settings (config dynamique) =====
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id   TEXT NOT NULL,
        key        TEXT NOT NULL,
        value      TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, key)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS daily_claims (
        user_id         TEXT PRIMARY KEY,
        last_claim_date TEXT,
        streak          INTEGER DEFAULT 0,
        total_claims    INTEGER DEFAULT 0,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Codes promo (owner cree, users redeem via /redeem CODE)
    # reward_type : 'tookcoins' | 'pass_xp' | 'premium_grant_days'
    # reward_value : int (montant TC, XP, ou jours selon type)
    c.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
        code         TEXT PRIMARY KEY,
        reward_type  TEXT NOT NULL,
        reward_value INTEGER NOT NULL DEFAULT 0,
        max_uses     INTEGER NOT NULL DEFAULT 1,
        used_count   INTEGER NOT NULL DEFAULT 0,
        expires_at   TEXT,
        note         TEXT,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS promo_redemptions (
        code       TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        ts         TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (code, user_id)
    )''')

    # ===== LEAGUE OF LEGENDS =====
    c.execute('''CREATE TABLE IF NOT EXISTS lol_profiles (
        user_id        TEXT PRIMARY KEY,
        puuid          TEXT NOT NULL,
        summoner_id    TEXT,
        game_name      TEXT,
        tag_line       TEXT,
        platform       TEXT DEFAULT 'euw1',
        summoner_level INTEGER,
        last_synced    TEXT,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS lol_rank_config (
        guild_id   TEXT PRIMARY KEY,
        enabled    INTEGER DEFAULT 0,
        role_map   TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Scout sessions : link partageable avec data scout des 5 adversaires
    c.execute('''CREATE TABLE IF NOT EXISTS lol_scout_sessions (
        slug         TEXT PRIMARY KEY,
        owner_id     TEXT NOT NULL,
        platform     TEXT NOT NULL,
        riot_ids     TEXT NOT NULL,
        scout_data   TEXT NOT NULL,
        status       TEXT DEFAULT 'active',
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        ended_at     TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS lol_scout_users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_slug TEXT NOT NULL,
        pseudo       TEXT NOT NULL,
        color        TEXT NOT NULL,
        joined_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        last_seen    TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS lol_scout_chat (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_slug TEXT NOT NULL,
        pseudo       TEXT NOT NULL,
        color        TEXT,
        message      TEXT NOT NULL,
        ts           TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS lol_scout_annotations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_slug TEXT NOT NULL,
        pseudo       TEXT NOT NULL,
        color        TEXT NOT NULL,
        kind         TEXT NOT NULL,
        data         TEXT NOT NULL,
        ts           TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Table guild_members (cache members par serveur) =====
    c.execute('''CREATE TABLE IF NOT EXISTS guild_members (
        guild_id    TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        username    TEXT,
        avatar_url  TEXT,
        is_bot      INTEGER DEFAULT 0,
        joined_at   TIMESTAMP,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id)
    )''')

    # ===== Table dm_messages (DM entre users et le bot, global cross-guild) =====
    c.execute('''CREATE TABLE IF NOT EXISTS dm_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        username    TEXT,
        avatar_url  TEXT,
        direction   TEXT NOT NULL,
        content     TEXT,
        attachments TEXT,
        ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        read_at     TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_dm_user_ts ON dm_messages(user_id, ts DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_dm_unread  ON dm_messages(direction, read_at)")

    # Table welcome
    c.execute('''CREATE TABLE IF NOT EXISTS welcome (
        guild_id TEXT PRIMARY KEY,
        channel_id INTEGER,
        message TEXT
    )''')
    try:
        c.execute("ALTER TABLE welcome ADD COLUMN message TEXT")
    except Exception:
        pass

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

    # ===== Cache des roles par guild (pour pickers dashboard) =====
    c.execute('''CREATE TABLE IF NOT EXISTS guild_roles (
        guild_id    TEXT NOT NULL,
        role_id     TEXT NOT NULL,
        name        TEXT NOT NULL,
        color       INTEGER DEFAULT 0,
        position    INTEGER DEFAULT 0,
        managed     INTEGER DEFAULT 0,
        is_everyone INTEGER DEFAULT 0,
        updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, role_id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_groles_guild ON guild_roles(guild_id)')

    # ===== TICKETS =====
    # Panneau "Ouvrir un ticket" : message avec bouton dans un salon public.
    c.execute('''CREATE TABLE IF NOT EXISTS ticket_panels (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id           TEXT NOT NULL,
        channel_id         TEXT NOT NULL,
        message_id         TEXT,
        panel_title        TEXT,
        panel_description  TEXT,
        button_label       TEXT DEFAULT 'Ouvrir un ticket',
        button_emoji       TEXT DEFAULT '🎫',
        button_style       TEXT DEFAULT 'primary',
        support_role_id    TEXT,
        category_id        TEXT,
        welcome_message    TEXT,
        enabled            INTEGER NOT NULL DEFAULT 1,
        created_by         TEXT,
        created_at         TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tp_guild ON ticket_panels(guild_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tp_msg   ON ticket_panels(message_id)')

    # Tickets ouverts par les membres
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    TEXT NOT NULL,
        panel_id    INTEGER,
        opener_id   TEXT NOT NULL,
        channel_id  TEXT NOT NULL UNIQUE,
        status      TEXT DEFAULT 'open',
        claimed_by  TEXT,
        opened_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        closed_at   TEXT,
        closed_by   TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tk_guild  ON tickets(guild_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tk_opener ON tickets(opener_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tk_status ON tickets(status)')

    # ===== SOCIAL ALERTS =====
    # Notifie un salon Discord quand un createur publie sur une plateforme.
    # platform : 'twitch' (live) | 'youtube' (nouvelle video) | 'reddit' (nouveau post)
    # target_id : pseudo Twitch / channel_id YouTube (UCxxxx) / username Reddit
    # last_seen_id : video_id youtube / post_id reddit / 'live'/'offline' twitch
    c.execute('''CREATE TABLE IF NOT EXISTS social_alerts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id         TEXT NOT NULL,
        platform         TEXT NOT NULL,
        target_id        TEXT NOT NULL,
        target_label     TEXT,
        channel_id       TEXT NOT NULL,
        message_template TEXT,
        last_seen_id     TEXT,
        last_check_at    TEXT,
        enabled          INTEGER NOT NULL DEFAULT 1,
        created_by       TEXT,
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sa_guild    ON social_alerts(guild_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sa_enabled  ON social_alerts(enabled)')

    # ===== REACTION ROLES =====
    # Mapping (guild, message, emoji) -> role. mode='toggle' = ajout/retrait
    # standard, 'add_only' = retire pas le role quand l'user enleve la reaction,
    # 'unique' = au sein d'un meme group_key, un seul role actif (radio).
    c.execute('''CREATE TABLE IF NOT EXISTS reaction_roles (
        guild_id    TEXT NOT NULL,
        message_id  TEXT NOT NULL,
        channel_id  TEXT NOT NULL,
        emoji       TEXT NOT NULL,
        role_id     TEXT NOT NULL,
        mode        TEXT DEFAULT 'toggle',
        group_key   TEXT,
        created_by  TEXT,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, message_id, emoji)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rr_message ON reaction_roles(message_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rr_guild   ON reaction_roles(guild_id)')

    # Migration : colonnes label + position pour reaction_roles avances
    for col, ddl in [
        ("label",    "TEXT"),
        ("position", "INTEGER DEFAULT 0"),
    ]:
        if col not in _table_columns(c, "reaction_roles"):
            try:
                c.execute(f"ALTER TABLE reaction_roles ADD COLUMN {col} {ddl}")
            except Exception:
                pass

    # ===== CUSTOM COMMANDS (dashboard-only builder) =====
    c.execute('''CREATE TABLE IF NOT EXISTS custom_commands (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id       TEXT NOT NULL,
        name           TEXT NOT NULL,
        description    TEXT,
        response_text  TEXT,
        response_embed TEXT,
        use_embed      INTEGER DEFAULT 0,
        enabled        INTEGER DEFAULT 1,
        created_by     TEXT,
        uses_count     INTEGER DEFAULT 0,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at     TEXT,
        UNIQUE(guild_id, name)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_cc_guild ON custom_commands(guild_id)')

    # ===== GIVEAWAYS =====
    c.execute('''CREATE TABLE IF NOT EXISTS giveaways (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id      TEXT NOT NULL,
        channel_id    TEXT NOT NULL,
        message_id    TEXT,
        prize         TEXT NOT NULL,
        winners_count INTEGER NOT NULL DEFAULT 1,
        ends_at       TEXT NOT NULL,
        created_by    TEXT,
        ended         INTEGER DEFAULT 0,
        cancelled     INTEGER DEFAULT 0,
        winner_ids    TEXT,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_gw_guild ON giveaways(guild_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_gw_ended ON giveaways(ended)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_gw_ends_at ON giveaways(ends_at)')
    c.execute('''CREATE TABLE IF NOT EXISTS giveaway_entries (
        giveaway_id INTEGER NOT NULL,
        user_id     TEXT NOT NULL,
        joined_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (giveaway_id, user_id),
        FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
    )''')

    # ===== MODERATION : sanctions + config auto-timeout =====
    c.execute('''CREATE TABLE IF NOT EXISTS mod_actions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id      TEXT NOT NULL,
        user_id       TEXT NOT NULL,
        action_type   TEXT NOT NULL,
        reason        TEXT,
        moderator_id  TEXT,
        duration_sec  INTEGER,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        revoked_at    TEXT,
        revoked_by    TEXT,
        revoke_reason TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ma_guild_user ON mod_actions(guild_id, user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ma_action     ON mod_actions(action_type)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ma_active     ON mod_actions(revoked_at)')

    c.execute('''CREATE TABLE IF NOT EXISTS mod_config (
        guild_id              TEXT PRIMARY KEY,
        autotimeout_threshold INTEGER DEFAULT 0,
        autotimeout_duration  INTEGER DEFAULT 600,
        modlog_channel_id     TEXT,
        updated_at            TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== CS2 : profils lies (steam / faceit / premier elo declare) =====
    c.execute('''CREATE TABLE IF NOT EXISTS cs_profiles (
        discord_id  TEXT PRIMARY KEY,
        steam_id    TEXT,
        faceit_id   TEXT,
        faceit_nick TEXT,
        premier_elo INTEGER,
        linked_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at  TEXT
    )''')
    # Config rank-role par guild (active ou non, plus l'ID de role par palier)
    c.execute('''CREATE TABLE IF NOT EXISTS cs_rank_config (
        guild_id       TEXT PRIMARY KEY,
        enabled        INTEGER DEFAULT 0,
        role_grey      TEXT,
        role_lightblue TEXT,
        role_blue      TEXT,
        role_purple    TEXT,
        role_pink      TEXT,
        role_red       TEXT,
        role_gold      TEXT,
        updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Lobbies queue Premier (voice channels temporaires). Auto-delete quand vide.
    c.execute('''CREATE TABLE IF NOT EXISTS cs_queue_lobbies (
        channel_id TEXT PRIMARY KEY,
        guild_id   TEXT NOT NULL,
        creator_id TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Cache leger (skin prices, stats) pour reduire les hits API
    c.execute('''CREATE TABLE IF NOT EXISTS cs_cache (
        cache_key  TEXT PRIMARY KEY,
        data       TEXT,
        fetched_at REAL
    )''')

    # ===== MONETIZATION (Discord SKU / entitlements) =====
    # Stocke chaque entitlement Discord recu (achat utilisateur).
    # Pour SKU "durable" (achat unique), starts_at est rempli, ends_at NULL et deleted=0
    # signifient un premium permanent. Pour "subscription", ends_at borne la periode active.
    c.execute('''CREATE TABLE IF NOT EXISTS entitlements (
        entitlement_id TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL,
        sku_id         TEXT NOT NULL,
        type           INTEGER,
        starts_at      TEXT,
        ends_at        TEXT,
        consumed       INTEGER DEFAULT 0,
        deleted        INTEGER DEFAULT 0,
        raw_json       TEXT,
        updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_entitlements_user ON entitlements(user_id)')

    # Reglages premium par utilisateur (carte /niveau personnalisee, etc.).
    c.execute('''CREATE TABLE IF NOT EXISTS premium_settings (
        user_id           TEXT PRIMARY KEY,
        niveau_background TEXT DEFAULT 'default',
        updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Migration : nouvelles colonnes cosmetiques Pass (titre + emoji selectionnes)
    for col, ddl in [
        ("pass_selected_title", "TEXT DEFAULT NULL"),
        ("pass_selected_emoji", "TEXT DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE premium_settings ADD COLUMN {col} {ddl}")
        except Exception:
            pass

    # Grants premium manuels (owner offre la feature gratuitement, comptes test, etc.).
    # feature='all' = pack complet, 'pass' = Battle Pass, ou cle specifique.
    c.execute('''CREATE TABLE IF NOT EXISTS premium_grants (
        user_id    TEXT NOT NULL,
        feature    TEXT NOT NULL DEFAULT 'all',
        granted_by TEXT,
        granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
        note       TEXT,
        PRIMARY KEY (user_id, feature)
    )''')

    # ===== BATTLE PASS =====
    # Une saison = 1 mois calendaire. month_key au format 'YYYY-MM'.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_seasons (
        season_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        month_key   TEXT UNIQUE NOT NULL,
        name        TEXT,
        started_at  TEXT NOT NULL,
        ends_at     TEXT NOT NULL,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Definition des recompenses par palier (1..30) et par saison.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_rewards (
        season_id   INTEGER NOT NULL,
        tier        INTEGER NOT NULL,
        type        TEXT NOT NULL,            -- 'bg' | 'sabre' | 'title' | 'emoji' | 'boost_xp' | 'tookcoins'
        payload     TEXT,                     -- JSON: {bg_id} ou {sabre_id} ou {title} etc.
        label       TEXT,
        PRIMARY KEY (season_id, tier),
        FOREIGN KEY (season_id) REFERENCES pass_seasons(season_id) ON DELETE CASCADE
    )''')

    # Pool de templates de quetes ; on tire dedans au reset daily/weekly.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_quest_templates (
        template_id INTEGER PRIMARY KEY AUTOINCREMENT,
        type        TEXT NOT NULL,            -- 'send_messages' | 'play_duels' | 'earn_xp' | 'use_commands'
        period      TEXT NOT NULL,            -- 'daily' | 'weekly'
        target      INTEGER NOT NULL,
        label       TEXT NOT NULL,
        xp_reward   INTEGER NOT NULL DEFAULT 50
    )''')

    # Quetes actives d'un user pour la periode courante.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_user_quests (
        user_id      TEXT NOT NULL,
        period       TEXT NOT NULL,           -- 'daily' | 'weekly'
        slot         INTEGER NOT NULL,        -- 0..N pour distinguer plusieurs quetes meme periode
        template_id  INTEGER,
        type         TEXT NOT NULL,
        target       INTEGER NOT NULL,
        progress     INTEGER NOT NULL DEFAULT 0,
        period_start TEXT NOT NULL,           -- ISO date debut periode (YYYY-MM-DD pour daily, YYYY-Www pour weekly)
        claimed      INTEGER NOT NULL DEFAULT 0,
        xp_reward    INTEGER NOT NULL DEFAULT 50,
        PRIMARY KEY (user_id, period, slot, period_start)
    )''')

    # Progression du user dans la saison (XP du Pass != XP message).
    c.execute('''CREATE TABLE IF NOT EXISTS pass_progress (
        user_id          TEXT NOT NULL,
        season_id        INTEGER NOT NULL,
        xp               INTEGER NOT NULL DEFAULT 0,
        claimed_max_tier INTEGER NOT NULL DEFAULT 0,
        updated_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, season_id)
    )''')

    # Recompenses debloquees par un user. expires_at NULL = permanent
    # (sabres, titres). Sinon date d'expiration (BG saisonniers = fin du mois suivant).
    c.execute('''CREATE TABLE IF NOT EXISTS pass_unlocks (
        unlock_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      TEXT NOT NULL,
        season_id    INTEGER,
        type         TEXT NOT NULL,
        payload      TEXT,                    -- JSON
        unlocked_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        expires_at   TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pass_unlocks_user ON pass_unlocks(user_id)')

    conn.commit()
    conn.close()

    # Seed de la table sabres si vide (depuis duel_sabres.SABRES_DEFAULT)
    seed_sabres_si_vide()
    seed_pass_quest_templates_si_vide()
    # Migration : nettoie d'abord les sabres saisonniers casses (raretes invalides)
    cleanup_legacy_seasonal_sabres()
    # Re-seed sabres saisonniers + pass_rewards pour saisons existantes
    _migrate_pass_rewards_and_sabres()
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
        from duel.sabres import SABRES_DEFAULT
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

def get_activity_by_day(guild_id=None, days=14):
    """Compte les logs par jour sur les `days` derniers jours.
       guild_id=None -> cross-server. Retourne [{date, count}, ...] (ASC, dates en string YYYY-MM-DD).
       Inclut les jours sans activite (count=0)."""
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute("""SELECT DATE(ts) AS day, COUNT(*) AS n
                     FROM logs WHERE guild_id = ? AND ts >= datetime('now', ?)
                     GROUP BY day""", (str(guild_id), f"-{int(days)} days"))
    else:
        c.execute("""SELECT DATE(ts) AS day, COUNT(*) AS n
                     FROM logs WHERE ts >= datetime('now', ?)
                     GROUP BY day""", (f"-{int(days)} days",))
    by_day = {r["day"]: r["n"] for r in c.fetchall()}
    conn.close()
    # Generer la liste complete
    import datetime as _dt
    today = _dt.date.today()
    out = []
    for i in range(int(days) - 1, -1, -1):
        d = today - _dt.timedelta(days=i)
        ds = d.isoformat()
        out.append({"date": ds, "count": by_day.get(ds, 0)})
    return out

def get_xp_by_day(guild_id=None, days=14):
    """Approximation : on n'a pas de log XP par event, mais on peut deduire l'activite via les logs de type 'command' + actions message.
       Pour l'instant on renvoie le COUNT de logs de type action_message_* + command par jour comme proxy d'activite."""
    conn = get_db()
    c = conn.cursor()
    where = "type IN ('command', 'action_message_delete', 'action_message_edit', 'action_voice_join', 'action_member_join')"
    if guild_id:
        c.execute(f"""SELECT DATE(ts) AS day, COUNT(*) AS n
                      FROM logs WHERE guild_id = ? AND {where} AND ts >= datetime('now', ?)
                      GROUP BY day""", (str(guild_id), f"-{int(days)} days"))
    else:
        c.execute(f"""SELECT DATE(ts) AS day, COUNT(*) AS n
                      FROM logs WHERE {where} AND ts >= datetime('now', ?)
                      GROUP BY day""", (f"-{int(days)} days",))
    by_day = {r["day"]: r["n"] for r in c.fetchall()}
    conn.close()
    import datetime as _dt
    today = _dt.date.today()
    out = []
    for i in range(int(days) - 1, -1, -1):
        d = today - _dt.timedelta(days=i)
        ds = d.isoformat()
        out.append({"date": ds, "count": by_day.get(ds, 0)})
    return out

def get_activity_heatmap(guild_id=None, weeks=4):
    """Heatmap 7 jours x 24h sur les `weeks` dernieres semaines.
       Retourne [[count_mon_h0, count_mon_h1, ...], [count_tue_h0, ...], ...] (7 lignes x 24 cols)."""
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute("""SELECT
                       CAST(strftime('%w', ts) AS INTEGER) AS dow,
                       CAST(strftime('%H', ts) AS INTEGER) AS hour,
                       COUNT(*) AS n
                     FROM logs WHERE guild_id = ? AND ts >= datetime('now', ?)
                     GROUP BY dow, hour""", (str(guild_id), f"-{int(weeks*7)} days"))
    else:
        c.execute("""SELECT
                       CAST(strftime('%w', ts) AS INTEGER) AS dow,
                       CAST(strftime('%H', ts) AS INTEGER) AS hour,
                       COUNT(*) AS n
                     FROM logs WHERE ts >= datetime('now', ?)
                     GROUP BY dow, hour""", (f"-{int(weeks*7)} days",))
    matrix = [[0]*24 for _ in range(7)]
    for r in c.fetchall():
        # SQLite : strftime %w => 0=Dimanche..6=Samedi. On reorganise en 0=Lundi..6=Dimanche
        dow = (r["dow"] - 1) % 7
        matrix[dow][r["hour"]] = r["n"]
    conn.close()
    return matrix

def get_heatmap_cell_detail(guild_id=None, dow=0, hour=0, weeks=4, limit=10):
    """Detail d'une cellule heatmap (dow + hour) sur les dernieres `weeks`
    semaines. dow=0..6 ou 0=Lundi (consistant avec get_activity_heatmap).
    Retourne (total, top_rows) ou top_rows = liste {type, content, n}."""
    sql_dow = (dow + 1) % 7  # SQLite %w : 0=Dimanche
    conn = get_db()
    c = conn.cursor()
    params_total = []
    params_top = []
    if guild_id:
        gfilter = "guild_id = ? AND "
        params_total = [str(guild_id), f"-{int(weeks*7)} days", sql_dow, hour]
        params_top   = [str(guild_id), f"-{int(weeks*7)} days", sql_dow, hour, limit]
    else:
        gfilter = ""
        params_total = [f"-{int(weeks*7)} days", sql_dow, hour]
        params_top   = [f"-{int(weeks*7)} days", sql_dow, hour, limit]

    c.execute(f"""SELECT COUNT(*) AS n FROM logs WHERE {gfilter}
                   ts >= datetime('now', ?)
                   AND CAST(strftime('%w', ts) AS INTEGER) = ?
                   AND CAST(strftime('%H', ts) AS INTEGER) = ?""",
              params_total)
    total = c.fetchone()["n"]

    c.execute(f"""SELECT type, content, COUNT(*) AS n FROM logs WHERE {gfilter}
                   ts >= datetime('now', ?)
                   AND CAST(strftime('%w', ts) AS INTEGER) = ?
                   AND CAST(strftime('%H', ts) AS INTEGER) = ?
                   GROUP BY type, content ORDER BY n DESC LIMIT ?""",
              params_top)
    top = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"total": total, "top": top}


def get_top_commands(guild_id=None, days=30, limit=10):
    """Top des commandes les plus utilisees."""
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute("""SELECT
                       SUBSTR(content, 1, INSTR(content || ' ', ' ') - 1) AS cmd,
                       COUNT(*) AS n
                     FROM logs WHERE guild_id = ? AND type = 'command' AND ts >= datetime('now', ?)
                     GROUP BY cmd ORDER BY n DESC LIMIT ?""",
                  (str(guild_id), f"-{int(days)} days", int(limit)))
    else:
        c.execute("""SELECT
                       SUBSTR(content, 1, INSTR(content || ' ', ' ') - 1) AS cmd,
                       COUNT(*) AS n
                     FROM logs WHERE type = 'command' AND ts >= datetime('now', ?)
                     GROUP BY cmd ORDER BY n DESC LIMIT ?""",
                  (f"-{int(days)} days", int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_top_active_users(guild_id, days=30, limit=10):
    """Users les plus actifs (envois de commandes + messages edit/delete) sur N jours."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT user_id, MAX(username) AS username, COUNT(*) AS n
                 FROM logs WHERE guild_id = ? AND ts >= datetime('now', ?) AND user_id IS NOT NULL
                 GROUP BY user_id ORDER BY n DESC LIMIT ?""",
              (str(guild_id), f"-{int(days)} days", int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def prune_logs_global(keep_per_guild=5000, max_age_days=90):
    """Purge globale logs : limite N par guild + supprime > max_age_days. Retourne dict counts."""
    conn = get_db()
    c = conn.cursor()
    # 1. Purge par age
    c.execute("DELETE FROM logs WHERE ts < datetime('now', ?)", (f"-{int(max_age_days)} days",))
    by_age = c.rowcount
    # 2. Purge par guild (garde les N plus recents)
    c.execute("SELECT DISTINCT guild_id FROM logs")
    guilds = [r["guild_id"] for r in c.fetchall()]
    by_count = 0
    for gid in guilds:
        c.execute("""DELETE FROM logs WHERE id IN (
                       SELECT id FROM logs WHERE guild_id = ?
                       ORDER BY ts DESC LIMIT -1 OFFSET ?
                     )""", (str(gid), int(keep_per_guild)))
        by_count += c.rowcount
    conn.commit()
    # VACUUM pour récupérer l'espace disque
    try:
        c.execute("VACUUM")
    except Exception:
        pass
    conn.close()
    return {"by_age": by_age, "by_count": by_count}


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

# ===== DM MESSAGES (global cross-guild) =====
def save_dm(user_id, username, direction, content=None, attachments=None, avatar_url=None):
    """direction = 'in' (user -> bot) ou 'out' (bot -> user via dashboard)."""
    import json
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO dm_messages
                 (user_id, username, avatar_url, direction, content, attachments)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (str(user_id), username, avatar_url, direction, content,
               json.dumps(attachments) if attachments else None))
    msg_id = c.lastrowid
    conn.commit()
    conn.close()
    return msg_id

def list_dm_conversations():
    """Liste des users avec qui le bot a echange en DM, dernier message + count unread."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT
            user_id,
            (SELECT username   FROM dm_messages d2 WHERE d2.user_id = d1.user_id AND d2.username   IS NOT NULL ORDER BY ts DESC LIMIT 1) AS username,
            (SELECT avatar_url FROM dm_messages d2 WHERE d2.user_id = d1.user_id AND d2.avatar_url IS NOT NULL ORDER BY ts DESC LIMIT 1) AS avatar_url,
            (SELECT content   FROM dm_messages d2 WHERE d2.user_id = d1.user_id ORDER BY ts DESC LIMIT 1) AS last_content,
            (SELECT direction FROM dm_messages d2 WHERE d2.user_id = d1.user_id ORDER BY ts DESC LIMIT 1) AS last_direction,
            MAX(ts) AS last_ts,
            SUM(CASE WHEN direction = 'in' AND read_at IS NULL THEN 1 ELSE 0 END) AS unread
        FROM dm_messages d1
        GROUP BY user_id
        ORDER BY last_ts DESC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_dm_conversation(user_id, limit=200):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT * FROM dm_messages WHERE user_id = ?
                 ORDER BY ts DESC LIMIT ?""", (str(user_id), int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    rows.reverse()  # plus ancien -> plus recent
    return rows

def mark_dm_read(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""UPDATE dm_messages SET read_at = CURRENT_TIMESTAMP
                 WHERE user_id = ? AND direction = 'in' AND read_at IS NULL""",
              (str(user_id),))
    conn.commit()
    conn.close()

def count_unread_dms():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS n FROM dm_messages WHERE direction = 'in' AND read_at IS NULL")
    n = c.fetchone()["n"]
    conn.close()
    return n

def delete_dm_conversation(user_id):
    """Supprime tous les messages echanges avec un user donne. Retourne le nombre supprime."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM dm_messages WHERE user_id = ?", (str(user_id),))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


# ===== SETTINGS (config dynamique) =====
DEFAULT_SETTINGS = {
    "xp_min":               "1",
    "xp_max":               "5",
    "xp_cooldown_seconds":  "30",
    "log_retention_days":   "90",
    "log_keep_per_guild":   "5000",
    "welcome_template":     "👋 Bienvenue {user} !\nBienvenue sur **{guild}** ! Tu es le membre numéro **{count}**.",
}

def get_setting(key, default=None):
    if default is None:
        default = DEFAULT_SETTINGS.get(key)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO settings (key, value, updated_at)
                 VALUES (?, ?, CURRENT_TIMESTAMP)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
              (key, str(value)))
    conn.commit()
    conn.close()

def get_all_settings():
    """Retourne dict : key -> value (avec defaults appliques)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings")
    db = {r["key"]: r["value"] for r in c.fetchall()}
    conn.close()
    out = dict(DEFAULT_SETTINGS)
    out.update(db)
    return out


GUILD_DEFAULT_SETTINGS = {
    "xp_enabled":      "1",
    "music":           "1",
    "giveaway":        "1",
    "fun":             "1",
    "moderation_cmds": "1",
    "tickets":         "1",
    "welcome":         "1",
    "rolereaction":    "1",
    "reactions":       "1",
    "social_alerts":   "1",
    "custom_commands": "1",
    "poll":            "1",
    "cs2":             "1",
    "lol":             "1",
    "duels":           "1",
}

def guild_setting_get(guild_id, key, default=None):
    if default is None:
        default = GUILD_DEFAULT_SETTINGS.get(key)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
              (str(guild_id), key))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default

def guild_setting_set(guild_id, key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO guild_settings (guild_id, key, value, updated_at)
                 VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                 ON CONFLICT(guild_id, key) DO UPDATE SET
                   value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
              (str(guild_id), key, str(value)))
    conn.commit()
    conn.close()

def guild_settings_all(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM guild_settings WHERE guild_id = ?", (str(guild_id),))
    db = {r["key"]: r["value"] for r in c.fetchall()}
    conn.close()
    out = dict(GUILD_DEFAULT_SETTINGS)
    out.update(db)
    return out


# ===== DAILY LOGIN BONUS =====
def daily_claim_get(user_id):
    """Etat actuel : {last_claim_date, streak, total_claims}."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT last_claim_date, streak, total_claims FROM daily_claims WHERE user_id = ?",
              (str(user_id),))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"last_claim_date": None, "streak": 0, "total_claims": 0}
    return dict(row)

PROMO_REWARD_TYPES = {"tookcoins", "pass_xp", "premium_grant_days"}

def promo_code_create(code, reward_type, reward_value, max_uses=1, expires_at=None, note=None):
    if reward_type not in PROMO_REWARD_TYPES:
        raise ValueError(f"reward_type invalide: {reward_type}")
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO promo_codes (code, reward_type, reward_value, max_uses, expires_at, note)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (code.upper(), reward_type, int(reward_value), int(max_uses),
               expires_at, note))
    conn.commit()
    conn.close()

def promo_code_get(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM promo_codes WHERE code = ?", (code.upper(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def promo_codes_list():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def promo_code_delete(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM promo_codes WHERE code = ?", (code.upper(),))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n

def promo_redeem_check(code, user_id):
    """Verifie sans appliquer : (ok, reason, promo_dict)."""
    promo = promo_code_get(code)
    if not promo:
        return False, "code_invalid", None
    if promo["max_uses"] > 0 and promo["used_count"] >= promo["max_uses"]:
        return False, "max_uses_reached", promo
    exp = promo.get("expires_at")
    if exp:
        try:
            import datetime as _d
            if _d.datetime.fromisoformat(exp.replace("Z", "+00:00")) < _d.datetime.now(_d.timezone.utc):
                return False, "expired", promo
        except Exception:
            pass
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM promo_redemptions WHERE code = ? AND user_id = ?",
              (code.upper(), str(user_id)))
    already = c.fetchone()
    conn.close()
    if already:
        return False, "already_redeemed", promo
    return True, "ok", promo

def promo_redeem_apply(code, user_id):
    """Marque la redemption (atomic). Le caller doit appliquer le reward."""
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO promo_redemptions (code, user_id) VALUES (?, ?)",
              (code.upper(), str(user_id)))
    c.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
              (code.upper(),))
    conn.commit()
    conn.close()


# ===== LEAGUE OF LEGENDS =====
def lol_profile_get(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM lol_profiles WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def lol_profile_upsert(user_id, *, puuid, game_name, tag_line, platform,
                       summoner_id=None, summoner_level=None):
    import datetime as _d
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO lol_profiles
                   (user_id, puuid, summoner_id, game_name, tag_line, platform,
                    summoner_level, last_synced)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(user_id) DO UPDATE SET
                   puuid          = excluded.puuid,
                   summoner_id    = excluded.summoner_id,
                   game_name      = excluded.game_name,
                   tag_line       = excluded.tag_line,
                   platform       = excluded.platform,
                   summoner_level = excluded.summoner_level,
                   last_synced    = excluded.last_synced""",
              (str(user_id), puuid, summoner_id, game_name, tag_line, platform,
               summoner_level,
               _d.datetime.utcnow().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()


def lol_profile_unlink(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM lol_profiles WHERE user_id = ?", (str(user_id),))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


def lol_rank_config_get(guild_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM lol_rank_config WHERE guild_id = ?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"guild_id": str(guild_id), "enabled": 0, "role_map": None}
    return dict(row)


_SCOUT_COLORS = ["#E74C3C", "#3498DB", "#2ECC71", "#F1C40F", "#9B59B6",
                 "#1ABC9C", "#E67E22", "#34495E"]


def lol_scout_session_create(slug, owner_id, platform, riot_ids, scout_data):
    import json as _j
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO lol_scout_sessions
                   (slug, owner_id, platform, riot_ids, scout_data, status)
                 VALUES (?, ?, ?, ?, ?, 'active')""",
              (slug, str(owner_id), platform, _j.dumps(riot_ids), _j.dumps(scout_data)))
    conn.commit()
    conn.close()


def lol_scout_session_get(slug):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM lol_scout_sessions WHERE slug = ?", (slug,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def lol_scout_session_stop(slug, owner_id=None):
    conn = get_db()
    c = conn.cursor()
    if owner_id:
        c.execute("""UPDATE lol_scout_sessions SET status='stopped',
                       ended_at=CURRENT_TIMESTAMP
                     WHERE slug=? AND owner_id=? AND status='active'""",
                  (slug, str(owner_id)))
    else:
        c.execute("""UPDATE lol_scout_sessions SET status='stopped',
                       ended_at=CURRENT_TIMESTAMP
                     WHERE slug=? AND status='active'""", (slug,))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


def lol_scout_sessions_list(owner_id=None, status=None, limit=50):
    conn = get_db()
    c = conn.cursor()
    q = "SELECT * FROM lol_scout_sessions WHERE 1=1"
    params = []
    if owner_id:
        q += " AND owner_id=?"
        params.append(str(owner_id))
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    c.execute(q, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def lol_scout_user_join(slug, pseudo):
    """Renvoie {pseudo, color}. Si pseudo deja pris dans la session, ré-use.
    Color assignee dans l'ordre d'arrivee."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM lol_scout_users WHERE session_slug=? AND pseudo=?",
              (slug, pseudo))
    row = c.fetchone()
    if row:
        c.execute("UPDATE lol_scout_users SET last_seen=CURRENT_TIMESTAMP WHERE id=?",
                  (row["id"],))
        conn.commit()
        conn.close()
        return {"pseudo": pseudo, "color": row["color"]}
    c.execute("SELECT COUNT(*) AS n FROM lol_scout_users WHERE session_slug=?", (slug,))
    n = c.fetchone()["n"]
    color = _SCOUT_COLORS[n % len(_SCOUT_COLORS)]
    c.execute("""INSERT INTO lol_scout_users (session_slug, pseudo, color)
                 VALUES (?, ?, ?)""", (slug, pseudo, color))
    conn.commit()
    conn.close()
    return {"pseudo": pseudo, "color": color}


def lol_scout_chat_add(slug, pseudo, color, message):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO lol_scout_chat (session_slug, pseudo, color, message)
                 VALUES (?, ?, ?, ?)""", (slug, pseudo, color, message[:500]))
    conn.commit()
    chat_id = c.lastrowid
    conn.close()
    return chat_id


def lol_scout_chat_list(slug, since_id=0, limit=100):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT * FROM lol_scout_chat
                 WHERE session_slug=? AND id>?
                 ORDER BY id ASC LIMIT ?""",
              (slug, int(since_id), int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def lol_scout_annot_add(slug, pseudo, color, kind, data_json):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO lol_scout_annotations
                   (session_slug, pseudo, color, kind, data)
                 VALUES (?, ?, ?, ?, ?)""",
              (slug, pseudo, color, kind, data_json))
    conn.commit()
    aid = c.lastrowid
    conn.close()
    return aid


def lol_scout_annot_list(slug, since_id=0, limit=500):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT * FROM lol_scout_annotations
                 WHERE session_slug=? AND id>?
                 ORDER BY id ASC LIMIT ?""",
              (slug, int(since_id), int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def lol_rank_config_upsert(guild_id, *, enabled=None, role_map=None):
    import json as _j
    cur = lol_rank_config_get(guild_id)
    new_enabled = int(enabled) if enabled is not None else int(cur.get("enabled") or 0)
    new_map_str = _j.dumps(role_map) if role_map is not None else cur.get("role_map")
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO lol_rank_config (guild_id, enabled, role_map, updated_at)
                 VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                 ON CONFLICT(guild_id) DO UPDATE SET
                   enabled    = excluded.enabled,
                   role_map   = excluded.role_map,
                   updated_at = CURRENT_TIMESTAMP""",
              (str(guild_id), new_enabled, new_map_str))
    conn.commit()
    conn.close()


def daily_claim_apply(user_id, today_str, new_streak):
    """Marque la claim du jour et bump le streak. Idempotent par jour."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO daily_claims (user_id, last_claim_date, streak, total_claims, updated_at)
                 VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                 ON CONFLICT(user_id) DO UPDATE SET
                   last_claim_date = excluded.last_claim_date,
                   streak          = excluded.streak,
                   total_claims    = total_claims + 1,
                   updated_at      = CURRENT_TIMESTAMP""",
              (str(user_id), today_str, new_streak))
    conn.commit()
    conn.close()


# ===== GUILD MEMBERS (cache) =====
def upsert_member(guild_id, user_id, username, avatar_url=None, is_bot=False, joined_at=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO guild_members (guild_id, user_id, username, avatar_url, is_bot, joined_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                 ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   username = excluded.username,
                   avatar_url = excluded.avatar_url,
                   is_bot = excluded.is_bot,
                   joined_at = COALESCE(excluded.joined_at, guild_members.joined_at),
                   updated_at = CURRENT_TIMESTAMP""",
              (str(guild_id), str(user_id), username, avatar_url, 1 if is_bot else 0,
               joined_at.isoformat() if joined_at else None))
    conn.commit()
    conn.close()

def remove_member(guild_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM guild_members WHERE guild_id = ? AND user_id = ?",
              (str(guild_id), str(user_id)))
    conn.commit()
    conn.close()

def list_members(guild_id, include_bots=False, search=None, limit=200):
    conn = get_db()
    c = conn.cursor()
    where = ["guild_id = ?"]
    args  = [str(guild_id)]
    if not include_bots:
        where.append("is_bot = 0")
    if search:
        where.append("(LOWER(username) LIKE ? OR user_id LIKE ?)")
        like = f"%{search.lower()}%"
        args += [like, like]
    args.append(int(limit))
    sql = f"""SELECT * FROM guild_members WHERE {' AND '.join(where)}
              ORDER BY username COLLATE NOCASE LIMIT ?"""
    c.execute(sql, args)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def replace_guild_members(guild_id, members):
    """Bulk replace. members = list of dicts {user_id, username, avatar_url, is_bot, joined_at}."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM guild_members WHERE guild_id = ?", (str(guild_id),))
    for m in members:
        c.execute("""INSERT INTO guild_members (guild_id, user_id, username, avatar_url, is_bot, joined_at)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (str(guild_id), str(m["user_id"]), m.get("username"),
                   m.get("avatar_url"), 1 if m.get("is_bot") else 0,
                   m["joined_at"].isoformat() if m.get("joined_at") else None))
    conn.commit()
    conn.close()


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
    c.execute("SELECT channel_id, message FROM welcome WHERE guild_id = ?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def set_welcome(guild_id, channel_id, message=None):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT INTO welcome (guild_id, channel_id, message)
           VALUES (?, ?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET
               channel_id = excluded.channel_id,
               message = excluded.message""",
        (str(guild_id), channel_id, message),
    )
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


# ===== MONETIZATION HELPERS =====

import json as _json
import datetime as _dt


def upsert_entitlement(entitlement: dict):
    """Insert or update an entitlement received from Discord.

    `entitlement` is the raw dict from discord.py's Entitlement.to_dict() or
    equivalent. Required keys: id, user_id, sku_id. Optional: type,
    starts_at, ends_at, consumed, deleted.
    """
    if not entitlement:
        return
    eid = str(entitlement.get("id") or "")
    if not eid:
        return
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO entitlements (entitlement_id, user_id, sku_id, type,
                                  starts_at, ends_at, consumed, deleted,
                                  raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(entitlement_id) DO UPDATE SET
            user_id    = excluded.user_id,
            sku_id     = excluded.sku_id,
            type       = excluded.type,
            starts_at  = excluded.starts_at,
            ends_at    = excluded.ends_at,
            consumed   = excluded.consumed,
            deleted    = excluded.deleted,
            raw_json   = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
    ''', (
        eid,
        str(entitlement.get("user_id") or ""),
        str(entitlement.get("sku_id") or ""),
        int(entitlement.get("type") or 0),
        entitlement.get("starts_at"),
        entitlement.get("ends_at"),
        int(bool(entitlement.get("consumed"))),
        int(bool(entitlement.get("deleted"))),
        _json.dumps(entitlement, default=str),
    ))
    conn.commit()
    conn.close()


def mark_entitlement_deleted(entitlement_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE entitlements SET deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE entitlement_id = ?",
        (str(entitlement_id),),
    )
    conn.commit()
    conn.close()


def user_has_active_entitlement(user_id, sku_id=None) -> bool:
    """Return True if user has at least one non-deleted, non-expired entitlement.

    If sku_id is given, restricts to that SKU.
    """
    conn = get_db()
    c = conn.cursor()
    now = _dt.datetime.utcnow().isoformat()
    if sku_id:
        rows = c.execute(
            "SELECT ends_at FROM entitlements WHERE user_id = ? AND sku_id = ? AND deleted = 0",
            (str(user_id), str(sku_id)),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT ends_at FROM entitlements WHERE user_id = ? AND deleted = 0",
            (str(user_id),),
        ).fetchall()
    conn.close()
    for r in rows:
        end = r["ends_at"]
        if not end:
            return True  # achat unique permanent
        if end > now:
            return True
    return False


def list_user_entitlements(user_id):
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM entitlements WHERE user_id = ? ORDER BY updated_at DESC",
        (str(user_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_premium_settings(user_id) -> dict:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM premium_settings WHERE user_id = ?",
        (str(user_id),),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"user_id": str(user_id), "niveau_background": "default"}


def add_premium_grant(user_id, feature="all", granted_by=None, note=None):
    """Accorde manuellement la feature premium a un utilisateur."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO premium_grants (user_id, feature, granted_by, granted_at, note)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(user_id, feature) DO UPDATE SET
            granted_by = excluded.granted_by,
            granted_at = CURRENT_TIMESTAMP,
            note       = excluded.note
    ''', (str(user_id), feature, str(granted_by) if granted_by else None, note))
    conn.commit()
    conn.close()


def remove_premium_grant(user_id, feature="all"):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM premium_grants WHERE user_id = ? AND feature = ?",
        (str(user_id), feature),
    )
    conn.commit()
    conn.close()


def has_premium_grant(user_id, feature="all", inherit_all: bool = True) -> bool:
    """True si l'user a un grant manuel pour cette feature.

    Par defaut, un grant feature='all' compte aussi (master pack premium).
    Passer `inherit_all=False` pour exiger un grant strictement sur la feature
    demandee. Utile pour les abonnements distincts (ex. Battle Pass) qui ne
    doivent PAS etre auto-debloques par le grant 'all' du /niveau Premium.
    """
    conn = get_db()
    c = conn.cursor()
    if inherit_all:
        row = c.execute(
            "SELECT 1 FROM premium_grants WHERE user_id = ? AND feature IN (?, 'all') LIMIT 1",
            (str(user_id), feature),
        ).fetchone()
    else:
        row = c.execute(
            "SELECT 1 FROM premium_grants WHERE user_id = ? AND feature = ? LIMIT 1",
            (str(user_id), feature),
        ).fetchone()
    conn.close()
    return bool(row)


def list_premium_grants(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id:
        rows = c.execute(
            "SELECT * FROM premium_grants WHERE user_id = ? ORDER BY granted_at DESC",
            (str(user_id),),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM premium_grants ORDER BY granted_at DESC",
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_is_premium(user_id, feature="all", owner_id=None) -> bool:
    """Combinaison unifiee : entitlement Discord OU grant manuel OU owner ENV.

    `owner_id` (str) est lu depuis DISCORD_OWNER_ID a l'appel ; le passer
    explicitement evite l'import os ici.
    """
    if not user_id:
        return False
    uid = str(user_id)
    if owner_id and uid == str(owner_id):
        return True
    if has_premium_grant(uid, feature):
        return True
    if user_has_active_entitlement(uid):
        return True
    return False


def set_premium_setting(user_id, key: str, value):
    """Update a single premium setting column for the user."""
    allowed = {"niveau_background", "pass_selected_title", "pass_selected_emoji"}
    if key not in allowed:
        raise ValueError(f"Unknown premium setting: {key}")
    conn = get_db()
    c = conn.cursor()
    c.execute(
        f'''
        INSERT INTO premium_settings (user_id, {key}, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            {key}      = excluded.{key},
            updated_at = CURRENT_TIMESTAMP
        ''',
        (str(user_id), value),
    )
    conn.commit()
    conn.close()


def _current_month_key(now=None):
    now = now or _dt.datetime.utcnow()
    return now.strftime("%Y-%m")


def _month_bounds(month_key: str) -> tuple[str, str]:
    """Retourne (start_iso, end_iso) du mois donne 'YYYY-MM'."""
    y, m = map(int, month_key.split("-"))
    start = _dt.datetime(y, m, 1)
    if m == 12:
        end = _dt.datetime(y + 1, 1, 1) - _dt.timedelta(seconds=1)
    else:
        end = _dt.datetime(y, m + 1, 1) - _dt.timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def _seasonal_bg_expiry(unlocked_at: _dt.datetime) -> str:
    """BG saisonnier : expire fin du mois SUIVANT le mois de deblocage.

    Exemple : deblocage 28 mai 2026 -> expire 30 juin 2026 23:59:59.
    """
    y, m = unlocked_at.year, unlocked_at.month
    if m == 12:
        ey, em = y + 1, 12
        # Mois suivant decembre = janvier annee+2
        if em == 12:
            ny, nm = ey, 12  # decembre meme annee
        ny, nm = y + 2, 1
        end = _dt.datetime(y + 2, 1, 1) - _dt.timedelta(seconds=1)
    else:
        # Mois suivant
        nm = m + 1
        ny = y
        if nm == 12:
            end = _dt.datetime(ny + 1, 1, 1) - _dt.timedelta(seconds=1)
        else:
            end = _dt.datetime(ny, nm + 1, 1) - _dt.timedelta(seconds=1)
    return end.isoformat()


_PASS_QUEST_TEMPLATES_DEFAULT = [
    # type, period, target, label, xp_reward
    ("send_messages", "daily",  10,  "Envoie 10 messages",                50),
    ("send_messages", "daily",  30,  "Envoie 30 messages",                50),
    ("send_messages", "daily",  50,  "Envoie 50 messages",                50),
    ("play_duels",    "daily",  1,   "Joue 1 duel",                       50),
    ("play_duels",    "daily",  3,   "Joue 3 duels",                      50),
    ("earn_xp",       "daily",  100, "Gagne 100 XP message",              50),
    ("earn_xp",       "daily",  250, "Gagne 250 XP message",              50),
    ("use_commands",  "daily",  3,   "Utilise 3 slash commands",          50),
    ("use_commands",  "daily",  8,   "Utilise 8 slash commands",          50),
    ("send_messages", "weekly", 200,  "Envoie 200 messages cette semaine", 250),
    ("send_messages", "weekly", 500,  "Envoie 500 messages cette semaine", 250),
    ("play_duels",    "weekly", 10,   "Joue 10 duels cette semaine",       250),
    ("play_duels",    "weekly", 25,   "Joue 25 duels cette semaine",       250),
    ("earn_xp",       "weekly", 1500, "Gagne 1500 XP cette semaine",       250),
    ("earn_xp",       "weekly", 3000, "Gagne 3000 XP cette semaine",       250),
    ("use_commands",  "weekly", 25,   "Utilise 25 slash commands",         250),
    ("use_commands",  "weekly", 60,   "Utilise 60 slash commands",         250),
]


def seed_pass_quest_templates_si_vide():
    conn = get_db()
    c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM pass_quest_templates").fetchone()["n"]
    if n == 0:
        c.executemany(
            "INSERT INTO pass_quest_templates (type, period, target, label, xp_reward) VALUES (?, ?, ?, ?, ?)",
            _PASS_QUEST_TEMPLATES_DEFAULT,
        )
        conn.commit()
        print(f"[seed] {len(_PASS_QUEST_TEMPLATES_DEFAULT)} pass_quest_templates")
    else:
        # Migration : rebalance daily earn_xp 200/500 -> 100/250 (anciennes versions trop hardcore).
        c.execute(
            "UPDATE pass_quest_templates SET target = 100, label = 'Gagne 100 XP message' WHERE type='earn_xp' AND period='daily' AND target = 200"
        )
        c.execute(
            "UPDATE pass_quest_templates SET target = 250, label = 'Gagne 250 XP message' WHERE type='earn_xp' AND period='daily' AND target = 500"
        )
        if c.rowcount:
            print("[migration] pass_quest_templates earn_xp daily rebalance")
        conn.commit()
    conn.close()


def _current_period_start(period: str, now: _dt.datetime = None) -> str:
    """Renvoie le marqueur de debut de periode :
    - daily  -> 'YYYY-MM-DD' UTC
    - weekly -> 'YYYY-Www' ISO week
    """
    now = now or _dt.datetime.utcnow()
    if period == "daily":
        return now.strftime("%Y-%m-%d")
    if period == "weekly":
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return now.strftime("%Y-%m-%d")


def ensure_user_quests(user_id, period: str, slots: int = 3, now: _dt.datetime = None) -> list[dict]:
    """Assure que l'user a `slots` quetes pour la periode courante.

    Si la periode a change ou aucune quete n'existe, on tire `slots` templates
    aleatoires distincts dans le pool et on les insere. Sinon retourne tel quel.
    """
    import random as _random
    period_start = _current_period_start(period, now)
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM pass_user_quests WHERE user_id = ? AND period = ? AND period_start = ? ORDER BY slot",
        (str(user_id), period, period_start),
    ).fetchall()
    if rows:
        conn.close()
        return [dict(r) for r in rows]

    # Tire `slots` templates differents pour cette periode
    templates = c.execute(
        "SELECT * FROM pass_quest_templates WHERE period = ?", (period,)
    ).fetchall()
    if not templates:
        conn.close()
        return []
    chosen = _random.sample(templates, k=min(slots, len(templates)))
    for slot, t in enumerate(chosen):
        c.execute(
            '''INSERT OR IGNORE INTO pass_user_quests
               (user_id, period, slot, template_id, type, target, progress, period_start, claimed, xp_reward)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?)''',
            (str(user_id), period, slot, t["template_id"], t["type"], t["target"],
             period_start, t["xp_reward"]),
        )
    conn.commit()
    rows = c.execute(
        "SELECT * FROM pass_user_quests WHERE user_id = ? AND period = ? AND period_start = ? ORDER BY slot",
        (str(user_id), period, period_start),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_user_active_quests(user_id) -> list[dict]:
    """Retourne les quetes daily + weekly de la periode courante (les genere si besoin)."""
    quests = []
    quests += ensure_user_quests(user_id, "daily",  slots=3)
    quests += ensure_user_quests(user_id, "weekly", slots=3)
    return quests


def increment_quest_progress(user_id, quest_type: str, amount: int = 1) -> list[dict]:
    """Incremente le progress de TOUTES les quetes actives matchant ce type.

    Retourne la liste des quetes nouvellement completees (target atteint sur
    cet appel) — utile pour creditter l'XP du Pass et notifier l'utilisateur.
    """
    if not user_id or amount <= 0:
        return []
    completed_now = []
    for period in ("daily", "weekly"):
        period_start = _current_period_start(period)
        conn = get_db()
        c = conn.cursor()
        rows = c.execute(
            '''SELECT * FROM pass_user_quests
               WHERE user_id = ? AND period = ? AND period_start = ? AND type = ?''',
            (str(user_id), period, period_start, quest_type),
        ).fetchall()
        for r in rows:
            old_progress = r["progress"]
            target = r["target"]
            if old_progress >= target:
                continue  # deja complete
            new_progress = min(target, old_progress + amount)
            c.execute(
                '''UPDATE pass_user_quests SET progress = ?
                   WHERE user_id = ? AND period = ? AND slot = ? AND period_start = ?''',
                (new_progress, str(user_id), period, r["slot"], period_start),
            )
            if new_progress >= target and old_progress < target:
                completed_now.append(dict(r) | {"progress": new_progress})
        conn.commit()
        conn.close()
    return completed_now


def claim_quest_reward(user_id, period: str, slot: int) -> dict | None:
    """Marque la quete claimed et retourne {xp_reward, ...} si OK, sinon None."""
    period_start = _current_period_start(period)
    conn = get_db()
    c = conn.cursor()
    r = c.execute(
        '''SELECT * FROM pass_user_quests
           WHERE user_id = ? AND period = ? AND slot = ? AND period_start = ?''',
        (str(user_id), period, slot, period_start),
    ).fetchone()
    if not r:
        conn.close()
        return None
    if r["claimed"] or r["progress"] < r["target"]:
        conn.close()
        return None
    c.execute(
        '''UPDATE pass_user_quests SET claimed = 1
           WHERE user_id = ? AND period = ? AND slot = ? AND period_start = ?''',
        (str(user_id), period, slot, period_start),
    )
    conn.commit()
    conn.close()
    return dict(r)


def get_or_create_current_season(name: str = None) -> dict:
    """Retourne la saison du mois courant, en la creant si elle n'existe pas.

    A la creation, on seed automatiquement les 30 paliers de recompenses et
    les 3 sabres cosmetiques de la saison.
    """
    mk = _current_month_key()
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM pass_seasons WHERE month_key = ?", (mk,)).fetchone()
    if row:
        conn.close()
        return dict(row)
    start_iso, end_iso = _month_bounds(mk)
    season_name = name or f"Saison {mk}"
    c.execute(
        "INSERT INTO pass_seasons (month_key, name, started_at, ends_at) VALUES (?, ?, ?, ?)",
        (mk, season_name, start_iso, end_iso),
    )
    conn.commit()
    sid = c.lastrowid
    row = c.execute("SELECT * FROM pass_seasons WHERE season_id = ?", (sid,)).fetchone()
    conn.close()
    seed_pass_rewards_for_season(sid, mk)
    seed_seasonal_sabres(mk)
    return dict(row)


# Aucun TookCoin pour eviter le P2W (TookCoins servent aux duels/sabres).
# Recompenses purement cosmetiques + boosts XP message (limites dans le temps).
# Format : (tier, type, payload_dict, label)
_PASS_TIER_MAP = [
    (1,  "boost_xp",  {"hours": 0.5, "multiplier": 2.0}, "Boost XP ×2 pendant 30min"),
    (2,  "title",     {"title": "Initié"},               "Titre : Initié"),
    (3,  "boost_xp",  {"hours": 1, "multiplier": 2.0},   "Boost XP ×2 pendant 1h"),
    (4,  "bg",        {"index": 0},                      "Background saisonnier #1"),
    (5,  "emoji",     {"emoji": "🌱"},                   "Emoji 🌱"),
    (6,  "title",     {"title": "Adepte"},               "Titre : Adepte"),
    (7,  "boost_xp",  {"hours": 1, "multiplier": 2.0},   "Boost XP ×2 pendant 1h"),
    (8,  "emoji",     {"emoji": "🔥"},                   "Emoji 🔥"),
    (9,  "bg",        {"index": 1},                      "Background saisonnier #2"),
    (10, "sabre",     {"rarete": "R"},                   "Sabre cosmétique R (Rare)"),
    (11, "emoji",     {"emoji": "⚡"},                   "Emoji ⚡"),
    (12, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (13, "title",     {"title": "Vétéran"},              "Titre : Vétéran"),
    (14, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (15, "bg",        {"index": 2},                      "Background saisonnier #3"),
    (16, "emoji",     {"emoji": "💎"},                   "Emoji 💎"),
    (17, "title",     {"title": "Élu"},                  "Titre : Élu"),
    (18, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (19, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (20, "sabre",     {"rarete": "SR"},                  "Sabre cosmétique SR (Super Rare)"),
    (21, "emoji",     {"emoji": "🌊"},                   "Emoji 🌊"),
    (22, "boost_xp",  {"hours": 3, "multiplier": 2.0},   "Boost XP ×2 pendant 3h"),
    (23, "bg",        {"index": 3},                      "Background saisonnier #4"),
    (24, "emoji",     {"emoji": "🎯"},                   "Emoji 🎯"),
    (25, "title",     {"title": "Maître"},               "Titre : Maître"),
    (26, "boost_xp",  {"hours": 3, "multiplier": 2.0},   "Boost XP ×2 pendant 3h"),
    (27, "title",     {"title": "Légende"},              "Titre : Légende"),
    (28, "emoji",     {"emoji": "🌟"},                   "Emoji 🌟"),
    (29, "bg",        {"index": 4},                      "Background saisonnier #5"),
    (30, "sabre",     {"rarete": "SSR"},                 "Sabre cosmétique SSR (Super Super Rare)"),
]

_SEASONAL_BG_NAMES = [
    "crystal_cave", "liquid_chrome", "neon_tokyo", "stained_glass", "cosmic_vortex",
]


def _migrate_pass_rewards_and_sabres():
    """A chaque demarrage : assure que toutes les saisons existantes ont
    leurs sabres saisonniers + leur pass_rewards a jour avec le _PASS_TIER_MAP
    courant. Les unlocks deja accordes ne sont PAS revoques."""
    conn = get_db()
    c = conn.cursor()
    seasons = c.execute("SELECT season_id, month_key FROM pass_seasons").fetchall()
    conn.close()
    for s in seasons:
        try:
            seed_seasonal_sabres(s["month_key"])
        except Exception as e:
            print(f"[migrate] seasonal sabres {s['month_key']} error: {e!r}")
        try:
            # Re-seed pass_rewards : drop + insert pour appliquer le nouveau mapping
            conn = get_db(); c = conn.cursor()
            c.execute("DELETE FROM pass_rewards WHERE season_id = ?", (s["season_id"],))
            conn.commit()
            conn.close()
            seed_pass_rewards_for_season(s["season_id"], s["month_key"])
        except Exception as e:
            print(f"[migrate] pass_rewards season {s['season_id']} error: {e!r}")


def seed_pass_rewards_for_season(season_id: int, month_key: str):
    """Insere les 30 lignes pass_rewards pour une saison. Idempotent."""
    conn = get_db()
    c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM pass_rewards WHERE season_id = ?", (season_id,)).fetchone()["n"]
    if n > 0:
        conn.close()
        return
    rows = []
    for tier, rtype, payload, label in _PASS_TIER_MAP:
        # Pour les BG, resoudre le nom saisonnier
        if rtype == "bg":
            bg_name = _SEASONAL_BG_NAMES[payload["index"]]
            payload = {"bg_id": f"seasonal:{month_key}:{bg_name}", "bg_name": bg_name}
        rows.append((season_id, tier, rtype, _json.dumps(payload), label))
    c.executemany(
        "INSERT INTO pass_rewards (season_id, tier, type, payload, label) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"[seed] pass_rewards x{len(rows)} for season {season_id} ({month_key})")


def seed_seasonal_sabres(month_key: str):
    """Cree 3 sabres cosmetiques (R/SR/SSR) avec stats ET effets equivalents
    aux sabres f2p de meme rarete (anti-P2W). Seuls le visuel (nom, emoji,
    description) change.

    R   -> overcharge   (75% degats sup + ignore defense, comme Sabre Cyan)
    SR  -> reflect_100  (renvoie 100% degats, comme Sabre Argent)
    SSR -> ultimate     (combo supreme, comme Sabre Arc-en-Ciel)

    IDs : season_<YYYY-MM>_<R|SR|SSR>"""
    conn = get_db()
    c = conn.cursor()
    sabres_data = [
        (f"season_{month_key}_R",
         f"Lame Saisonnière {month_key}", "🌒", "R", 0,
         "Skin saisonnier du Battle Pass · stats identiques aux sabres R classiques.",
         "Surcharge Saisonnière",
         "Inflige 75% de dégâts supplémentaires et ignore la défense.",
         "🌙", "overcharge"),
        (f"season_{month_key}_SR",
         f"Croissant Saisonnier {month_key}", "🌘", "SR", 0,
         "Skin saisonnier du Battle Pass · stats identiques aux sabres SR classiques.",
         "Réflexion Saisonnière",
         "Renvoie 100% des dégâts au prochain coup adverse.",
         "🪞", "reflect_100"),
        (f"season_{month_key}_SSR",
         f"Étoile Saisonnière {month_key}", "🌟", "SSR", 0,
         "Skin saisonnier légendaire du Battle Pass · stats identiques aux sabres SSR classiques.",
         "Apothéose Saisonnière",
         "Cumule les effets : 100% de dégâts + ignore défense + lifesteal 100%.",
         "👑", "ultimate"),
    ]
    for s in sabres_data:
        try:
            c.execute('''INSERT OR IGNORE INTO sabres
                        (id, nom, emoji, rarete, prix, description,
                         speciale_nom, speciale_description, speciale_emoji, speciale_effet)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', s)
        except Exception as e:
            print(f"[seed sabres saisonniers] error: {e!r}")
    conn.commit()
    conn.close()


def cleanup_legacy_seasonal_sabres():
    """Migration : supprime les anciens sabres saisonniers crees avec des
    raretes invalides (rare/epique/legendaire). Nettoie aussi duel_collection
    et reset sabre_equipe sur 'bleu' pour les profils qui les avaient equipes."""
    conn = get_db()
    c = conn.cursor()
    bad = c.execute(
        "SELECT id FROM sabres WHERE id LIKE 'season_%_rare' OR id LIKE 'season_%_epique' OR id LIKE 'season_%_legendaire'"
    ).fetchall()
    if not bad:
        conn.close()
        return
    ids = [r["id"] for r in bad]
    # Reset sabre_equipe sur 'bleu' si l'user avait un sabre legacy equipe
    c.execute(
        f"UPDATE duel_profil SET sabre_equipe = 'bleu' WHERE sabre_equipe IN ({','.join(['?'] * len(ids))})",
        ids,
    )
    # Vire les sabres legacy de duel_collection
    c.execute(
        f"DELETE FROM duel_collection WHERE sabre_id IN ({','.join(['?'] * len(ids))})",
        ids,
    )
    # Supprime les sabres eux-memes
    c.executemany("DELETE FROM sabres WHERE id = ?", [(i,) for i in ids])
    # Supprime les pass_unlocks qui pointaient sur ces sabres legacy
    c.execute(
        "DELETE FROM pass_unlocks WHERE type = 'sabre' AND payload LIKE '%season_%_rare%' OR payload LIKE '%season_%_epique%' OR payload LIKE '%season_%_legendaire%'"
    )
    conn.commit()
    conn.close()
    print(f"[migrate] cleanup legacy seasonal sabres: removed {len(ids)} ({ids})")


def get_pass_rewards_for_season(season_id: int) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM pass_rewards WHERE season_id = ? ORDER BY tier",
        (int(season_id),),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = _json.loads(d.get("payload") or "{}")
        except Exception:
            d["payload"] = {}
        out.append(d)
    return out


def auto_claim_pass_tiers(user_id, season_id: int, current_xp: int,
                           tier_xp: int = 250, max_tier: int = 30) -> list[dict]:
    """Compare XP du user au seuil de chaque palier non encore reclame ; declenche
    les rewards correspondants. Retourne la liste des rewards delivres.

    Les rewards instantanes (tookcoins) sont appliques directement.
    Les autres (bg, title, emoji, boost_xp, sabre) creent des entrees pass_unlocks
    avec expires_at adapte.
    """
    progress = get_pass_progress(user_id, season_id)
    claimed_max = progress.get("claimed_max_tier", 0)
    new_tier = min(max_tier, current_xp // tier_xp)
    if new_tier <= claimed_max:
        return []

    rewards_def = {r["tier"]: r for r in get_pass_rewards_for_season(season_id)}
    delivered = []
    season = None
    for t in range(claimed_max + 1, new_tier + 1):
        rdef = rewards_def.get(t)
        if not rdef:
            continue
        rtype = rdef["type"]
        payload = rdef.get("payload") or {}
        # Resolution selon type
        if rtype == "tookcoins":
            try:
                # S'assure que le profil duel existe (sinon UPDATE no-op)
                if get_duel_profil(user_id) is None:
                    creer_duel_profil(user_id, str(user_id))
                ajouter_tookcoins(user_id, int(payload.get("amount") or 0))
            except Exception as e:
                print(f"[auto_claim] tookcoins error: {e!r}")
        elif rtype == "sabre":
            # Ajoute le sabre saisonnier de la rarete a la collection
            mk = season["month_key"] if season else None
            if not mk:
                conn = get_db(); c = conn.cursor()
                row = c.execute("SELECT month_key FROM pass_seasons WHERE season_id = ?",
                                (season_id,)).fetchone()
                conn.close()
                if row:
                    mk = row["month_key"]
            sabre_id = f"season_{mk}_{payload.get('rarete')}"
            try:
                if get_duel_profil(user_id) is None:
                    creer_duel_profil(user_id, str(user_id))
                ajouter_sabre(user_id, sabre_id)
            except Exception as e:
                print(f"[auto_claim] sabre add error: {e!r}")
            # Aussi enregistrer dans pass_unlocks pour visibilite
            add_pass_unlock(user_id, "sabre", {"sabre_id": sabre_id, **payload}, season_id=season_id)
        elif rtype == "bg":
            # BG saisonnier : expire fin du mois SUIVANT
            now = _dt.datetime.utcnow()
            exp = _seasonal_bg_expiry(now)
            add_pass_unlock(user_id, "bg", payload, season_id=season_id, expires_at=exp)
        elif rtype == "boost_xp":
            # Active immediatement, expire dans N heures
            hours = float(payload.get("hours") or 1)
            exp = (_dt.datetime.utcnow() + _dt.timedelta(hours=hours)).isoformat()
            add_pass_unlock(user_id, "boost_xp", payload, season_id=season_id, expires_at=exp)
        elif rtype == "title":
            add_pass_unlock(user_id, "title", payload, season_id=season_id)  # permanent
        elif rtype == "emoji":
            add_pass_unlock(user_id, "emoji", payload, season_id=season_id)  # permanent
        else:
            print(f"[auto_claim] unknown reward type: {rtype}")

        delivered.append({
            "tier":    t,
            "type":    rtype,
            "payload": payload,
            "label":   rdef.get("label"),
        })

    # Update claimed_max_tier
    set_pass_claimed_tier(user_id, season_id, new_tier)
    return delivered


def get_user_cosmetic(user_id) -> dict:
    """Retourne le titre et l'emoji actifs (verifies dans les unlocks) pour cet user.

    Renvoie {'title': str|None, 'emoji': str|None}. Si l'user a selectionne un
    titre/emoji qu'il ne possede plus (cas rare : revoke), on renvoie None.
    """
    settings = get_premium_settings(user_id)
    sel_title = settings.get("pass_selected_title")
    sel_emoji = settings.get("pass_selected_emoji")
    out = {"title": None, "emoji": None}
    if not (sel_title or sel_emoji):
        return out
    unlocks = list_user_pass_unlocks(user_id, include_expired=False)
    titles_owned = set()
    emojis_owned = set()
    for u in unlocks:
        p = u.get("payload") or {}
        if u["type"] == "title" and p.get("title"):
            titles_owned.add(p["title"])
        elif u["type"] == "emoji" and p.get("emoji"):
            emojis_owned.add(p["emoji"])
    if sel_title and sel_title in titles_owned:
        out["title"] = sel_title
    if sel_emoji and sel_emoji in emojis_owned:
        out["emoji"] = sel_emoji
    return out


def list_user_owned_cosmetics(user_id) -> dict:
    """Retourne les listes 'titles' et 'emojis' que l'user possede via Pass."""
    unlocks = list_user_pass_unlocks(user_id, include_expired=False)
    titles, emojis = [], []
    for u in unlocks:
        p = u.get("payload") or {}
        if u["type"] == "title" and p.get("title") and p["title"] not in titles:
            titles.append(p["title"])
        elif u["type"] == "emoji" and p.get("emoji") and p["emoji"] not in emojis:
            emojis.append(p["emoji"])
    return {"titles": titles, "emojis": emojis}


def get_active_xp_boost_multiplier(user_id) -> float:
    """Retourne le plus haut multiplicateur boost_xp encore actif, sinon 1.0."""
    unlocks = list_user_pass_unlocks(user_id, type_="boost_xp", include_expired=False)
    best = 1.0
    for u in unlocks:
        try:
            m = float((u.get("payload") or {}).get("multiplier") or 1.0)
            best = max(best, m)
        except Exception:
            pass
    return best


def user_has_active_pass(user_id, sku_pass_id: str = None) -> bool:
    """Pass actif : grant manuel feature='pass' OU entitlement Discord
    sur le SKU subscription Pass.

    Le Battle Pass est un produit independant du /niveau Premium. Un grant
    feature='all' (Premium pack) ne deverrouille PAS le Pass automatiquement.
    Pour activer le check via SKU subscription Discord, passer `sku_pass_id`.
    L'owner gere son acces via _has_pass dans web.py / is_premium_user dans bot.py.
    """
    if not user_id:
        return False
    if has_premium_grant(user_id, feature="pass", inherit_all=False):
        return True
    if sku_pass_id and user_has_active_entitlement(user_id, sku_id=sku_pass_id):
        return True
    return False


def get_pass_progress(user_id, season_id: int) -> dict:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM pass_progress WHERE user_id = ? AND season_id = ?",
        (str(user_id), int(season_id)),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"user_id": str(user_id), "season_id": int(season_id), "xp": 0, "claimed_max_tier": 0}


def add_pass_xp(user_id, season_id: int, amount: int) -> int:
    """Incremente l'XP du Pass et retourne le nouveau total."""
    if amount <= 0:
        cur = get_pass_progress(user_id, season_id)
        return cur.get("xp", 0)
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO pass_progress (user_id, season_id, xp, claimed_max_tier, updated_at)
        VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, season_id) DO UPDATE SET
            xp         = pass_progress.xp + excluded.xp,
            updated_at = CURRENT_TIMESTAMP
    ''', (str(user_id), int(season_id), int(amount)))
    conn.commit()
    new_total = c.execute(
        "SELECT xp FROM pass_progress WHERE user_id = ? AND season_id = ?",
        (str(user_id), int(season_id)),
    ).fetchone()["xp"]
    conn.close()
    return new_total


def set_pass_claimed_tier(user_id, season_id: int, tier: int):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO pass_progress (user_id, season_id, xp, claimed_max_tier, updated_at)
        VALUES (?, ?, 0, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, season_id) DO UPDATE SET
            claimed_max_tier = MAX(pass_progress.claimed_max_tier, excluded.claimed_max_tier),
            updated_at = CURRENT_TIMESTAMP
    ''', (str(user_id), int(season_id), int(tier)))
    conn.commit()
    conn.close()


def add_pass_unlock(user_id, type_: str, payload: dict, season_id: int = None,
                    expires_at: str = None) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO pass_unlocks (user_id, season_id, type, payload, expires_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (str(user_id), season_id, type_, _json.dumps(payload or {}), expires_at))
    conn.commit()
    uid = c.lastrowid
    conn.close()
    return uid


def list_user_pass_unlocks(user_id, type_: str = None, include_expired=False) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    now = _dt.datetime.utcnow().isoformat()
    if type_:
        rows = c.execute(
            "SELECT * FROM pass_unlocks WHERE user_id = ? AND type = ? ORDER BY unlocked_at DESC",
            (str(user_id), type_),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM pass_unlocks WHERE user_id = ? ORDER BY unlocked_at DESC",
            (str(user_id),),
        ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = _json.loads(d.get("payload") or "{}")
        except Exception:
            d["payload"] = {}
        if not include_expired and d.get("expires_at") and d["expires_at"] < now:
            continue
        out.append(d)
    return out


def replace_guild_roles(guild_id, roles: list[dict]):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM guild_roles WHERE guild_id = ?", (str(guild_id),))
    for r in roles:
        c.execute('''INSERT INTO guild_roles
            (guild_id, role_id, name, color, position, managed, is_everyone)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (str(guild_id), str(r["role_id"]), r["name"],
             int(r.get("color", 0) or 0), int(r.get("position", 0) or 0),
             int(bool(r.get("managed"))), int(bool(r.get("is_everyone")))))
    conn.commit()
    conn.close()


def list_roles(guild_id, exclude_everyone=True, exclude_managed=True) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM guild_roles WHERE guild_id = ? ORDER BY position DESC",
        (str(guild_id),),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if exclude_everyone and d.get("is_everyone"):
            continue
        if exclude_managed and d.get("managed"):
            continue
        out.append(d)
    return out


def reaction_role_add(guild_id, message_id, channel_id, emoji: str,
                       role_id, mode: str = "toggle", group_key: str = None,
                       created_by=None, label: str = None, position: int = 0):
    """Insere un mapping reaction-role. UPSERT sur (guild, message, emoji)."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO reaction_roles
            (guild_id, message_id, channel_id, emoji, role_id, mode, group_key, created_by, label, position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, message_id, emoji) DO UPDATE SET
            channel_id = excluded.channel_id,
            role_id    = excluded.role_id,
            mode       = excluded.mode,
            group_key  = excluded.group_key,
            label      = excluded.label,
            position   = excluded.position
    ''', (str(guild_id), str(message_id), str(channel_id), emoji,
          str(role_id), mode, group_key, str(created_by) if created_by else None,
          label, int(position)))
    conn.commit()
    conn.close()


def reaction_role_remove(guild_id, message_id, emoji: str):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
        (str(guild_id), str(message_id), emoji),
    )
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted


def reaction_role_remove_message(guild_id, message_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM reaction_roles WHERE guild_id = ? AND message_id = ?",
        (str(guild_id), str(message_id)),
    )
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted


def reaction_role_get(guild_id, message_id, emoji: str) -> dict | None:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
        (str(guild_id), str(message_id), emoji),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def reaction_role_list(guild_id, message_id=None) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    if message_id:
        rows = c.execute(
            "SELECT * FROM reaction_roles WHERE guild_id = ? AND message_id = ? ORDER BY rowid",
            (str(guild_id), str(message_id)),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM reaction_roles WHERE guild_id = ? ORDER BY message_id, rowid",
            (str(guild_id),),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reaction_role_list_unique_group(guild_id, message_id, group_key: str) -> list[dict]:
    """Retourne tous les mappings d'un meme groupe 'unique' sur ce message."""
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        '''SELECT * FROM reaction_roles
           WHERE guild_id = ? AND message_id = ? AND group_key = ? AND mode = 'unique' ''',
        (str(guild_id), str(message_id), group_key),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ticket_panel_create(guild_id, channel_id, panel_title=None, panel_description=None,
                        button_label="Ouvrir un ticket", button_emoji="🎫",
                        button_style="primary", support_role_id=None, category_id=None,
                        welcome_message=None, created_by=None) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO ticket_panels
        (guild_id, channel_id, panel_title, panel_description,
         button_label, button_emoji, button_style,
         support_role_id, category_id, welcome_message, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (str(guild_id), str(channel_id), panel_title, panel_description,
         button_label, button_emoji, button_style,
         str(support_role_id) if support_role_id else None,
         str(category_id) if category_id else None,
         welcome_message, str(created_by) if created_by else None))
    conn.commit()
    pid = c.lastrowid
    conn.close()
    return pid


def ticket_panel_set_message(panel_id, message_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE ticket_panels SET message_id = ? WHERE id = ?",
              (str(message_id), int(panel_id)))
    conn.commit()
    conn.close()


def ticket_panel_get(panel_id) -> dict | None:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM ticket_panels WHERE id = ?", (int(panel_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def ticket_panel_get_by_message(guild_id, message_id) -> dict | None:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM ticket_panels WHERE guild_id = ? AND message_id = ?",
        (str(guild_id), str(message_id)),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def ticket_panels_list(guild_id) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM ticket_panels WHERE guild_id = ? ORDER BY id DESC",
        (str(guild_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ticket_panel_delete(panel_id, guild_id=None) -> int:
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute("DELETE FROM ticket_panels WHERE id = ? AND guild_id = ?",
                  (int(panel_id), str(guild_id)))
    else:
        c.execute("DELETE FROM ticket_panels WHERE id = ?", (int(panel_id),))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


def ticket_create(guild_id, panel_id, opener_id, channel_id) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO tickets (guild_id, panel_id, opener_id, channel_id)
                 VALUES (?, ?, ?, ?)''',
              (str(guild_id), int(panel_id) if panel_id else None,
               str(opener_id), str(channel_id)))
    conn.commit()
    tid = c.lastrowid
    conn.close()
    return tid


def ticket_get_by_channel(channel_id) -> dict | None:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM tickets WHERE channel_id = ?",
                    (str(channel_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def ticket_get_open_by_user(guild_id, opener_id, panel_id=None) -> dict | None:
    conn = get_db()
    c = conn.cursor()
    if panel_id:
        row = c.execute(
            '''SELECT * FROM tickets WHERE guild_id = ? AND opener_id = ?
                 AND panel_id = ? AND status = 'open' LIMIT 1''',
            (str(guild_id), str(opener_id), int(panel_id)),
        ).fetchone()
    else:
        row = c.execute(
            '''SELECT * FROM tickets WHERE guild_id = ? AND opener_id = ?
                 AND status = 'open' LIMIT 1''',
            (str(guild_id), str(opener_id)),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def ticket_set_claimed(ticket_id, claimed_by):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET claimed_by = ? WHERE id = ?",
              (str(claimed_by), int(ticket_id)))
    conn.commit()
    conn.close()


def ticket_set_status(ticket_id, status: str, closed_by=None):
    conn = get_db()
    c = conn.cursor()
    if status in ("closed", "deleted"):
        c.execute('''UPDATE tickets SET status = ?, closed_at = CURRENT_TIMESTAMP,
                                          closed_by = ? WHERE id = ?''',
                  (status, str(closed_by) if closed_by else None, int(ticket_id)))
    else:
        c.execute("UPDATE tickets SET status = ?, closed_at = NULL, closed_by = NULL WHERE id = ?",
                  (status, int(ticket_id)))
    conn.commit()
    conn.close()


def tickets_list(guild_id, status=None, limit=200) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    if status:
        rows = c.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND status = ? ORDER BY id DESC LIMIT ?",
            (str(guild_id), status, int(limit)),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM tickets WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (str(guild_id), int(limit)),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def social_alert_create(guild_id, platform: str, target_id: str, channel_id,
                         target_label: str = None, message_template: str = None,
                         created_by=None) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO social_alerts
        (guild_id, platform, target_id, target_label, channel_id,
         message_template, enabled, created_by)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)''',
        (str(guild_id), platform, target_id, target_label, str(channel_id),
         message_template, str(created_by) if created_by else None))
    conn.commit()
    aid = c.lastrowid
    conn.close()
    return aid


def social_alert_delete(alert_id, guild_id=None) -> int:
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute("DELETE FROM social_alerts WHERE id = ? AND guild_id = ?",
                  (int(alert_id), str(guild_id)))
    else:
        c.execute("DELETE FROM social_alerts WHERE id = ?", (int(alert_id),))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


def social_alert_set_enabled(alert_id, enabled: bool, guild_id=None):
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute("UPDATE social_alerts SET enabled = ? WHERE id = ? AND guild_id = ?",
                  (1 if enabled else 0, int(alert_id), str(guild_id)))
    else:
        c.execute("UPDATE social_alerts SET enabled = ? WHERE id = ?",
                  (1 if enabled else 0, int(alert_id)))
    conn.commit()
    conn.close()


def social_alert_update_seen(alert_id, last_seen_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute('''UPDATE social_alerts
        SET last_seen_id = ?, last_check_at = CURRENT_TIMESTAMP
        WHERE id = ?''',
        (last_seen_id, int(alert_id)))
    conn.commit()
    conn.close()


def social_alert_touch_check(alert_id):
    """Met a jour last_check_at sans toucher last_seen_id."""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE social_alerts SET last_check_at = CURRENT_TIMESTAMP WHERE id = ?',
              (int(alert_id),))
    conn.commit()
    conn.close()


def social_alert_reset(alert_id, guild_id=None) -> int:
    """Efface last_seen_id pour forcer une re-detection au prochain poll."""
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        c.execute('UPDATE social_alerts SET last_seen_id = NULL, last_check_at = NULL '
                  'WHERE id = ? AND guild_id = ?',
                  (int(alert_id), str(guild_id)))
    else:
        c.execute('UPDATE social_alerts SET last_seen_id = NULL, last_check_at = NULL '
                  'WHERE id = ?',
                  (int(alert_id),))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


def social_alerts_list(guild_id=None, enabled_only: bool = False) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    sql  = "SELECT * FROM social_alerts WHERE 1=1"
    args = []
    if guild_id:
        sql += " AND guild_id = ?"
        args.append(str(guild_id))
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY id DESC"
    rows = c.execute(sql, tuple(args)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cs_profile_get(discord_id) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM cs_profiles WHERE discord_id = ?", (str(discord_id),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def cs_profile_upsert(discord_id, *, steam_id=None, faceit_id=None,
                      faceit_nick=None, premier_elo=None) -> None:
    """Insère ou met à jour un profil. Seuls les params non-None sont écrits."""
    conn = get_db()
    c = conn.cursor()
    existing = c.execute("SELECT 1 FROM cs_profiles WHERE discord_id = ?",
                         (str(discord_id),)).fetchone()
    if existing:
        sets, args = [], []
        if steam_id is not None:
            sets.append("steam_id = ?");    args.append(steam_id)
        if faceit_id is not None:
            sets.append("faceit_id = ?");   args.append(faceit_id)
        if faceit_nick is not None:
            sets.append("faceit_nick = ?"); args.append(faceit_nick)
        if premier_elo is not None:
            sets.append("premier_elo = ?"); args.append(int(premier_elo))
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            args.append(str(discord_id))
            c.execute(f"UPDATE cs_profiles SET {', '.join(sets)} WHERE discord_id = ?", args)
    else:
        c.execute("""INSERT INTO cs_profiles (discord_id, steam_id, faceit_id, faceit_nick, premier_elo)
                     VALUES (?, ?, ?, ?, ?)""",
                  (str(discord_id), steam_id, faceit_id, faceit_nick,
                   int(premier_elo) if premier_elo is not None else None))
    conn.commit()
    conn.close()


def cs_profile_unlink(discord_id, platform: str) -> None:
    """platform = 'steam' | 'faceit' | 'all'."""
    conn = get_db()
    c = conn.cursor()
    if platform == "steam":
        c.execute("UPDATE cs_profiles SET steam_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE discord_id = ?", (str(discord_id),))
    elif platform == "faceit":
        c.execute("UPDATE cs_profiles SET faceit_id = NULL, faceit_nick = NULL, updated_at = CURRENT_TIMESTAMP WHERE discord_id = ?", (str(discord_id),))
    elif platform == "all":
        c.execute("DELETE FROM cs_profiles WHERE discord_id = ?", (str(discord_id),))
    conn.commit()
    conn.close()


def cs_rank_config_get(guild_id) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM cs_rank_config WHERE guild_id = ?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"guild_id": str(guild_id), "enabled": 0,
            "role_grey": None, "role_lightblue": None, "role_blue": None,
            "role_purple": None, "role_pink": None, "role_red": None,
            "role_gold": None}


def cs_rank_config_upsert(guild_id, *, enabled=None, **roles) -> None:
    """roles : role_grey, role_lightblue, role_blue, role_purple, role_pink, role_red, role_gold."""
    conn = get_db()
    c = conn.cursor()
    valid = {"role_grey", "role_lightblue", "role_blue", "role_purple",
             "role_pink", "role_red", "role_gold"}
    existing = c.execute("SELECT 1 FROM cs_rank_config WHERE guild_id = ?",
                         (str(guild_id),)).fetchone()
    if existing:
        sets, args = [], []
        if enabled is not None:
            sets.append("enabled = ?"); args.append(int(bool(enabled)))
        for k, v in roles.items():
            if k in valid:
                sets.append(f"{k} = ?"); args.append(str(v) if v else None)
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            args.append(str(guild_id))
            c.execute(f"UPDATE cs_rank_config SET {', '.join(sets)} WHERE guild_id = ?", args)
    else:
        cols = ["guild_id", "enabled"]
        vals = [str(guild_id), int(bool(enabled)) if enabled is not None else 0]
        for k, v in roles.items():
            if k in valid:
                cols.append(k); vals.append(str(v) if v else None)
        placeholders = ",".join("?" for _ in cols)
        c.execute(f"INSERT INTO cs_rank_config ({','.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()


def cs_queue_lobby_add(channel_id, guild_id, creator_id) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO cs_queue_lobbies (channel_id, guild_id, creator_id)
                 VALUES (?, ?, ?)""",
              (str(channel_id), str(guild_id), str(creator_id)))
    conn.commit()
    conn.close()


def cs_queue_lobby_delete(channel_id) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM cs_queue_lobbies WHERE channel_id = ?", (str(channel_id),))
    conn.commit()
    conn.close()


def cs_queue_lobbies_list(guild_id=None) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    if guild_id:
        rows = c.execute("SELECT * FROM cs_queue_lobbies WHERE guild_id = ?",
                         (str(guild_id),)).fetchall()
    else:
        rows = c.execute("SELECT * FROM cs_queue_lobbies").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cs_cache_get(cache_key: str, max_age_sec: int) -> Optional[dict]:
    import json as _json, time as _time
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT data, fetched_at FROM cs_cache WHERE cache_key = ?",
                    (cache_key,)).fetchone()
    conn.close()
    if not row:
        return None
    if _time.time() - (row["fetched_at"] or 0) > max_age_sec:
        return None
    try:
        return _json.loads(row["data"])
    except Exception:
        return None


def cs_cache_set(cache_key: str, data) -> None:
    import json as _json, time as _time
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO cs_cache (cache_key, data, fetched_at)
                 VALUES (?, ?, ?)""",
              (cache_key, _json.dumps(data, ensure_ascii=False), _time.time()))
    conn.commit()
    conn.close()


VALID_MOD_ACTIONS = {"warn", "kick", "ban", "unban", "timeout", "untimeout", "note"}


def mod_action_add(guild_id, user_id, action_type: str, *,
                   reason: Optional[str] = None,
                   moderator_id=None,
                   duration_sec: Optional[int] = None) -> int:
    if action_type not in VALID_MOD_ACTIONS:
        raise ValueError(f"action_type invalide: {action_type}")
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO mod_actions
                 (guild_id, user_id, action_type, reason, moderator_id, duration_sec)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (str(guild_id), str(user_id), action_type, reason,
               str(moderator_id) if moderator_id else None,
               int(duration_sec) if duration_sec else None))
    aid = c.lastrowid
    conn.commit()
    conn.close()
    return aid


def mod_actions_list(guild_id, *,
                     user_id=None,
                     action_types: Optional[list] = None,
                     include_revoked: bool = True,
                     limit: int = 100,
                     offset: int = 0) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    sql  = "SELECT * FROM mod_actions WHERE guild_id = ?"
    args = [str(guild_id)]
    if user_id:
        sql += " AND user_id = ?"
        args.append(str(user_id))
    if action_types:
        placeholders = ",".join("?" for _ in action_types)
        sql += f" AND action_type IN ({placeholders})"
        args.extend(action_types)
    if not include_revoked:
        sql += " AND revoked_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args.extend([int(limit), int(offset)])
    rows = c.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mod_action_get(action_id: int) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM mod_actions WHERE id = ?", (int(action_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def mod_action_revoke(action_id: int, revoked_by, revoke_reason: Optional[str] = None) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("""UPDATE mod_actions
                 SET revoked_at = CURRENT_TIMESTAMP, revoked_by = ?, revoke_reason = ?
                 WHERE id = ? AND revoked_at IS NULL""",
              (str(revoked_by) if revoked_by else None, revoke_reason, int(action_id)))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n > 0


def mod_action_count_active(guild_id, user_id, action_type: str = "warn") -> int:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("""SELECT COUNT(*) AS n FROM mod_actions
                       WHERE guild_id = ? AND user_id = ? AND action_type = ? AND revoked_at IS NULL""",
                    (str(guild_id), str(user_id), action_type)).fetchone()
    conn.close()
    return int(row["n"]) if row else 0


def mod_config_get(guild_id) -> dict:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM mod_config WHERE guild_id = ?", (str(guild_id),)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "guild_id": str(guild_id),
        "autotimeout_threshold": 0,
        "autotimeout_duration":  600,
        "modlog_channel_id":     None,
    }


def mod_config_upsert(guild_id, *,
                      autotimeout_threshold: Optional[int] = None,
                      autotimeout_duration:  Optional[int] = None,
                      modlog_channel_id:     Optional[str] = None) -> None:
    conn = get_db()
    c = conn.cursor()
    existing = c.execute("SELECT 1 FROM mod_config WHERE guild_id = ?",
                         (str(guild_id),)).fetchone()
    if existing:
        sets, args = [], []
        if autotimeout_threshold is not None:
            sets.append("autotimeout_threshold = ?"); args.append(int(autotimeout_threshold))
        if autotimeout_duration is not None:
            sets.append("autotimeout_duration = ?");  args.append(int(autotimeout_duration))
        if modlog_channel_id is not None:
            sets.append("modlog_channel_id = ?"); args.append(str(modlog_channel_id) if modlog_channel_id else None)
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            args.append(str(guild_id))
            c.execute(f"UPDATE mod_config SET {', '.join(sets)} WHERE guild_id = ?", args)
    else:
        c.execute("""INSERT INTO mod_config
                     (guild_id, autotimeout_threshold, autotimeout_duration, modlog_channel_id)
                     VALUES (?, ?, ?, ?)""",
                  (str(guild_id),
                   int(autotimeout_threshold) if autotimeout_threshold is not None else 0,
                   int(autotimeout_duration) if autotimeout_duration is not None else 600,
                   str(modlog_channel_id) if modlog_channel_id else None))
    conn.commit()
    conn.close()


import json as _json_gw


def giveaway_create(guild_id, channel_id, prize: str, winners_count: int,
                    ends_at_iso: str, created_by) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO giveaways
                 (guild_id, channel_id, prize, winners_count, ends_at, created_by)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (str(guild_id), str(channel_id), prize, int(winners_count),
               ends_at_iso, str(created_by) if created_by else None))
    gid_ = c.lastrowid
    conn.commit()
    conn.close()
    return gid_


def giveaway_set_message_id(giveaway_id: int, message_id) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE giveaways SET message_id = ? WHERE id = ?",
              (str(message_id), int(giveaway_id)))
    conn.commit()
    conn.close()


def giveaway_get(giveaway_id: int) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM giveaways WHERE id = ?", (int(giveaway_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def giveaway_get_by_message(message_id) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM giveaways WHERE message_id = ?", (str(message_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def giveaways_list(guild_id=None, *, only_active: bool = False,
                   limit: int = 100) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    sql  = "SELECT * FROM giveaways WHERE 1=1"
    args = []
    if guild_id:
        sql += " AND guild_id = ?"
        args.append(str(guild_id))
    if only_active:
        sql += " AND ended = 0 AND cancelled = 0"
    sql += " ORDER BY ends_at DESC LIMIT ?"
    args.append(int(limit))
    rows = c.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def giveaway_entry_add(giveaway_id: int, user_id) -> bool:
    """Retourne True si nouveau participant, False si deja inscrit."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                  (int(giveaway_id), str(user_id)))
        added = c.rowcount > 0
        conn.commit()
        return added
    finally:
        conn.close()


def giveaway_entry_remove(giveaway_id: int, user_id) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
              (int(giveaway_id), str(user_id)))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n > 0


def giveaway_entries(giveaway_id: int) -> list[str]:
    conn = get_db()
    c = conn.cursor()
    rows = c.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?",
                     (int(giveaway_id),)).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def giveaway_entries_count(giveaway_id: int) -> int:
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT COUNT(*) AS n FROM giveaway_entries WHERE giveaway_id = ?",
                    (int(giveaway_id),)).fetchone()
    conn.close()
    return int(row["n"]) if row else 0


def giveaway_set_ended(giveaway_id: int, winner_ids: list) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE giveaways SET ended = 1, winner_ids = ? WHERE id = ?",
              (_json_gw.dumps([str(w) for w in winner_ids]), int(giveaway_id)))
    conn.commit()
    conn.close()


def giveaway_cancel(giveaway_id: int) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE giveaways SET cancelled = 1, ended = 1 WHERE id = ?",
              (int(giveaway_id),))
    conn.commit()
    conn.close()


def giveaways_pending_finalize(now_iso: str) -> list[dict]:
    """Liste les giveaways non termines dont la date est passee."""
    conn = get_db()
    c = conn.cursor()
    rows = c.execute("""SELECT * FROM giveaways
                        WHERE ended = 0 AND cancelled = 0 AND ends_at <= ?""",
                     (now_iso,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


import re as _cc_re

CUSTOM_CMD_NAME_RE = _cc_re.compile(r"^[a-z0-9_-]{1,32}$")


def custom_cmd_upsert(guild_id, name: str, *,
                      description: Optional[str] = None,
                      response_text: Optional[str] = None,
                      response_embed: Optional[str] = None,
                      use_embed: bool = False,
                      enabled: bool = True,
                      created_by=None) -> int:
    if not CUSTOM_CMD_NAME_RE.match(name):
        raise ValueError("nom invalide : a-z 0-9 _ - uniquement, max 32 chars")
    conn = get_db()
    c = conn.cursor()
    existing = c.execute(
        "SELECT id FROM custom_commands WHERE guild_id = ? AND name = ?",
        (str(guild_id), name)
    ).fetchone()
    if existing:
        c.execute("""UPDATE custom_commands
                     SET description = COALESCE(?, description),
                         response_text  = ?,
                         response_embed = ?,
                         use_embed      = ?,
                         enabled        = ?,
                         updated_at     = CURRENT_TIMESTAMP
                     WHERE id = ?""",
                  (description, response_text, response_embed,
                   int(bool(use_embed)), int(bool(enabled)), existing["id"]))
        cid = existing["id"]
    else:
        c.execute("""INSERT INTO custom_commands
                     (guild_id, name, description, response_text, response_embed,
                      use_embed, enabled, created_by)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                  (str(guild_id), name, description, response_text, response_embed,
                   int(bool(use_embed)), int(bool(enabled)),
                   str(created_by) if created_by else None))
        cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid


def custom_cmd_get(guild_id, name: str) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM custom_commands WHERE guild_id = ? AND LOWER(name) = LOWER(?)",
        (str(guild_id), name)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def custom_cmds_list(guild_id, *, enabled_only: bool = False) -> list[dict]:
    conn = get_db()
    c = conn.cursor()
    sql  = "SELECT * FROM custom_commands WHERE guild_id = ?"
    args = [str(guild_id)]
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY name ASC"
    rows = c.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def custom_cmd_delete(guild_id, name: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM custom_commands WHERE guild_id = ? AND name = ?",
              (str(guild_id), name))
    n = c.rowcount
    conn.commit()
    conn.close()
    return n > 0


def custom_cmd_increment_uses(cmd_id: int) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE custom_commands SET uses_count = uses_count + 1 WHERE id = ?",
              (int(cmd_id),))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
