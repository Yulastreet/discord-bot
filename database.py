import os
import sqlite3
import datetime as _dt
from typing import Optional

# DB file configurable via env DB_PATH (dev = bot_database_dev.db par defaut)
DB_FILE = os.getenv("DB_PATH") or "bot_database.db"


def get_db():
    conn = sqlite3.connect(DB_FILE)
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
        owner_id     TEXT,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        active       INTEGER DEFAULT 1
    )''')
    # Migration : ajoute owner_id si table existait deja sans cette colonne.
    if "owner_id" not in _table_columns(c, "guilds"):
        try:
            c.execute("ALTER TABLE guilds ADD COLUMN owner_id TEXT")
        except Exception as _e:
            print(f"[db migration] add guilds.owner_id : {_e}")

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

    # ===== Telemetry lectures musique (stats top tracks / top requesters) =====
    c.execute('''CREATE TABLE IF NOT EXISTS music_plays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id     TEXT NOT NULL,
        user_id      TEXT,
        track_title  TEXT NOT NULL,
        track_url    TEXT,
        source       TEXT,
        duration     INTEGER,
        played_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_music_plays_guild_ts ON music_plays(guild_id, played_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_music_plays_title    ON music_plays(guild_id, track_title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_music_plays_user     ON music_plays(guild_id, user_id)")

    # ===== Automod : filtres moderation auto (TookBot+) =====
    c.execute('''CREATE TABLE IF NOT EXISTS automod_config (
        guild_id                   TEXT PRIMARY KEY,
        enabled                    INTEGER DEFAULT 0,
        banned_words_enabled       INTEGER DEFAULT 0,
        banned_words               TEXT DEFAULT '',     -- CSV
        discord_invites_enabled    INTEGER DEFAULT 0,
        mention_spam_enabled       INTEGER DEFAULT 0,
        mention_spam_threshold     INTEGER DEFAULT 5,
        raid_protection_enabled    INTEGER DEFAULT 0,
        raid_threshold             INTEGER DEFAULT 5,   -- joins/minute
        log_channel_id             TEXT,
        updated_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Cards collection (Mudae-like) =====
    # Catalogue global de cartes pop culture (Anime, Manga, Jeu video, Star
    # Wars, Hazbin Hotel, Amazing Digital Circus, etc.). Rarites :
    # common / rare / epic / legendary / mythic.
    c.execute('''CREATE TABLE IF NOT EXISTS cards (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        universe    TEXT,
        subtitle    TEXT,
        rarity      TEXT NOT NULL DEFAULT 'common',
        image_url   TEXT,
        description TEXT,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_cards_rarity ON cards(rarity)")
    # Migration : source_image_url (URL originale avant overlay)
    try:
        c.execute("ALTER TABLE cards ADD COLUMN source_image_url TEXT")
    except Exception:
        pass
    # Migration : not_tradeable flag sur user_cards
    try:
        c.execute("ALTER TABLE user_cards ADD COLUMN not_tradeable INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration : not_obtainable flag sur cards (cache du catalogue + roll)
    try:
        c.execute("ALTER TABLE cards ADD COLUMN not_obtainable INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration : flavor_subtitle (sous-titre affiche sous le nom)
    try:
        c.execute("ALTER TABLE cards ADD COLUMN flavor_subtitle TEXT")
    except Exception:
        pass
    # Migration : element (aleatoire par carte) pour le systeme de combat
    try:
        c.execute("ALTER TABLE cards ADD COLUMN element TEXT")
    except Exception:
        pass
    # Backfill : assigne un element aleatoire aux cartes qui n'en ont pas (one-shot)
    try:
        c.execute(
            "UPDATE cards SET element = CASE ABS(RANDOM()) % 5 "
            "WHEN 0 THEN 'eclat' WHEN 1 THEN 'abysse' WHEN 2 THEN 'fracture' "
            "WHEN 3 THEN 'vif' ELSE 'neant' END "
            "WHERE element IS NULL OR element = ''")
    except Exception:
        pass
    # Migration : winning_emoji sur card_event_log
    try:
        c.execute("ALTER TABLE card_event_log ADD COLUMN winning_emoji TEXT")
    except Exception:
        pass
    # Migration : claim_code (captcha texte)
    try:
        c.execute("ALTER TABLE card_event_log ADD COLUMN claim_code TEXT")
    except Exception:
        pass
    # Migration : role a ping sur drop event / boss (fans de la feature cartes)
    try:
        c.execute("ALTER TABLE guild_card_config ADD COLUMN ping_role_id TEXT")
    except Exception:
        pass
    # Migration : card_scale_pct sur borders (echelle de la carte dans le cadre)
    try:
        c.execute("ALTER TABLE borders ADD COLUMN card_scale_pct INTEGER DEFAULT 100")
    except Exception:
        pass
    # Migration : qty sur user_borders (bordures consommables, copies en stock)
    try:
        c.execute("ALTER TABLE user_borders ADD COLUMN qty INTEGER DEFAULT 1")
    except Exception:
        pass
    # Migration : fusion_level sur card_customizations (prestige etoiles 0-5)
    try:
        c.execute("ALTER TABLE card_customizations ADD COLUMN fusion_level INTEGER DEFAULT 0")
    except Exception:
        pass

    # Possessions : un user peut posseder plusieurs copies d'une meme carte.
    c.execute('''CREATE TABLE IF NOT EXISTS user_cards (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        card_id     INTEGER NOT NULL,
        claimed_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        favorite    INTEGER DEFAULT 0,
        FOREIGN KEY (card_id) REFERENCES cards(id)
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_user ON user_cards(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_card ON user_cards(card_id)")

    # Settings user : last roll + favorite + wishlist
    c.execute('''CREATE TABLE IF NOT EXISTS user_card_settings (
        user_id        TEXT PRIMARY KEY,
        last_roll_at   TEXT,
        favorite_card  INTEGER,
        updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Suggestions de cartes par communaute (owner approve via dashboard)
    c.execute('''CREATE TABLE IF NOT EXISTS card_suggestions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        suggester_id   TEXT NOT NULL,
        suggester_name TEXT,
        guild_id       TEXT,
        channel_id     TEXT,
        name           TEXT NOT NULL,
        universe       TEXT,
        subtitle       TEXT,
        image_url      TEXT,
        source_type    TEXT DEFAULT 'url',
        status         TEXT NOT NULL DEFAULT 'pending',
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        reviewed_at    TEXT,
        reviewer_id    TEXT,
        reject_reason  TEXT,
        created_card_id INTEGER
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_sugg_status ON card_suggestions(status)")
    # Migration : type de suggestion + target pour edits + proposed_rarity
    for col, ddl in (
        ("suggestion_type", "TEXT DEFAULT 'new'"),
        ("target_card_id", "INTEGER"),
        ("proposed_rarity", "TEXT"),
        ("original_image_url", "TEXT"),
    ):
        try:
            c.execute(f"ALTER TABLE card_suggestions ADD COLUMN {col} {ddl}")
        except Exception:
            pass

    # Cards Events : drops aleatoires de cartes dans un salon, premiere reaction wins
    c.execute('''CREATE TABLE IF NOT EXISTS card_event_config (
        guild_id           TEXT PRIMARY KEY,
        channel_id         TEXT,
        enabled            INTEGER DEFAULT 0,
        min_interval_min   INTEGER DEFAULT 300,
        max_interval_min   INTEGER DEFAULT 600,
        min_rarity         TEXT DEFAULT 'rare',
        next_drop_at       TEXT,
        updated_at         TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS card_event_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id      TEXT,
        channel_id    TEXT,
        message_id    TEXT,
        card_id       INTEGER,
        status        TEXT DEFAULT 'pending',
        claimer_id    TEXT,
        claimed_at    TEXT,
        dropped_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        triggered_by  TEXT DEFAULT 'auto'
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_event_msg ON card_event_log(message_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_event_status ON card_event_log(status)")

    # ===== Economie Essences : monnaie globale par user =====
    c.execute('''CREATE TABLE IF NOT EXISTS user_currency (
        user_id   TEXT PRIMARY KEY,
        essences  INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Bordures : catalogue + config placement (owner) =====
    c.execute('''CREATE TABLE IF NOT EXISTS borders (
        border_key  TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        filename    TEXT NOT NULL,
        offset_x    INTEGER DEFAULT 0,
        offset_y    INTEGER DEFAULT 0,
        scale_pct   INTEGER DEFAULT 100,
        enabled     INTEGER DEFAULT 1,
        sort_order  INTEGER DEFAULT 0,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Cosmetiques possedes par user (bordures pour l'instant)
    c.execute('''CREATE TABLE IF NOT EXISTS user_borders (
        user_id     TEXT NOT NULL,
        border_key  TEXT NOT NULL,
        acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, border_key)
    )''')

    # Bordure appliquee par un user sur une carte donnee
    c.execute('''CREATE TABLE IF NOT EXISTS card_customizations (
        user_id     TEXT NOT NULL,
        card_id     INTEGER NOT NULL,
        border_key  TEXT,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, card_id)
    )''')

    # ===== Combat de boss coopératif =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_boss (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    TEXT,
        channel_id  TEXT,
        message_id  TEXT,
        name        TEXT,
        element     TEXT,
        tier        INTEGER DEFAULT 1,
        max_hp      INTEGER,
        hp          INTEGER,
        atk         INTEGER,
        status      TEXT DEFAULT 'open',
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_boss_msg ON card_boss(message_id)")
    for _col, _ddl in (("start_at", "REAL"), ("image_url", "TEXT"), ("atk_spawn", "INTEGER"),
                       ("card_id", "INTEGER")):
        try:
            c.execute(f"ALTER TABLE card_boss ADD COLUMN {_col} {_ddl}")
        except Exception:
            pass
    c.execute('''CREATE TABLE IF NOT EXISTS card_boss_participant (
        boss_id        INTEGER NOT NULL,
        user_id        TEXT NOT NULL,
        name           TEXT,
        element        TEXT,
        max_hp         INTEGER,
        hp             INTEGER,
        atk            INTEGER,
        damage         INTEGER DEFAULT 0,
        last_attack    REAL DEFAULT 0,
        card_id        INTEGER,
        PRIMARY KEY (boss_id, user_id)
    )''')
    try:
        c.execute("ALTER TABLE card_boss_participant ADD COLUMN card_id INTEGER")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE card_boss_participant ADD COLUMN aptitude TEXT")
    except Exception:
        pass

    # ===== Roll charges (multi-roll/h) + bonus rolls offerts =====
    c.execute('''CREATE TABLE IF NOT EXISTS roll_events (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   TEXT NOT NULL,
        guild_id  TEXT NOT NULL,
        rolled_at REAL NOT NULL
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_roll_events_uig ON roll_events(user_id, guild_id, rolled_at)")
    c.execute('''CREATE TABLE IF NOT EXISTS roll_grant_state (
        user_id   TEXT PRIMARY KEY,
        consumed  INTEGER DEFAULT 0
    )''')
    try:
        c.execute("ALTER TABLE roll_grant_state ADD COLUMN credits INTEGER DEFAULT 0")
    except Exception:
        pass

    # ===== Card Wishlist : cartes desirees par user =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_wishlist (
        user_id   TEXT NOT NULL,
        card_id   INTEGER NOT NULL,
        added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, card_id)
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_card ON card_wishlist(card_id)")

    # ===== Card Profile : 3 cartes vedettes par user =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_profile (
        user_id   TEXT PRIMARY KEY,
        left_id   INTEGER,
        mid_id    INTEGER,
        right_id  INTEGER,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Card Shop : 6 slots configurables par owner =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_shop_slots (
        slot        INTEGER PRIMARY KEY,
        item_type   TEXT,
        item_ref    TEXT,
        price       INTEGER DEFAULT 0,
        label       TEXT,
        subtitle    TEXT,
        enabled     INTEGER DEFAULT 0,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Seed slots 1-6 si absent
    for _slot in range(1, 7):
        c.execute("INSERT OR IGNORE INTO card_shop_slots (slot, enabled) VALUES (?, 0)", (_slot,))
    # Seed bordures par defaut (5 fournies)
    _default_borders = [
        ("gold",  "Bordure Or",     "gold_border.png",  1),
        ("leaf",  "Bordure Feuille", "leaf_border.png",  2),
        ("frost", "Bordure Givre",   "frost_border.png", 3),
        ("hell",  "Bordure Enfer",   "hell_border.png",  4),
        ("void",  "Bordure Néant",   "void_border.png",  5),
    ]
    for _bk, _bn, _bf, _so in _default_borders:
        c.execute("INSERT OR IGNORE INTO borders (border_key, name, filename, sort_order) "
                   "VALUES (?, ?, ?, ?)", (_bk, _bn, _bf, _so))

    # Trades de cartes entre joueurs (multi-cartes, non-equivalent)
    c.execute('''CREATE TABLE IF NOT EXISTS card_trades (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id    TEXT NOT NULL,
        receiver_id  TEXT NOT NULL,
        guild_id     TEXT,
        channel_id   TEXT,
        message_id   TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        resolved_at  TEXT
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_trades_status ON card_trades(status)")
    c.execute('''CREATE TABLE IF NOT EXISTS card_trade_items (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id   INTEGER NOT NULL,
        side       TEXT NOT NULL,
        card_id    INTEGER NOT NULL,
        qty        INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (trade_id) REFERENCES card_trades(id) ON DELETE CASCADE
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_trade_items_trade ON card_trade_items(trade_id)")

    # Cooldown roll par (user, guild) - 1h par serveur
    c.execute('''CREATE TABLE IF NOT EXISTS user_guild_roll_cooldown (
        user_id        TEXT NOT NULL,
        guild_id       TEXT NOT NULL,
        last_roll_at   TEXT,
        PRIMARY KEY (user_id, guild_id)
    )''')

    # Config per-guild : salon obligatoire pour utiliser /roll et /collection
    c.execute('''CREATE TABLE IF NOT EXISTS guild_card_config (
        guild_id     TEXT PRIMARY KEY,
        channel_id   TEXT,
        enabled      INTEGER DEFAULT 1,
        updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Dashboard notifications : cloche header =====
    # Stockes par user_id. Type : 'automod_alert', 'entitlement', 'milestone',
    # 'trial_expire', 'raid_alert', 'system'.
    c.execute('''CREATE TABLE IF NOT EXISTS dashboard_notifications (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        guild_id    TEXT,
        type        TEXT NOT NULL,
        title       TEXT NOT NULL,
        message     TEXT,
        link_url    TEXT,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        read_at     TEXT
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_dash_notif_user ON dashboard_notifications(user_id, read_at)")

    # ===== Reminders : /remind <duree> <texte> =====
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        channel_id  TEXT NOT NULL,
        text        TEXT NOT NULL,
        due_at      TEXT NOT NULL,            -- ISO 'YYYY-MM-DD HH:MM:SS' UTC
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        fired       INTEGER DEFAULT 0
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at, fired)")

    # ===== Tempvoice : salons vocaux temporaires =====
    # Config par guild : lobby_channel_id = vocal "Creer ton salon" que les
    # users rejoignent pour declencher la creation ; category_id = categorie
    # ou poser le salon cree (null = meme categorie que le lobby).
    c.execute('''CREATE TABLE IF NOT EXISTS tempvoice_config (
        guild_id          TEXT PRIMARY KEY,
        lobby_channel_id  TEXT NOT NULL,
        category_id       TEXT,
        default_name      TEXT DEFAULT 'Vocal de {user}',
        updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Salons tempvoice actifs : track les channels crees pour qu'on sache qui
    # est owner + cleanup au boot si bot pas la lors du dernier "vide".
    c.execute('''CREATE TABLE IF NOT EXISTS tempvoice_active (
        channel_id   TEXT PRIMARY KEY,
        guild_id     TEXT NOT NULL,
        owner_id     TEXT NOT NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_tempvoice_guild ON tempvoice_active(guild_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tempvoice_owner ON tempvoice_active(owner_id)")

    # ===== Bot personalizer : profil bot custom par serveur =====
    c.execute('''CREATE TABLE IF NOT EXISTS guild_bot_profile (
        guild_id      TEXT PRIMARY KEY,
        nick          TEXT,
        avatar_url    TEXT,
        banner_url    TEXT,
        about_me      TEXT,
        status        TEXT,     -- online | idle | dnd | invisible
        activity_type TEXT,     -- playing | streaming | listening | watching | competing | custom
        activity_text TEXT,
        applied_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Migration : ajoute colonnes status/activity si table existait deja
    _bp_cols = _table_columns(c, "guild_bot_profile")
    for _col in ("status", "activity_type", "activity_text", "applied_by"):
        if _col not in _bp_cols:
            try:
                c.execute(f"ALTER TABLE guild_bot_profile ADD COLUMN {_col} TEXT")
            except Exception as _e:
                print(f"[db migration] add guild_bot_profile.{_col} : {_e}")

    # ===== Stripe subscriptions (TookBot+) =====
    c.execute('''CREATE TABLE IF NOT EXISTS stripe_subscriptions (
        discord_user_id        TEXT PRIMARY KEY,
        stripe_customer_id     TEXT,
        stripe_subscription_id TEXT,
        plan_months            INTEGER,
        status                 TEXT,    -- active | past_due | canceled | incomplete | etc
        current_period_end     INTEGER, -- epoch seconds
        created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_stripe_sub_customer ON stripe_subscriptions(stripe_customer_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_stripe_sub_status   ON stripe_subscriptions(status)")

    # ===== Uptime checks (page /status.html, barres heure par heure) =====
    c.execute('''CREATE TABLE IF NOT EXISTS service_uptime_check (
        component   TEXT NOT NULL,
        hour_bucket TEXT NOT NULL,
        checks      INTEGER DEFAULT 0,
        oks         INTEGER DEFAULT 0,
        last_ok     INTEGER DEFAULT 0,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (component, hour_bucket)
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_uptime_component_ts ON service_uptime_check(component, hour_bucket DESC)")

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

    # Roles d'un member (pour gating mod perms cote dashboard/bot)
    c.execute('''CREATE TABLE IF NOT EXISTS member_roles (
        guild_id TEXT NOT NULL,
        user_id  TEXT NOT NULL,
        role_id  TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id, role_id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_member_roles_user ON member_roles(guild_id, user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_member_roles_role ON member_roles(guild_id, role_id)')

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
    # + delivery (reaction|button) et style (embed|text) du message
    for col, ddl in [
        ("label",    "TEXT"),
        ("position", "INTEGER DEFAULT 0"),
        ("delivery", "TEXT DEFAULT 'reaction'"),
        ("style",    "TEXT DEFAULT 'embed'"),
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
    # Migration : nouvelles colonnes cosmetiques Pass + flag trial TookBot+
    for col, ddl in [
        ("pass_selected_title", "TEXT DEFAULT NULL"),
        ("pass_selected_emoji", "TEXT DEFAULT NULL"),
        ("trial_used_at",       "TEXT DEFAULT NULL"),  # ISO timestamp 1er trial
    ]:
        try:
            c.execute(f"ALTER TABLE premium_settings ADD COLUMN {col} {ddl}")
        except Exception:
            pass

    # Grants premium manuels (owner offre la feature gratuitement, comptes test, etc.).
    # feature='all' = pack complet, 'pass' = Battle Pass, 'guild_boost' = Guild Boost +, etc.
    c.execute('''CREATE TABLE IF NOT EXISTS premium_grants (
        user_id    TEXT NOT NULL,
        feature    TEXT NOT NULL DEFAULT 'all',
        granted_by TEXT,
        granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
        note       TEXT,
        PRIMARY KEY (user_id, feature)
    )''')
    # Migration : expires_at pour les grants temporaires (trial 7j etc.)
    try:
        c.execute("ALTER TABLE premium_grants ADD COLUMN expires_at TEXT DEFAULT NULL")
    except Exception:
        pass

    # Assignations Guild Boost + : un user assigne son achat/grant a une (ou
    # plusieurs si owner) guild. PK composite pour permettre l'owner d'avoir
    # plusieurs assignations ; pour les autres users on supprime les anciennes
    # rows avant insert (1 user = 1 assignation max).
    c.execute('''CREATE TABLE IF NOT EXISTS guild_boost (
        user_id     TEXT NOT NULL,
        guild_id    TEXT NOT NULL,
        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, guild_id)
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
    # Migration : ajoute les nouveaux sabres f2p SSR (obsidienne, celeste)
    # apparus apres le seed initial des DB existantes.
    ensure_extra_default_sabres()
    seed_pass_quest_templates_si_vide()
    # Migration : nettoie d'abord les sabres saisonniers casses (raretes invalides)
    cleanup_legacy_seasonal_sabres()
    # Re-seed sabres saisonniers + pass_rewards pour saisons existantes
    _migrate_pass_rewards_and_sabres()
    # Seed initial cards si table vide
    try:
        from services.cards_seed import seed_initial_cards
        seed_initial_cards()
    except Exception as e:
        print(f"[cards seed] erreur: {e!r}")
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


def ensure_extra_default_sabres():
    """Migration : pour les DB existantes qui datent d'avant l'ajout de nouveaux
    sabres f2p au pool SSR (obsidienne, celeste). INSERT OR IGNORE pour ne
    rien ecraser de modifie."""
    try:
        from duel.sabres import SABRES_DEFAULT
    except ImportError:
        return
    conn = get_db()
    c = conn.cursor()
    for sid in ("obsidienne", "celeste"):
        s = SABRES_DEFAULT.get(sid)
        if not s:
            continue
        sp = s.get("speciale", {})
        try:
            c.execute("""INSERT OR IGNORE INTO sabres
                (id, nom, emoji, rarete, prix, description,
                 speciale_nom, speciale_description, speciale_emoji, speciale_effet)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (s["id"], s["nom"], s.get("emoji", ""), s["rarete"],
                 int(s.get("prix", 0)), s.get("description", ""),
                 sp.get("nom", ""), sp.get("description", ""),
                 sp.get("emoji", ""), sp.get("effet", "")))
        except Exception as e:
            print(f"[ensure_extra_sabres] {sid} error: {e!r}")
    conn.commit()
    conn.close()


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


# ===== XP MESSAGES (per-guild) — refonte clean 2026-06 =====
# Formule canonique : xp_for_level(L) = L^E ; get_level(xp) = floor(xp^(1/E))
# E = exposant de la courbe, configurable par serveur via 'xp_curve_exponent'
# (defaut 5.0). Plage utile 2.0 a 8.0.

_DEFAULT_XP_EXPONENT = 5.0
_XP_EXP_MIN = 2.0
_XP_EXP_MAX = 8.0


def get_xp_curve_exponent(guild_id=None) -> float:
    """Lit l'exposant de courbe XP pour ce serveur. Clamp [2.0, 8.0]."""
    if guild_id is None:
        return _DEFAULT_XP_EXPONENT
    try:
        v = guild_setting_get(guild_id, "xp_curve_exponent", str(_DEFAULT_XP_EXPONENT))
        e = float(v)
        return max(_XP_EXP_MIN, min(_XP_EXP_MAX, e))
    except Exception:
        return _DEFAULT_XP_EXPONENT


def get_level(xp, guild_id=None) -> int:
    xp = int(xp or 0)
    if xp <= 0:
        return 0
    e = get_xp_curve_exponent(guild_id)
    return int(xp ** (1.0 / e))


def xp_for_level(level, guild_id=None) -> int:
    level = max(0, int(level or 0))
    e = get_xp_curve_exponent(guild_id)
    return int(round(level ** e))


def get_xp(guild_id, user_id) -> int:
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT xp FROM users WHERE guild_id = ? AND user_id = ?",
              (str(guild_id), str(user_id)))
    r = c.fetchone(); conn.close()
    return int(r["xp"]) if r else 0

def set_xp(guild_id, user_id, xp, username=None):
    """Upsert canonical : recalcule level depuis xp via courbe du serveur."""
    xp = max(0, int(xp or 0))
    level = get_level(xp, guild_id)
    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO users (guild_id, user_id, username, xp, level)
                 VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   xp       = excluded.xp,
                   level    = excluded.level,
                   username = COALESCE(excluded.username, users.username)""",
              (str(guild_id), str(user_id), username, xp, level))
    conn.commit(); conn.close()

def add_xp(guild_id, user_id, delta, username=None) -> tuple:
    """Increment XP. Retourne (new_xp, old_level, new_level, leveled_up)."""
    cur = get_xp(guild_id, user_id)
    old_level = get_level(cur, guild_id)
    new_xp = max(0, cur + int(delta or 0))
    set_xp(guild_id, user_id, new_xp, username=username)
    new_level = get_level(new_xp, guild_id)
    return (new_xp, old_level, new_level, new_level > old_level)

def get_progress(xp, guild_id=None) -> tuple:
    """Retourne (level, xp_in_level, xp_needed_in_level, percent_0_100)."""
    xp = int(xp or 0)
    level = get_level(xp, guild_id)
    start = xp_for_level(level, guild_id)
    end   = xp_for_level(level + 1, guild_id)
    span  = max(1, end - start)
    in_lvl = max(0, xp - start)
    pct = int(round(100 * in_lvl / span))
    return (level, in_lvl, end - start, max(0, min(100, pct)))

def get_leaderboard(guild_id, limit=10) -> list:
    conn = get_db(); c = conn.cursor()
    c.execute("""SELECT user_id, username, xp, level FROM users
                 WHERE guild_id = ? AND xp > 0
                 ORDER BY xp DESC LIMIT ?""",
              (str(guild_id), int(limit)))
    rows = [dict(r) for r in c.fetchall()]; conn.close()
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
def upsert_guild(guild_id, name, icon_url=None, member_count=0, owner_id=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO guilds (guild_id, name, icon_url, member_count, owner_id, last_seen_at, active)
                 VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                 ON CONFLICT(guild_id) DO UPDATE SET
                   name = excluded.name,
                   icon_url = excluded.icon_url,
                   member_count = excluded.member_count,
                   owner_id = COALESCE(excluded.owner_id, guilds.owner_id),
                   last_seen_at = CURRENT_TIMESTAMP,
                   active = 1""",
              (str(guild_id), name, icon_url, int(member_count or 0),
               str(owner_id) if owner_id else None))
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


def music_queue_move_to_front(guild_id, track_id):
    """Deplace une track a la position 1 (sera la prochaine jouee).
    Decale les autres positions de +1. Retourne True si OK."""
    conn = get_db()
    c = conn.cursor()
    # Verifie que la track existe et appartient a la guild
    row = c.execute(
        "SELECT id FROM music_queue WHERE guild_id = ? AND id = ?",
        (str(guild_id), int(track_id)),
    ).fetchone()
    if not row:
        conn.close()
        return False
    # Min position actuelle (sera notre nouvelle pos cible - 1)
    min_pos = c.execute(
        "SELECT MIN(position) AS p FROM music_queue WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchone()["p"] or 1
    # Pose la track a (min_pos - 1) -> sera sortie en 1er
    c.execute(
        "UPDATE music_queue SET position = ? WHERE id = ?",
        (min_pos - 1, int(track_id)),
    )
    conn.commit()
    conn.close()
    return True

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


# ===== Music plays telemetry =====
def stripe_subscription_upsert(discord_user_id, *, stripe_customer_id=None,
                                stripe_subscription_id=None, plan_months=None,
                                status=None, current_period_end=None):
    """UPSERT par discord_user_id. Champs None ignores (preserve existing)."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO stripe_subscriptions (discord_user_id) VALUES (?)",
              (str(discord_user_id),))
    fields, values = [], []
    if stripe_customer_id     is not None: fields.append("stripe_customer_id = ?");     values.append(stripe_customer_id)
    if stripe_subscription_id is not None: fields.append("stripe_subscription_id = ?"); values.append(stripe_subscription_id)
    if plan_months            is not None: fields.append("plan_months = ?");            values.append(int(plan_months))
    if status                 is not None: fields.append("status = ?");                 values.append(status)
    if current_period_end     is not None: fields.append("current_period_end = ?");     values.append(int(current_period_end))
    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        c.execute(f"UPDATE stripe_subscriptions SET {', '.join(fields)} WHERE discord_user_id = ?",
                  (*values, str(discord_user_id)))
    conn.commit(); conn.close()


def stripe_subscription_get(discord_user_id):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM stripe_subscriptions WHERE discord_user_id = ?", (str(discord_user_id),))
    r = c.fetchone(); conn.close()
    return dict(r) if r else None


def stripe_subscription_get_by_customer(stripe_customer_id):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM stripe_subscriptions WHERE stripe_customer_id = ?", (stripe_customer_id,))
    r = c.fetchone(); conn.close()
    return dict(r) if r else None


def stripe_subscription_get_by_subscription(stripe_subscription_id):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM stripe_subscriptions WHERE stripe_subscription_id = ?", (stripe_subscription_id,))
    r = c.fetchone(); conn.close()
    return dict(r) if r else None


# ===== Automod helpers =====
def automod_config_get(guild_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM automod_config WHERE guild_id = ?", (str(guild_id),)).fetchone()
    conn.close()
    return dict(r) if r else {
        "guild_id": str(guild_id),
        "enabled": 0,
        "banned_words_enabled": 0, "banned_words": "",
        "discord_invites_enabled": 0,
        "mention_spam_enabled": 0, "mention_spam_threshold": 5,
        "raid_protection_enabled": 0, "raid_threshold": 5,
        "log_channel_id": None,
    }


def automod_config_set(guild_id, **fields):
    """UPSERT config automod. Accepte un sous-ensemble des champs."""
    allowed = {
        "enabled", "banned_words_enabled", "banned_words",
        "discord_invites_enabled", "mention_spam_enabled", "mention_spam_threshold",
        "raid_protection_enabled", "raid_threshold", "log_channel_id",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO automod_config (guild_id) VALUES (?)", (str(guild_id),))
    sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = CURRENT_TIMESTAMP"
    c.execute(f"UPDATE automod_config SET {sets} WHERE guild_id = ?",
              (*fields.values(), str(guild_id)))
    conn.commit(); conn.close()


# ===== Cards collection helpers =====
import random as _rd_cards


CARD_RARITY_WEIGHTS = {
    "common":    50,
    "rare":      30,
    "epic":      15,
    "legendary": 4,
    "mythic":    1,
    "secret":    0,   # poids 0 = jamais roll auto, owner-give uniquement
}

# Elements (combat). Cycle pentagone : chaque element bat les 2 SUIVANTS,
# perd contre les 2 PRECEDENTS. Ordre = eclat>abysse>fracture>vif>neant>eclat.
CARD_ELEMENTS = ["eclat", "abysse", "fracture", "vif", "neant"]
CARD_ELEMENT_LABELS = {
    "eclat": "Éclat", "abysse": "Abysse", "fracture": "Fracture",
    "vif": "Vif", "neant": "Néant",
}
# Emoji unicode fallback (en attendant les emojis custom du support)
CARD_ELEMENT_EMOJI = {
    "eclat": "🔆", "abysse": "🌊", "fracture": "⛓", "vif": "🩸", "neant": "🕳",
}
# Nom de l'emoji custom support (cherche par nom, sinon fallback unicode)
CARD_ELEMENT_EMOJI_NAME = {
    "eclat": "elem_eclat", "abysse": "elem_abysse", "fracture": "elem_fracture",
    "vif": "elem_vif", "neant": "elem_neant",
}


def element_matchup(attacker: str, defender: str) -> float:
    """Cercle de faiblesse : chaque element bat le SUIVANT et perd contre le
    PRECEDENT. +25% si avantage, -20% si desavantage, neutre sinon.
    Cycle : eclat>abysse>fracture>vif>neant>eclat."""
    try:
        ia = CARD_ELEMENTS.index(attacker)
        idd = CARD_ELEMENTS.index(defender)
    except (ValueError, AttributeError):
        return 1.0
    n = len(CARD_ELEMENTS)
    diff = (idd - ia) % n   # 1 = attacker bat defender ; n-1 = desavantage
    if diff == 1:
        return 1.25
    if diff == n - 1:
        return 0.8
    return 1.0


def random_element() -> str:
    return _rd_cards.choice(CARD_ELEMENTS) if "_rd_cards" in globals() else __import__("random").choice(CARD_ELEMENTS)


def card_add(name, universe=None, subtitle=None, rarity="common",
              image_url=None, description=None, flavor_subtitle=None, element=None):
    conn = get_db(); c = conn.cursor()
    elem = element if element in CARD_ELEMENTS else random_element()
    c.execute('''INSERT INTO cards (name, universe, subtitle, rarity, image_url, description, flavor_subtitle, element)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (name, universe, subtitle, rarity, image_url, description, flavor_subtitle, elem))
    cid = c.lastrowid
    conn.commit(); conn.close()
    return cid


def card_list_all(limit=1000, rarity=None, search=None, offset=0):
    conn = get_db(); c = conn.cursor()
    where = ["1=1"]
    params: list = []
    if rarity:
        where.append("rarity = ?"); params.append(rarity)
    if search:
        where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
        like = f"%{search.lower()}%"
        params += [like, like, like]
    params.append(int(limit))
    params.append(int(offset))
    rows = c.execute(
        f"SELECT * FROM cards WHERE {' AND '.join(where)} "
        f"ORDER BY id ASC LIMIT ? OFFSET ?", params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_count_filtered(rarity=None, search=None):
    """Total rows matching same filtres que card_list_all."""
    conn = get_db(); c = conn.cursor()
    where = ["1=1"]
    params: list = []
    if rarity:
        where.append("rarity = ?"); params.append(rarity)
    if search:
        where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
        like = f"%{search.lower()}%"
        params += [like, like, like]
    n = c.execute(
        f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}", params,
    ).fetchone()["n"]
    conn.close()
    return int(n)


def card_get(card_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM cards WHERE id = ?", (int(card_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_get_by_name(name):
    """Case insensitive."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM cards WHERE LOWER(name) = LOWER(?)",
                  (name,)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_delete(card_id):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM cards WHERE id = ?", (int(card_id),))
    deleted = c.rowcount > 0
    conn.commit(); conn.close()
    return deleted


def card_count_total():
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
    conn.close()
    return int(n)


_ROLL_WEIGHTS = {k: v for k, v in CARD_RARITY_WEIGHTS.items() if v > 0}


def card_roll_random(universe: str | None = None):
    """Pioche une carte selon les poids de rarete.
    Si universe fourni : filtre uniquement cette categorie.
    Retourne None si la table cards (ou la categorie) est vide."""
    rarity = _rd_cards.choices(
        list(_ROLL_WEIGHTS.keys()),
        weights=list(_ROLL_WEIGHTS.values()),
        k=1,
    )[0]
    conn = get_db(); c = conn.cursor()
    if universe:
        rows = c.execute("SELECT * FROM cards WHERE rarity = ? AND universe = ? "
                          "AND COALESCE(not_obtainable, 0) = 0",
                          (rarity, universe)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM cards WHERE universe = ? "
                              "AND COALESCE(not_obtainable, 0) = 0 "
                              "ORDER BY RANDOM() LIMIT 1",
                              (universe,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM cards WHERE rarity = ? "
                          "AND COALESCE(not_obtainable, 0) = 0",
                          (rarity,)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM cards WHERE COALESCE(not_obtainable, 0) = 0 "
                              "ORDER BY RANDOM() LIMIT 1").fetchall()
    conn.close()
    if not rows:
        return None
    return dict(_rd_cards.choice(rows))


def user_card_add(user_id, card_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)",
              (str(user_id), int(card_id)))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def user_card_add_with_flag(user_id, card_id, not_tradeable=False):
    """Comme user_card_add mais avec flag not_tradeable."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_cards (user_id, card_id, not_tradeable) VALUES (?, ?, ?)",
              (str(user_id), int(card_id), 1 if not_tradeable else 0))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def user_card_list(user_id, rarity=None, categorie=None):
    """Toutes les copies du user, jointes a la carte. ORDER BY rarete desc.
    categorie : filtre optionnel matchant l'univers OU l'origine (subtitle), insensible casse."""
    conn = get_db(); c = conn.cursor()
    where = "uc.user_id = ?"
    params = [str(user_id)]
    if rarity:
        where += " AND c.rarity = ?"
        params.append(rarity)
    if categorie:
        where += " AND (LOWER(c.universe) = LOWER(?) OR LOWER(c.subtitle) = LOWER(?))"
        params.append(categorie); params.append(categorie)
    # Ordre par rarite (mythic d'abord), puis par card name
    rarity_order = ("CASE c.rarity "
                    "WHEN 'mythic' THEN 0 "
                    "WHEN 'legendary' THEN 1 "
                    "WHEN 'epic' THEN 2 "
                    "WHEN 'rare' THEN 3 "
                    "WHEN 'common' THEN 4 ELSE 5 END")
    rows = c.execute(
        f"SELECT uc.*, c.name, c.universe, c.subtitle, c.rarity, c.image_url, c.element "
        f"FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        f"WHERE {where} ORDER BY {rarity_order} ASC, c.name ASC", params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_owners_count(card_id):
    """Nombre de users distincts possedant cette carte."""
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(DISTINCT user_id) AS n FROM user_cards WHERE card_id = ?",
                   (int(card_id),)).fetchone()["n"]
    conn.close()
    return int(n)


def card_owners_list(card_id, limit=50):
    """Liste des possesseurs avec count. Ordre desc par count."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT user_id, COUNT(*) AS qty FROM user_cards "
        "WHERE card_id = ? GROUP BY user_id ORDER BY qty DESC LIMIT ?",
        (int(card_id), int(limit))).fetchall()
    conn.close()
    return [{"user_id": r["user_id"], "qty": r["qty"]} for r in rows]


def user_card_count(user_id):
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ?",
                  (str(user_id),)).fetchone()["n"]
    conn.close()
    return int(n)


def card_suggestion_add(suggester_id, suggester_name, guild_id, channel_id,
                          name, universe=None, subtitle=None,
                          image_url=None, source_type="url",
                          suggestion_type="new", target_card_id=None,
                          proposed_rarity=None, original_image_url=None):
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO card_suggestions
                 (suggester_id, suggester_name, guild_id, channel_id,
                  name, universe, subtitle, image_url, source_type,
                  suggestion_type, target_card_id, proposed_rarity, original_image_url)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (str(suggester_id), suggester_name,
                str(guild_id) if guild_id else None,
                str(channel_id) if channel_id else None,
                name, universe, subtitle, image_url, source_type,
                suggestion_type,
                int(target_card_id) if target_card_id else None,
                proposed_rarity, original_image_url))
    sid = c.lastrowid
    conn.commit(); conn.close()
    return sid


def card_suggestion_list(status=None, limit=200):
    conn = get_db(); c = conn.cursor()
    if status:
        rows = c.execute(
            "SELECT * FROM card_suggestions WHERE status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status, int(limit))).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM card_suggestions ORDER BY created_at DESC LIMIT ?",
            (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_suggestion_get(sid):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_suggestions WHERE id = ?",
                  (int(sid),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_suggestion_review(sid, status, reviewer_id, reason=None, created_card_id=None):
    conn = get_db(); c = conn.cursor()
    c.execute('''UPDATE card_suggestions SET
                 status = ?, reviewer_id = ?, reject_reason = ?,
                 created_card_id = ?, reviewed_at = CURRENT_TIMESTAMP
                 WHERE id = ?''',
              (status, str(reviewer_id), reason, created_card_id, int(sid)))
    conn.commit(); conn.close()


def card_suggestion_count_pending():
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM card_suggestions WHERE status = 'pending'").fetchone()["n"]
    conn.close()
    return int(n)


def user_card_count_owned(user_id, card_id, only_tradeable: bool = False):
    """Combien de copies user possede de cette carte.
    only_tradeable=True : exclut les not_tradeable (pour verif trade)."""
    conn = get_db(); c = conn.cursor()
    if only_tradeable:
        n = c.execute("SELECT COUNT(*) AS n FROM user_cards "
                      "WHERE user_id = ? AND card_id = ? "
                      "AND COALESCE(not_tradeable, 0) = 0",
                      (str(user_id), int(card_id))).fetchone()["n"]
    else:
        n = c.execute("SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ? AND card_id = ?",
                       (str(user_id), int(card_id))).fetchone()["n"]
    conn.close()
    return int(n)


def user_card_transfer_one(from_user, to_user, card_id):
    """Transfert UNE copie d'une carte tradeable. Skip les not_tradeable.
    Retourne True si OK, False si from_user n'en a pas de tradeable."""
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT id FROM user_cards "
                     "WHERE user_id = ? AND card_id = ? "
                     "AND COALESCE(not_tradeable, 0) = 0 LIMIT 1",
                     (str(from_user), int(card_id))).fetchone()
    if not row:
        conn.close()
        return False
    c.execute("UPDATE user_cards SET user_id = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?",
              (str(to_user), int(row["id"])))
    conn.commit(); conn.close()
    return True


def card_event_config_get(guild_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_event_config WHERE guild_id = ?",
                  (str(guild_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_event_config_set(guild_id, **kwargs):
    """Upsert config. Accepte channel_id, enabled, min_interval_min,
    max_interval_min, min_rarity, next_drop_at."""
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO card_event_config (guild_id) VALUES (?)
                 ON CONFLICT(guild_id) DO NOTHING''', (str(guild_id),))
    allowed = {"channel_id", "enabled", "min_interval_min", "max_interval_min",
                "min_rarity", "next_drop_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if fields:
        fields["updated_at"] = None  # CURRENT_TIMESTAMP via SQL
        sets = ", ".join(f"{k} = ?" for k in fields if k != "updated_at")
        sets += ", updated_at = CURRENT_TIMESTAMP"
        vals = [v for k, v in fields.items() if k != "updated_at"]
        vals.append(str(guild_id))
        c.execute(f"UPDATE card_event_config SET {sets} WHERE guild_id = ?", vals)
    conn.commit(); conn.close()


def card_event_config_all_enabled():
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM card_event_config WHERE enabled = 1 "
                     "AND channel_id IS NOT NULL").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_event_log_create(guild_id, channel_id, card_id, message_id=None,
                            triggered_by="auto", claim_code=None):
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO card_event_log
                 (guild_id, channel_id, card_id, message_id, triggered_by, claim_code)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (str(guild_id), str(channel_id), int(card_id),
                str(message_id) if message_id else None, triggered_by, claim_code))
    eid = c.lastrowid
    conn.commit(); conn.close()
    return eid


def card_event_log_delete(event_id):
    """Supprime un event (ex : echec d'envoi du message -> pas de ghost)."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM card_event_log WHERE id = ?", (int(event_id),))
    conn.commit(); conn.close()


def card_event_log_update_message(event_id, message_id, winning_emoji=None,
                                    claim_code=None):
    conn = get_db(); c = conn.cursor()
    sets = ["message_id = ?"]
    vals = [str(message_id)]
    if winning_emoji is not None:
        sets.append("winning_emoji = ?")
        vals.append(winning_emoji)
    if claim_code is not None:
        sets.append("claim_code = ?")
        vals.append(claim_code)
    vals.append(int(event_id))
    c.execute(f"UPDATE card_event_log SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit(); conn.close()


def card_event_log_get_pending_in_channel(channel_id):
    """Liste events pending dans un salon (pour captcha matching)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM card_event_log WHERE channel_id = ? AND status = 'pending' "
        "AND claim_code IS NOT NULL ORDER BY id DESC LIMIT 10",
        (str(channel_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_event_log_get_by_message(message_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_event_log WHERE message_id = ? AND status = 'pending'",
                  (str(message_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_event_log_claim(event_id, user_id):
    """Atomic claim. Retourne True si OK, False si deja claimed."""
    conn = get_db(); c = conn.cursor()
    c.execute('''UPDATE card_event_log
                 SET status = 'claimed', claimer_id = ?, claimed_at = CURRENT_TIMESTAMP
                 WHERE id = ? AND status = 'pending' ''',
              (str(user_id), int(event_id)))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


def card_event_log_recent(limit=50):
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT el.*, c.name AS card_name, c.rarity AS card_rarity, c.image_url "
        "FROM card_event_log el LEFT JOIN cards c ON c.id = el.card_id "
        "ORDER BY el.dropped_at DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== ECONOMIE ESSENCES =====
# Essences gagnees par /roll selon rarete carte obtenue. Doublon = x2.
ESSENCE_REWARDS = {
    "common":    12,
    "rare":      28,
    "epic":      65,
    "legendary": 220,
    "mythic":    650,
    "secret":    1000,
}


def currency_get(user_id) -> int:
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT essences FROM user_currency WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    conn.close()
    return int(r["essences"]) if r else 0


def currency_add(user_id, amount: int) -> int:
    """Ajoute (ou retire si negatif) des essences. Retourne nouveau solde."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_currency (user_id, essences) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET essences = essences + excluded.essences, "
              "updated_at = CURRENT_TIMESTAMP",
              (str(user_id), int(amount)))
    r = c.execute("SELECT essences FROM user_currency WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    conn.commit(); conn.close()
    return int(r["essences"]) if r else 0


def currency_spend(user_id, amount: int) -> bool:
    """Debite si solde suffisant. Retourne True si OK, False sinon. Atomic."""
    amount = int(amount)
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_currency SET essences = essences - ?, updated_at = CURRENT_TIMESTAMP "
              "WHERE user_id = ? AND essences >= ?",
              (amount, str(user_id), amount))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


# ===== ROUE DE LA CHANCE QUOTIDIENNE =====
# Recompenses : bonus % d'essences pour la journee (essence_bonus_daily) OU
# rolls offerts (via roll_give_user). 1 spin / jour / utilisateur.
def _today_str():
    """Date du jour en heure FRANCAISE (reset roue a minuit Europe/Paris)."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    except Exception:
        return _dt.date.today().isoformat()


def _ensure_wheel_tables():
    conn = get_db(); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS wheel_daily (
        user_id      TEXT NOT NULL,
        day          TEXT NOT NULL,
        reward_type  TEXT,
        reward_value INTEGER,
        spun_at      TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, day)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS essence_bonus_daily (
        user_id TEXT NOT NULL,
        day     TEXT NOT NULL,
        pct     INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_roll_claims (
        user_id    TEXT NOT NULL,
        day        TEXT NOT NULL,
        claimed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, day)
    )''')
    # Journal des gains de la roue (append-only, alimente le feed "en direct")
    c.execute('''CREATE TABLE IF NOT EXISTS wheel_wins (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      TEXT NOT NULL,
        reward_type  TEXT,
        reward_value INTEGER,
        won_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit(); conn.close()


def wheel_win_log(user_id, reward_type, reward_value):
    """Ajoute un gain au journal (feed en direct)."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO wheel_wins (user_id, reward_type, reward_value) VALUES (?, ?, ?)",
              (str(user_id), reward_type, int(reward_value)))
    conn.commit(); conn.close()


def wheel_wins_recent(limit=40):
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT user_id, reward_type, reward_value, won_at "
                     "FROM wheel_wins ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wheel_wins_reset() -> int:
    """Owner : vide le journal des gains de la roue. Retourne le nb supprime."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM wheel_wins")
    n = c.rowcount
    conn.commit(); conn.close()
    return int(n)


def daily_roll_claimed_today(user_id) -> bool:
    """True si le user a deja recupere son roll quotidien gratuit aujourd'hui (FR)."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM daily_roll_claims WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return r is not None


def daily_roll_grant(user_id) -> bool:
    """Octroie 1 roll gratuit du jour. False si deja recupere aujourd'hui."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO daily_roll_claims (user_id, day) VALUES (?, ?)",
                  (str(user_id), _today_str()))
        conn.commit(); ok = True
    except Exception:
        ok = False
    conn.close()
    if ok:
        roll_give_user(user_id, 1)
    return ok


def wheel_claim_today(user_id):
    """Retourne le spin du jour (dict) ou None si pas encore tourne."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM wheel_daily WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return dict(r) if r else None


def wheel_record(user_id, reward_type, reward_value) -> bool:
    """Enregistre le spin du jour. False si deja tourne aujourd'hui."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO wheel_daily (user_id, day, reward_type, reward_value) "
                  "VALUES (?, ?, ?, ?)",
                  (str(user_id), _today_str(), reward_type, int(reward_value)))
        conn.commit(); ok = True
    except Exception:
        ok = False
    conn.close()
    return ok


def wheel_reset_all() -> int:
    """Owner : reset la roue du jour pour TOUT LE MONDE (chacun peut re-tourner).
    Retourne le nb de spins effaces."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM wheel_daily WHERE day = ?", (_today_str(),))
    n = c.rowcount
    conn.commit(); conn.close()
    return int(n)


def essence_bonus_get(user_id) -> int:
    """Bonus % d'essences actif aujourd'hui pour ce user (0 si aucun)."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT pct FROM essence_bonus_daily WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return int(r["pct"]) if r else 0


def essence_bonus_set(user_id, pct):
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO essence_bonus_daily (user_id, day, pct) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, day) DO UPDATE SET pct = excluded.pct",
              (str(user_id), _today_str(), int(pct)))
    conn.commit(); conn.close()


def essence_reward_add(user_id, base_amount) -> int:
    """Ajoute des essences en appliquant le bonus % du jour. Retourne le gain reel."""
    base = int(base_amount)
    pct = essence_bonus_get(user_id)
    gain = base + (base * pct) // 100
    currency_add(user_id, gain)
    return gain


# ===== BORDURES =====
def borders_list(enabled_only=False):
    conn = get_db(); c = conn.cursor()
    where = "WHERE enabled = 1" if enabled_only else ""
    rows = c.execute(f"SELECT * FROM borders {where} ORDER BY sort_order, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def border_get(border_key):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM borders WHERE border_key = ?", (border_key,)).fetchone()
    conn.close()
    return dict(r) if r else None


def border_set_config(border_key, offset_x=None, offset_y=None, scale_pct=None,
                       enabled=None, name=None, card_scale_pct=None):
    conn = get_db(); c = conn.cursor()
    sets, vals = [], []
    for col, v in (("offset_x", offset_x), ("offset_y", offset_y),
                    ("scale_pct", scale_pct), ("card_scale_pct", card_scale_pct),
                    ("enabled", enabled), ("name", name)):
        if v is not None:
            sets.append(f"{col} = ?")
            vals.append(int(v) if col != "name" else v)
    if not sets:
        conn.close(); return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    vals.append(border_key)
    c.execute(f"UPDATE borders SET {', '.join(sets)} WHERE border_key = ?", vals)
    conn.commit(); conn.close()
    return True


def user_border_add(user_id, border_key, qty=1):
    """Ajoute qty copies en inventaire (incremente si deja present)."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_borders (user_id, border_key, qty) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, border_key) DO UPDATE SET qty = qty + excluded.qty",
              (str(user_id), border_key, int(qty)))
    conn.commit(); conn.close()


def user_border_qty(user_id, border_key) -> int:
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT qty FROM user_borders WHERE user_id = ? AND border_key = ?",
                  (str(user_id), border_key)).fetchone()
    conn.close()
    return int(r["qty"]) if r and r["qty"] is not None else 0


def user_border_has(user_id, border_key) -> bool:
    """True si au moins 1 copie en stock."""
    return user_border_qty(user_id, border_key) > 0


def user_border_consume(user_id, border_key) -> bool:
    """Retire 1 copie du stock. True si OK, False si stock vide. Atomic."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_borders SET qty = qty - 1 "
              "WHERE user_id = ? AND border_key = ? AND qty >= 1",
              (str(user_id), border_key))
    ok = c.rowcount > 0
    if ok:
        c.execute("DELETE FROM user_borders WHERE user_id = ? AND border_key = ? AND qty <= 0",
                  (str(user_id), border_key))
    conn.commit(); conn.close()
    return ok


def user_border_remove(user_id, border_key, qty=1):
    """Owner : retire qty copies (ou tout si qty None)."""
    conn = get_db(); c = conn.cursor()
    if qty is None:
        c.execute("DELETE FROM user_borders WHERE user_id = ? AND border_key = ?",
                  (str(user_id), border_key))
    else:
        c.execute("UPDATE user_borders SET qty = qty - ? WHERE user_id = ? AND border_key = ?",
                  (int(qty), str(user_id), border_key))
        c.execute("DELETE FROM user_borders WHERE user_id = ? AND border_key = ? AND qty <= 0",
                  (str(user_id), border_key))
    conn.commit(); conn.close()


def user_borders_list(user_id):
    """Inventaire : bordures en stock (qty > 0)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT ub.border_key, ub.qty, ub.acquired_at, b.name, b.filename "
        "FROM user_borders ub JOIN borders b ON b.border_key = ub.border_key "
        "WHERE ub.user_id = ? AND ub.qty > 0 ORDER BY ub.acquired_at DESC",
        (str(user_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_card_customizations_map(user_id):
    """Retourne {card_id: border_key} des cartes customisees par le user."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT card_id, border_key FROM card_customizations "
        "WHERE user_id = ? AND border_key IS NOT NULL", (str(user_id),)).fetchall()
    conn.close()
    return {int(r["card_id"]): r["border_key"] for r in rows}


# ===== FUSION (prestige etoiles) =====
def card_fusion_get(user_id, card_id) -> int:
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT fusion_level FROM card_customizations WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id))).fetchone()
    conn.close()
    return int(r["fusion_level"]) if r and r["fusion_level"] else 0


def card_fusion_set(user_id, card_id, level):
    """Upsert le niveau de fusion (etoiles) d'une carte pour un user."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_customizations (user_id, card_id, fusion_level, updated_at) "
              "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
              "ON CONFLICT(user_id, card_id) DO UPDATE SET fusion_level = excluded.fusion_level, "
              "updated_at = CURRENT_TIMESTAMP",
              (str(user_id), int(card_id), int(level)))
    conn.commit(); conn.close()


def user_card_fusion_map(user_id):
    """Retourne {card_id: fusion_level} pour les cartes ayant >=1 etoile."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT card_id, fusion_level FROM card_customizations "
        "WHERE user_id = ? AND fusion_level > 0", (str(user_id),)).fetchall()
    conn.close()
    return {int(r["card_id"]): int(r["fusion_level"]) for r in rows}


def user_card_set_not_tradeable(user_id, card_id, value=1):
    """Marque toutes les copies d'une carte d'un user comme (non) tradeable."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_cards SET not_tradeable = ? WHERE user_id = ? AND card_id = ?",
              (1 if value else 0, str(user_id), int(card_id)))
    conn.commit(); conn.close()


def user_card_lock_one(user_id, card_id):
    """Verrouille UNE seule copie (la carte qui porte les etoiles). Les doublons
    en trop restent echangeables/recyclables. No-op si une copie est deja verrouillee."""
    conn = get_db(); c = conn.cursor()
    already = c.execute(
        "SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ? AND card_id = ? "
        "AND COALESCE(not_tradeable,0) = 1", (str(user_id), int(card_id))).fetchone()["n"]
    if int(already) == 0:
        row = c.execute(
            "SELECT id FROM user_cards WHERE user_id = ? AND card_id = ? "
            "ORDER BY id ASC LIMIT 1", (str(user_id), int(card_id))).fetchone()
        if row:
            c.execute("UPDATE user_cards SET not_tradeable = 1 WHERE id = ?", (row["id"],))
    conn.commit(); conn.close()


def user_card_remove_copies(user_id, card_id, n) -> int:
    """Supprime n copies (rows) d'une carte pour un user. Retourne nb reellement
    supprime. Ne verifie pas le 'keep' (a faire cote appelant)."""
    if n <= 0:
        return 0
    conn = get_db(); c = conn.cursor()
    ids = c.execute("SELECT id FROM user_cards WHERE user_id = ? AND card_id = ? "
                    "ORDER BY id ASC LIMIT ?",
                    (str(user_id), int(card_id), int(n))).fetchall()
    id_list = [r["id"] for r in ids]
    if id_list:
        ph = ",".join("?" * len(id_list))
        c.execute(f"DELETE FROM user_cards WHERE id IN ({ph})", id_list)
    conn.commit(); conn.close()
    return len(id_list)


def card_profile_get(user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT left_id, mid_id, right_id FROM card_profile WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_profile_set(user_id, left_id, mid_id, right_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_profile (user_id, left_id, mid_id, right_id, updated_at) "
              "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
              "ON CONFLICT(user_id) DO UPDATE SET left_id = excluded.left_id, "
              "mid_id = excluded.mid_id, right_id = excluded.right_id, "
              "updated_at = CURRENT_TIMESTAMP",
              (str(user_id), int(left_id), int(mid_id), int(right_id)))
    conn.commit(); conn.close()


def user_card_rarity_breakdown(user_id):
    """Retourne {rarity: count_de_copies} pour un user."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT c.rarity AS rarity, COUNT(*) AS n FROM user_cards uc "
        "JOIN cards c ON c.id = uc.card_id WHERE uc.user_id = ? GROUP BY c.rarity",
        (str(user_id),)).fetchall()
    conn.close()
    return {r["rarity"]: int(r["n"]) for r in rows}


def all_card_origins():
    """Toutes les origines (subtitle) du catalogue + nb de cartes (uniques)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT subtitle AS origin, COUNT(*) AS n FROM cards "
        "WHERE subtitle IS NOT NULL AND subtitle != '' "
        "GROUP BY subtitle ORDER BY subtitle COLLATE NOCASE").fetchall()
    conn.close()
    return [(r["origin"], int(r["n"])) for r in rows]


def user_collection_origins(user_id):
    """Origines (subtitle) presentes dans la collection d'un user + nb cartes uniques."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT c.subtitle AS origin, COUNT(DISTINCT uc.card_id) AS n "
        "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        "WHERE uc.user_id = ? AND c.subtitle IS NOT NULL AND c.subtitle != '' "
        "GROUP BY c.subtitle ORDER BY c.subtitle COLLATE NOCASE",
        (str(user_id),)).fetchall()
    conn.close()
    return [(r["origin"], int(r["n"])) for r in rows]


def user_unique_rarity_breakdown(user_id):
    """Retourne {rarity: nb_cartes_uniques} (distinctes) pour un user."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT c.rarity AS rarity, COUNT(DISTINCT uc.card_id) AS n FROM user_cards uc "
        "JOIN cards c ON c.id = uc.card_id WHERE uc.user_id = ? GROUP BY c.rarity",
        (str(user_id),)).fetchall()
    conn.close()
    return {r["rarity"]: int(r["n"]) for r in rows}


# ===== STATS DE COMBAT (joueur) : derivees des cartes uniques pondérées =====
PLAYER_HP_BASE = 50
PLAYER_ATK_BASE = 25
# Gros chiffres = plus impressionnant. Cible : carte rare ~500 PV / 270 ATK.
PLAYER_HP_WEIGHTS = {
    "common": 300, "rare": 500, "epic": 900, "legendary": 1800, "mythic": 4000, "secret": 6000,
}
PLAYER_ATK_WEIGHTS = {
    "common": 150, "rare": 270, "epic": 500, "legendary": 1000, "mythic": 2200, "secret": 3500,
}


# Soft cap collection : au-dela de SOFT_T cartes possedees (total), chaque carte
# supplementaire ne compte que pour SOFT_DECAY. Lisse l'ecart entre joueurs et
# evite l'inflation absurde des nombres. (N'affecte PAS la difficulte car le boss
# scale sur cette meme valeur — voir team_scaled_boss_stats.)
COLLECTION_SOFT_T = 3000
COLLECTION_SOFT_DECAY = 0.7


def _collection_soft_factor(total_cards):
    n = int(total_cards or 0)
    if n <= COLLECTION_SOFT_T:
        return 1.0
    eff = COLLECTION_SOFT_T + (n - COLLECTION_SOFT_T) * COLLECTION_SOFT_DECAY
    return eff / n


# Bonus de fusion (% sur PV+ATK de base). Lineaire 1%/etoile jusqu'a 15, puis
# courbe logarithmique a rendement decroissant, SANS cap (pente continue a 15).
FUSION_CURVE_KNEE = 15
FUSION_CURVE_A = 30.0


def fusion_bonus_pct(stars) -> float:
    """Retourne le bonus de fusion en POURCENT (ex 23.4) pour un total d'etoiles."""
    import math
    s = max(0, int(stars))
    if s <= FUSION_CURVE_KNEE:
        return float(s)
    return FUSION_CURVE_KNEE + FUSION_CURVE_A * math.log(
        1 + (s - FUSION_CURVE_KNEE) / FUSION_CURVE_A)


def compute_player_combat_stats(user_id):
    """PV + ATK de BASE d'un joueur selon ses cartes UNIQUES pondérées par rareté,
    + bonus des etoiles de fusion (courbe fusion_bonus_pct, sans cap).
    C'est le socle 'collection' (recompense le temps de jeu). La carte ENGAGEE
    applique ensuite un multiplicateur (voir engaged_combat_stats).
    Retourne {hp, atk, unique_total, stars, bonus_pct}."""
    uniq = user_unique_rarity_breakdown(user_id)
    hp = PLAYER_HP_BASE + sum(PLAYER_HP_WEIGHTS.get(r, 0) * n for r, n in uniq.items())
    atk = PLAYER_ATK_BASE + sum(PLAYER_ATK_WEIGHTS.get(r, 0) * n for r, n in uniq.items())
    fmap = user_card_fusion_map(user_id)
    stars = sum(fmap.values())
    bonus_pct = fusion_bonus_pct(stars)
    mult = 1.0 + bonus_pct / 100.0
    soft = _collection_soft_factor(user_card_count(user_id))
    return {
        "hp": int(hp * mult * soft),
        "atk": int(atk * mult * soft),
        "unique_total": sum(uniq.values()),
        "stars": stars,
        "bonus_pct": bonus_pct,
    }


# Multiplicateur d'ATK selon la RARETE de la carte engagée au combat.
# Cree l'arbitrage "carte qui contre l'element (faible)" vs "grosse carte (neutre)".
# IMPORTANT : s'applique a l'ATK SEULE. Les PV viennent de la collection (= ta
# profondeur de jeu = ton mur), sinon la grosse carte serait a la fois plus
# tanky ET plus forte, et le contre elementaire ne servirait jamais.
# Ancre : epic = 1.0. common contre-element (0.8 x1.25 = 1.0) ~ epic neutre.
CARD_RARITY_COMBAT_MULT = {
    "common": 0.80, "rare": 0.92, "epic": 1.05,
    "legendary": 1.25, "mythic": 1.55, "secret": 1.90,
}

# Puissance de combat (affichage flashy) = PV + ATK x poids. Cappee a 999999999999999.
COMBAT_POWER_ATK_WEIGHT = 2
COMBAT_POWER_MAX = 999999999999999


def combat_power(hp, atk) -> int:
    p = int(hp) + int(atk) * COMBAT_POWER_ATK_WEIGHT
    return max(0, min(COMBAT_POWER_MAX, p))
# +20%/etoile (cap 5 = +100%, x2.0). La FUSION est le vrai axe de puissance (recompense
# l'investissement) plutot que la chance au roll. Ainsi une common 5* (0.80x2.0=1.60)
# bat une mythic brute (1.55). Les valeurs 0* ne changent pas -> equilibrage boss preserve.
CARD_STAR_COMBAT_BONUS = 0.20


def engaged_combat_stats(user_id, card_id):
    """Stats de combat REELLES. PV = socle collection (inchange). ATK = socle x
    modificateur de la carte engagée (rareté + etoiles de fusion de CETTE carte).
    Retourne {hp, atk, mult, rarity}."""
    base = compute_player_combat_stats(user_id)
    card = card_get(int(card_id)) if card_id else None
    rar = (card or {}).get("rarity")
    rar_mult = CARD_RARITY_COMBAT_MULT.get(rar, 1.0)
    stars = int(card_fusion_get(user_id, int(card_id))) if card_id else 0
    star_mult = 1.0 + min(5, stars) * CARD_STAR_COMBAT_BONUS
    mult = rar_mult * star_mult
    return {
        "hp": max(1, int(base["hp"])),
        "atk": max(1, int(base["atk"] * mult)),
        "mult": mult,
        "rarity": rar,
        "rar_mult": rar_mult,
        "stars": stars,
        "star_mult": star_mult,
    }


# ===== ROLL CHARGES (multi-roll par heure, par serveur) =====
import time as _roll_time


def roll_events_count(user_id, guild_id, window_sec=3600) -> int:
    """Nb de rolls 'normaux' (rechargeables) consommes dans la fenetre."""
    cutoff = _roll_time.time() - window_sec
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM roll_events "
                  "WHERE user_id = ? AND guild_id = ? AND rolled_at > ?",
                  (str(user_id), str(guild_id), cutoff)).fetchone()["n"]
    conn.close()
    return int(n)


def roll_events_oldest_ts(user_id, guild_id, window_sec=3600):
    """Timestamp epoch du plus vieux roll encore dans la fenetre (ou None)."""
    cutoff = _roll_time.time() - window_sec
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT MIN(rolled_at) AS t FROM roll_events "
                  "WHERE user_id = ? AND guild_id = ? AND rolled_at > ?",
                  (str(user_id), str(guild_id), cutoff)).fetchone()
    conn.close()
    return r["t"] if r and r["t"] is not None else None


def roll_events_add(user_id, guild_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_events (user_id, guild_id, rolled_at) VALUES (?, ?, ?)",
              (str(user_id), str(guild_id), _roll_time.time()))
    # purge vieux events (> 2h) pour ne pas gonfler la table
    c.execute("DELETE FROM roll_events WHERE rolled_at < ?", (_roll_time.time() - 7200,))
    conn.commit(); conn.close()


def roll_events_reset_all() -> int:
    """Owner : reset tous les cooldowns de roll (tout le monde peut re-roll)."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM roll_events")
    n = c.rowcount
    conn.commit(); conn.close()
    return n


# ===== ROLL BONUS (rolls offerts par owner, non rechargeables) =====
# Dispo = part du grant global non consommee + credits individuels.
def _roll_grant_row(c, user_id):
    r = c.execute("SELECT consumed, COALESCE(credits,0) AS credits "
                  "FROM roll_grant_state WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    return (int(r["consumed"]) if r else 0, int(r["credits"]) if r else 0)


def roll_bonus_available(user_id) -> int:
    grant = int(get_setting("roll_global_grant", "0") or 0)
    conn = get_db(); c = conn.cursor()
    consumed, credits = _roll_grant_row(c, user_id)
    conn.close()
    return max(0, grant - consumed) + max(0, credits)


def roll_bonus_consume(user_id) -> bool:
    """Consomme 1 roll bonus (grant global d'abord, puis credits). True si dispo."""
    grant = int(get_setting("roll_global_grant", "0") or 0)
    conn = get_db(); c = conn.cursor()
    consumed, credits = _roll_grant_row(c, user_id)
    if grant - consumed > 0:
        c.execute("INSERT INTO roll_grant_state (user_id, consumed) VALUES (?, 1) "
                  "ON CONFLICT(user_id) DO UPDATE SET consumed = consumed + 1",
                  (str(user_id),))
        ok = True
    elif credits > 0:
        c.execute("UPDATE roll_grant_state SET credits = credits - 1 WHERE user_id = ?",
                  (str(user_id),))
        ok = True
    else:
        ok = False
    conn.commit(); conn.close()
    return ok


def roll_give_user(user_id, n: int) -> int:
    """Owner : offre n rolls bonus a UN user (credits individuels). Retourne dispo."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_grant_state (user_id, credits) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET credits = COALESCE(credits,0) + excluded.credits",
              (str(user_id), int(n)))
    conn.commit(); conn.close()
    return roll_bonus_available(user_id)


def roll_set_user(user_id, n):
    """Owner : fixe le nombre EXACT de rolls bonus dispo d'un user.
    On annule sa part du grant global (consumed = grant) et on met credits = n."""
    grant = int(get_setting("roll_global_grant", "0") or 0)
    n = max(0, int(n))
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_grant_state (user_id, consumed, credits) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET consumed = ?, credits = ?",
              (str(user_id), grant, n, grant, n))
    conn.commit(); conn.close()


def roll_reset_user_cooldown(user_id) -> int:
    """Owner : reset le cooldown de roll d'UN user (tous serveurs)."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM roll_events WHERE user_id = ?", (str(user_id),))
    n = c.rowcount
    conn.commit(); conn.close()
    return n


def roll_reset_user_grant(user_id):
    """Owner : retire les rolls bonus d'UN user (credits 0 + aligne sur le grant global)."""
    grant = int(get_setting("roll_global_grant", "0") or 0)
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_grant_state (user_id, consumed, credits) VALUES (?, ?, 0) "
              "ON CONFLICT(user_id) DO UPDATE SET consumed = ?, credits = 0",
              (str(user_id), grant, grant))
    conn.commit(); conn.close()


def roll_grant_give_all(n: int) -> int:
    """Owner : offre n rolls bonus a tout le monde (grant cumulatif). Retourne nouveau grant."""
    grant = int(get_setting("roll_global_grant", "0") or 0) + int(n)
    set_setting("roll_global_grant", grant)
    return grant


def roll_grant_reset():
    """Remet le grant et la consommation a zero (retire les rolls bonus a tous)."""
    set_setting("roll_global_grant", 0)
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM roll_grant_state")
    conn.commit(); conn.close()


# ===== COMBAT BOSS =====
# Stats du boss selon le tier (1-5)
# Equilibrage (carte engagée x rareté désormais). Reference joueurs :
#   ATK base collection ~ 60k (moyen) a ~100k (gros rolleur), x carte ~0.8..1.55,
#   x matchup 0.8..1.25. T1 soloable par un gros joueur, T3 exige une vraie equipe.
BOSS_TIERS = {
    1: {"hp": 550000,   "atk": 7000,   "label": "Tier 1"},
    2: {"hp": 1250000,  "atk": 12000,  "label": "Tier 2"},
    3: {"hp": 2400000,  "atk": 15000,  "label": "Tier 3"},
    4: {"hp": 3600000,  "atk": 17000,  "label": "Tier 4"},
    5: {"hp": 4600000,  "atk": 20000,  "label": "Tier 5"},
}

# Scaling anti-powercreep : au lancement du combat, les PV/ATK du boss sont
# recalcules a partir de la puissance REELLE de l'equipe presente.
#   PV boss  = HP_FACTOR[tier] x somme(ATK base de l'equipe)
#   ATK boss = ATK_FACTOR[tier] x (PV base moyen de l'equipe)
# La base = socle collection (sans mult carte/etoiles/element/aptitude), donc
# fusionner/contrer/sortir une grosse rareté reste un avantage NON budgete = on
# gagne. Roller plus grossit le boss d'autant => jamais trivial.
# Facteurs calibres pour reproduire l'equilibrage de reference (cf BOSS_TIERS).
BOSS_TIER_SCALE = {
    1: {"hp": 3.0,  "atk": 0.06},
    2: {"hp": 5.5,  "atk": 0.10},
    3: {"hp": 7.9,  "atk": 0.13},
    4: {"hp": 11.8, "atk": 0.15},
    5: {"hp": 15.1, "atk": 0.17},
}


def card_boss_set_stats(boss_id, max_hp, atk):
    """Fixe PV (= max et courant) et ATK du boss (scaling sur l'equipe au lancement)."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET max_hp = ?, hp = ?, atk = ? WHERE id = ?",
              (int(max_hp), int(max_hp), int(atk), int(boss_id)))
    conn.commit(); conn.close()


def card_boss_create(guild_id, channel_id, name, element, tier, max_hp, atk,
                      image_url=None, start_at=None, card_id=None):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_boss (guild_id, channel_id, name, element, tier, max_hp, hp, atk, image_url, start_at, atk_spawn, card_id) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (str(guild_id), str(channel_id), name, element, int(tier),
               int(max_hp), int(max_hp), int(atk), image_url,
               float(start_at) if start_at else None, int(atk),
               int(card_id) if card_id else None))
    bid = c.lastrowid
    conn.commit(); conn.close()
    return bid


# ===== INVENTAIRE D'ITEMS (fragments mythic, golden roll, ...) =====
def _ensure_user_items():
    conn = get_db(); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_items (
        user_id  TEXT NOT NULL,
        item_key TEXT NOT NULL,
        qty      INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, item_key)
    )''')
    conn.commit(); conn.close()


def user_item_get(user_id, item_key) -> int:
    _ensure_user_items()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT qty FROM user_items WHERE user_id = ? AND item_key = ?",
                  (str(user_id), item_key)).fetchone()
    conn.close()
    return int(r["qty"]) if r else 0


def user_item_add(user_id, item_key, n=1):
    _ensure_user_items()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_items (user_id, item_key, qty) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, item_key) DO UPDATE SET qty = qty + excluded.qty",
              (str(user_id), item_key, int(n)))
    conn.commit(); conn.close()


def user_item_consume(user_id, item_key, n=1) -> bool:
    """Retire n exemplaires si dispo (atomic). True si consomme."""
    _ensure_user_items()
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_items SET qty = qty - ? WHERE user_id = ? AND item_key = ? AND qty >= ?",
              (int(n), str(user_id), item_key, int(n)))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


def user_item_set(user_id, item_key, qty):
    """Owner : fixe la quantite exacte d'un item."""
    _ensure_user_items()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_items (user_id, item_key, qty) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, item_key) DO UPDATE SET qty = excluded.qty",
              (str(user_id), item_key, max(0, int(qty))))
    conn.commit(); conn.close()


def currency_set(user_id, amount):
    """Owner : fixe le solde d'essences exact."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_currency (user_id, essences) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET essences = excluded.essences, "
              "updated_at = CURRENT_TIMESTAMP",
              (str(user_id), max(0, int(amount))))
    conn.commit(); conn.close()


def card_boss_set_start(boss_id, start_at):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET start_at = ? WHERE id = ?", (float(start_at), int(boss_id)))
    conn.commit(); conn.close()


def element_weaknesses(element):
    """Retourne les elements qui ont l'avantage contre `element` (le battent)."""
    return [e for e in CARD_ELEMENTS if element_matchup(e, element) > 1.0]


def card_boss_get(boss_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_boss WHERE id = ?", (int(boss_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_boss_list_active():
    """Boss encore en cours (recrutement ou combat) pour reprise au boot."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM card_boss WHERE status IN ('recruiting','fighting') "
                     "ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_boss_get_by_message(message_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_boss WHERE message_id = ?", (str(message_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_boss_set_message(boss_id, message_id):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET message_id = ? WHERE id = ?", (str(message_id), int(boss_id)))
    conn.commit(); conn.close()


def card_boss_apply_damage(boss_id, dmg) -> int:
    """Retire dmg au boss (atomic). Retourne le HP restant (>=0)."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET hp = MAX(0, hp - ?) WHERE id = ?", (int(dmg), int(boss_id)))
    r = c.execute("SELECT hp FROM card_boss WHERE id = ?", (int(boss_id),)).fetchone()
    conn.commit(); conn.close()
    return int(r["hp"]) if r else 0


def card_boss_heal(boss_id, amount) -> int:
    """Soigne le boss (cappe a max_hp). Retourne le HP apres soin."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET hp = MIN(max_hp, hp + ?) WHERE id = ?",
              (int(amount), int(boss_id)))
    r = c.execute("SELECT hp FROM card_boss WHERE id = ?", (int(boss_id),)).fetchone()
    conn.commit(); conn.close()
    return int(r["hp"]) if r else 0


def card_boss_set_status(boss_id, status):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET status = ? WHERE id = ?", (status, int(boss_id)))
    conn.commit(); conn.close()


def boss_participant_add(boss_id, user_id, name, element, hp, atk, card_id=None) -> bool:
    """Ajoute un participant. False si deja present."""
    conn = get_db(); c = conn.cursor()
    exists = c.execute("SELECT 1 FROM card_boss_participant WHERE boss_id = ? AND user_id = ?",
                       (int(boss_id), str(user_id))).fetchone()
    if exists:
        conn.close(); return False
    c.execute("INSERT INTO card_boss_participant (boss_id, user_id, name, element, max_hp, hp, atk, card_id) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (int(boss_id), str(user_id), name, element, int(hp), int(hp), int(atk),
               int(card_id) if card_id else None))
    conn.commit(); conn.close()
    return True


def boss_participant_get(boss_id, user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_boss_participant WHERE boss_id = ? AND user_id = ?",
                  (int(boss_id), str(user_id))).fetchone()
    conn.close()
    return dict(r) if r else None


def boss_participants_list(boss_id):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM card_boss_participant WHERE boss_id = ? "
                     "ORDER BY damage DESC", (int(boss_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def boss_participant_update(boss_id, user_id, hp=None, add_damage=None, last_attack=None,
                              element=None, card_id=None, aptitude=None, atk=None, max_hp=None):
    conn = get_db(); c = conn.cursor()
    sets, vals = [], []
    if hp is not None:
        sets.append("hp = ?"); vals.append(max(0, int(hp)))
    if max_hp is not None:
        sets.append("max_hp = ?"); vals.append(max(1, int(max_hp)))
    if atk is not None:
        sets.append("atk = ?"); vals.append(max(1, int(atk)))
    if add_damage is not None:
        sets.append("damage = damage + ?"); vals.append(int(add_damage))
    if last_attack is not None:
        sets.append("last_attack = ?"); vals.append(float(last_attack))
    if element is not None:
        sets.append("element = ?"); vals.append(element)
    if card_id is not None:
        sets.append("card_id = ?"); vals.append(int(card_id))
    if aptitude is not None:
        sets.append("aptitude = ?"); vals.append(aptitude)
    if not sets:
        conn.close(); return
    vals += [int(boss_id), str(user_id)]
    c.execute(f"UPDATE card_boss_participant SET {', '.join(sets)} WHERE boss_id = ? AND user_id = ?", vals)
    conn.commit(); conn.close()


# ===== WISHLIST =====
def wishlist_has(user_id, card_id) -> bool:
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM card_wishlist WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id))).fetchone()
    conn.close()
    return r is not None


def wishlist_toggle(user_id, card_id) -> bool:
    """Ajoute si absent, retire si present. Retourne True si ajoutee, False si retiree."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM card_wishlist WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id))).fetchone()
    if r:
        c.execute("DELETE FROM card_wishlist WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id)))
        added = False
    else:
        c.execute("INSERT OR IGNORE INTO card_wishlist (user_id, card_id) VALUES (?, ?)",
                  (str(user_id), int(card_id)))
        added = True
    conn.commit(); conn.close()
    return added


def wishlist_list(user_id):
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT w.card_id, w.added_at, c.name, c.rarity, c.universe "
        "FROM card_wishlist w JOIN cards c ON c.id = w.card_id "
        "WHERE w.user_id = ? ORDER BY c.name", (str(user_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wishlist_users_for_card(card_id, exclude_user=None):
    """Liste des user_id qui ont cette carte en wishlist (hors exclude_user)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT user_id FROM card_wishlist WHERE card_id = ?",
                     (int(card_id),)).fetchall()
    conn.close()
    out = [r["user_id"] for r in rows]
    if exclude_user is not None:
        out = [u for u in out if u != str(exclude_user)]
    return out


# ===== LEADERBOARDS (cartes) =====
LEADERBOARD_RARITY_POINTS = {
    "common": 1, "rare": 2, "epic": 5, "legendary": 25, "mythic": 100, "secret": 200,
}


def leaderboard_card_aggregates():
    """Par user : {user_id: {total, pts, mythic}}. Sur toute la base."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT uc.user_id AS uid, c.rarity AS rarity, COUNT(*) AS n "
        "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        "GROUP BY uc.user_id, c.rarity").fetchall()
    conn.close()
    agg = {}
    for r in rows:
        uid = r["uid"]; rar = r["rarity"]; n = int(r["n"])
        d = agg.setdefault(uid, {"total": 0, "pts": 0, "mythic": 0})
        d["total"] += n
        d["pts"] += LEADERBOARD_RARITY_POINTS.get(rar, 1) * n
        if rar == "mythic":
            d["mythic"] += n
    return agg


def leaderboard_essences(limit=10):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT user_id, essences FROM user_currency "
                     "WHERE essences > 0 ORDER BY essences DESC LIMIT ?",
                     (int(limit),)).fetchall()
    conn.close()
    return [(r["user_id"], int(r["essences"])) for r in rows]


def leaderboard_fusions(limit=10):
    """Top users par nb de cartes fusionnees (>=1 etoile) + total etoiles."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT user_id, COUNT(*) AS cards, SUM(fusion_level) AS stars "
        "FROM card_customizations WHERE fusion_level > 0 "
        "GROUP BY user_id ORDER BY stars DESC, cards DESC LIMIT ?",
        (int(limit),)).fetchall()
    conn.close()
    return [(r["user_id"], int(r["cards"]), int(r["stars"] or 0)) for r in rows]


# Essences rendues au recyclage d'un doublon (~50% du gain de roll)
ESSENCE_RECYCLE = {
    "common":    6,
    "rare":      14,
    "epic":      32,
    "legendary": 110,
    "mythic":    325,
    "secret":    500,
}

# Cout en doublons pour passer du niveau d'etoile L a L+1 (index = niveau actuel)
FUSION_STAR_COSTS = [2, 3, 4, 5, 6]  # total 20 doublons pour 5 etoiles
FUSION_MAX_STARS = 5

# Tier-up (/cardup) : consomme N doublons d'une rareté -> 1 carte rareté au-dessus
CARDUP_NEXT = {"common": "rare", "rare": "epic", "epic": "legendary", "legendary": "mythic"}
CARDUP_COST = {"common": 5, "rare": 5, "epic": 5, "legendary": 5}


def user_duplicate_count_by_rarity(user_id, rarity) -> int:
    """Nb de copies en trop (au-dela de 1 par carte) de cette rareté, UNIQUEMENT
    pour les cartes deja maxées (fusion 5 etoiles)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT uc.card_id, COUNT(*) AS n FROM user_cards uc "
        "JOIN cards c ON c.id = uc.card_id "
        "JOIN card_customizations cc ON cc.user_id = uc.user_id AND cc.card_id = uc.card_id "
        "WHERE uc.user_id = ? AND c.rarity = ? AND cc.fusion_level >= 5 "
        "GROUP BY uc.card_id",
        (str(user_id), rarity)).fetchall()
    conn.close()
    return sum(max(0, int(r["n"]) - 1) for r in rows)


def user_consume_duplicates_by_rarity(user_id, rarity, n) -> int:
    """Supprime n copies en trop (garde 1 par carte) de cette rareté, UNIQUEMENT
    sur les cartes maxées (5 etoiles). Garde la copie etoilee. Retourne nb supprime."""
    if n <= 0:
        return 0
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT uc.id, uc.card_id, COALESCE(uc.not_tradeable,0) AS nt "
        "FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        "JOIN card_customizations cc ON cc.user_id = uc.user_id AND cc.card_id = uc.card_id "
        "WHERE uc.user_id = ? AND c.rarity = ? AND cc.fusion_level >= 5 "
        "ORDER BY uc.card_id ASC, uc.not_tradeable DESC, uc.id ASC",
        (str(user_id), rarity)).fetchall()
    # Par carte : garde la 1ere (verrouillee en priorite), le reste = supprimable
    removable = []
    seen = set()
    for r in rows:
        cid = r["card_id"]
        if cid not in seen:
            seen.add(cid)          # 1ere copie gardee
            continue
        removable.append(r["id"])
    to_del = removable[:int(n)]
    if to_del:
        ph = ",".join("?" * len(to_del))
        c.execute(f"DELETE FROM user_cards WHERE id IN ({ph})", to_del)
    conn.commit(); conn.close()
    return len(to_del)


def card_pick_random_exact_rarity(rarity, element=None):
    """Carte aleatoire obtenable d'une rareté exacte. Filtre element optionnel."""
    conn = get_db(); c = conn.cursor()
    if element:
        r = c.execute("SELECT * FROM cards WHERE rarity = ? AND element = ? "
                      "AND COALESCE(not_obtainable,0) = 0 ORDER BY RANDOM() LIMIT 1",
                      (rarity, element)).fetchone()
    else:
        r = c.execute("SELECT * FROM cards WHERE rarity = ? AND COALESCE(not_obtainable,0) = 0 "
                      "ORDER BY RANDOM() LIMIT 1", (rarity,)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_customization_set(user_id, card_id, border_key):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_customizations (user_id, card_id, border_key, updated_at) "
              "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
              "ON CONFLICT(user_id, card_id) DO UPDATE SET border_key = excluded.border_key, "
              "updated_at = CURRENT_TIMESTAMP",
              (str(user_id), int(card_id), border_key))
    conn.commit(); conn.close()


def card_customization_get(user_id, card_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT border_key FROM card_customizations WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id))).fetchone()
    conn.close()
    return r["border_key"] if r else None


# ===== CARD SHOP =====
def card_shop_get_slots():
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM card_shop_slots ORDER BY slot").fetchall()
    conn.close()
    return [dict(r) for r in rows]


_SHOP_SLOT_COLS = {"item_type", "item_ref", "price", "label", "subtitle", "enabled"}


def card_shop_set_slot(slot, **fields):
    """Met a jour le slot. Tout champ present dans fields est ecrit, y compris
    None/vide (permet de vider un slot). Seules les cles non listees sont ignorees."""
    conn = get_db(); c = conn.cursor()
    sets, vals = [], []
    for col, v in fields.items():
        if col in _SHOP_SLOT_COLS:
            sets.append(f"{col} = ?")
            vals.append(v)
    if not sets:
        conn.close(); return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    vals.append(int(slot))
    c.execute(f"UPDATE card_shop_slots SET {', '.join(sets)} WHERE slot = ?", vals)
    conn.commit(); conn.close()
    return True


def card_shop_get_slot(slot):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_shop_slots WHERE slot = ?", (int(slot),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_pick_random_by_min_rarity(min_rarity: str):
    """Pioche carte random parmi celles >= min_rarity (skip not_obtainable + secret)."""
    tier = {"common": 0, "rare": 1, "epic": 2, "legendary": 3, "mythic": 4}
    min_tier = tier.get(min_rarity, 0)
    eligible = [k for k, v in tier.items() if v >= min_tier]
    if not eligible:
        return None
    placeholders = ",".join("?" * len(eligible))
    conn = get_db(); c = conn.cursor()
    row = c.execute(
        f"SELECT * FROM cards WHERE rarity IN ({placeholders}) "
        f"AND COALESCE(not_obtainable, 0) = 0 "
        f"ORDER BY RANDOM() LIMIT 1", eligible).fetchone()
    conn.close()
    return dict(row) if row else None


def card_trade_create(sender_id, receiver_id, guild_id, channel_id,
                       offer_items, request_items):
    """offer_items / request_items : list[(card_id, qty)].
    Retourne trade_id."""
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO card_trades (sender_id, receiver_id, guild_id, channel_id)
                 VALUES (?, ?, ?, ?)''',
              (str(sender_id), str(receiver_id),
                str(guild_id) if guild_id else None,
                str(channel_id) if channel_id else None))
    tid = c.lastrowid
    for cid, qty in offer_items:
        c.execute("INSERT INTO card_trade_items (trade_id, side, card_id, qty) VALUES (?, 'offer', ?, ?)",
                  (tid, int(cid), int(qty)))
    for cid, qty in request_items:
        c.execute("INSERT INTO card_trade_items (trade_id, side, card_id, qty) VALUES (?, 'request', ?, ?)",
                  (tid, int(cid), int(qty)))
    conn.commit(); conn.close()
    return tid


def card_trade_get(trade_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_trades WHERE id = ?", (int(trade_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_trade_items(trade_id, side=None):
    conn = get_db(); c = conn.cursor()
    if side:
        rows = c.execute(
            "SELECT ti.*, c.name, c.rarity, c.universe, c.subtitle "
            "FROM card_trade_items ti JOIN cards c ON c.id = ti.card_id "
            "WHERE ti.trade_id = ? AND ti.side = ?",
            (int(trade_id), side)).fetchall()
    else:
        rows = c.execute(
            "SELECT ti.*, c.name, c.rarity, c.universe, c.subtitle "
            "FROM card_trade_items ti JOIN cards c ON c.id = ti.card_id "
            "WHERE ti.trade_id = ?", (int(trade_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_trade_set_status(trade_id, status, message_id=None):
    conn = get_db(); c = conn.cursor()
    if message_id:
        c.execute("UPDATE card_trades SET status = ?, message_id = ?, "
                  "resolved_at = CASE WHEN ? IN ('accepted','refused','cancelled','countered') "
                  "THEN CURRENT_TIMESTAMP ELSE resolved_at END WHERE id = ?",
                  (status, str(message_id), status, int(trade_id)))
    else:
        c.execute("UPDATE card_trades SET status = ?, "
                  "resolved_at = CASE WHEN ? IN ('accepted','refused','cancelled','countered') "
                  "THEN CURRENT_TIMESTAMP ELSE resolved_at END WHERE id = ?",
                  (status, status, int(trade_id)))
    conn.commit(); conn.close()


def roll_cooldown_get(user_id, guild_id):
    """Retourne last_roll_at ISO ou None pour (user, guild)."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT last_roll_at FROM user_guild_roll_cooldown "
                  "WHERE user_id = ? AND guild_id = ?",
                  (str(user_id), str(guild_id))).fetchone()
    conn.close()
    return r["last_roll_at"] if r else None


def roll_cooldown_set(user_id, guild_id, when_iso):
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO user_guild_roll_cooldown (user_id, guild_id, last_roll_at)
                 VALUES (?, ?, ?)
                 ON CONFLICT(user_id, guild_id) DO UPDATE SET
                   last_roll_at = excluded.last_roll_at''',
              (str(user_id), str(guild_id), when_iso))
    conn.commit(); conn.close()


def user_card_settings_get(user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM user_card_settings WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    conn.close()
    return dict(r) if r else {"user_id": str(user_id), "last_roll_at": None,
                               "favorite_card": None}


def user_card_settings_set_last_roll(user_id, when_iso):
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO user_card_settings (user_id, last_roll_at)
                 VALUES (?, ?)
                 ON CONFLICT(user_id) DO UPDATE SET
                   last_roll_at = excluded.last_roll_at,
                   updated_at   = CURRENT_TIMESTAMP''',
              (str(user_id), when_iso))
    conn.commit(); conn.close()


# ===== Cards : config per-guild (salon obligatoire) =====
def guild_card_config_get(guild_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM guild_card_config WHERE guild_id = ?",
                  (str(guild_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def guild_card_config_set(guild_id, channel_id=None, enabled=None, ping_role_id=...):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO guild_card_config (guild_id) VALUES (?)",
              (str(guild_id),))
    fields = []
    values: list = []
    if channel_id is not None:
        fields.append("channel_id = ?"); values.append(str(channel_id) if channel_id else None)
    if enabled is not None:
        fields.append("enabled = ?"); values.append(1 if enabled else 0)
    # sentinelle ... -> ne pas toucher ; None -> effacer le role
    if ping_role_id is not ...:
        fields.append("ping_role_id = ?"); values.append(str(ping_role_id) if ping_role_id else None)
    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        c.execute(f"UPDATE guild_card_config SET {', '.join(fields)} WHERE guild_id = ?",
                  (*values, str(guild_id)))
    conn.commit(); conn.close()


# ===== Dashboard notifications helpers =====
def dash_notif_add(user_id, type_, title, message=None, link_url=None, guild_id=None):
    """Cree une notif pour un user. Retourne l'id."""
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO dashboard_notifications
                 (user_id, guild_id, type, title, message, link_url)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (str(user_id),
               str(guild_id) if guild_id else None,
               type_, title, message, link_url))
    nid = c.lastrowid
    conn.commit(); conn.close()
    return nid


def dash_notif_list(user_id, limit=20, unread_only=False):
    conn = get_db(); c = conn.cursor()
    where = "user_id = ?"
    params = [str(user_id)]
    if unread_only:
        where += " AND read_at IS NULL"
    rows = c.execute(
        f"SELECT * FROM dashboard_notifications WHERE {where} "
        f"ORDER BY created_at DESC LIMIT ?",
        params + [int(limit)],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def dash_notif_unread_count(user_id):
    conn = get_db(); c = conn.cursor()
    n = c.execute(
        "SELECT COUNT(*) AS n FROM dashboard_notifications "
        "WHERE user_id = ? AND read_at IS NULL",
        (str(user_id),),
    ).fetchone()["n"]
    conn.close()
    return int(n)


def dash_notif_mark_read(user_id, notif_id=None):
    """Marque comme lue. Si notif_id None : marque toutes."""
    conn = get_db(); c = conn.cursor()
    if notif_id:
        c.execute(
            "UPDATE dashboard_notifications SET read_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND read_at IS NULL",
            (int(notif_id), str(user_id)),
        )
    else:
        c.execute(
            "UPDATE dashboard_notifications SET read_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND read_at IS NULL",
            (str(user_id),),
        )
    conn.commit(); conn.close()


def dash_notif_purge_old(days=90):
    """Purge globale des notifs > N jours (cron daily)."""
    conn = get_db(); c = conn.cursor()
    c.execute(
        "DELETE FROM dashboard_notifications WHERE created_at < datetime('now', ?)",
        (f"-{int(days)} days",),
    )
    n = c.rowcount
    conn.commit(); conn.close()
    return n


# ===== Reminders helpers =====
def reminder_add(guild_id, user_id, channel_id, text, due_at_iso):
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO reminders (guild_id, user_id, channel_id, text, due_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (str(guild_id), str(user_id), str(channel_id), text, due_at_iso))
    rid = c.lastrowid
    conn.commit(); conn.close()
    return rid


def reminders_due(now_iso):
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at ASC",
        (now_iso,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reminder_mark_fired(reminder_id):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (int(reminder_id),))
    conn.commit(); conn.close()


def reminders_list_user(user_id, include_fired=False, limit=20):
    conn = get_db(); c = conn.cursor()
    if include_fired:
        rows = c.execute(
            "SELECT * FROM reminders WHERE user_id = ? ORDER BY due_at DESC LIMIT ?",
            (str(user_id), int(limit)),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND fired = 0 ORDER BY due_at ASC LIMIT ?",
            (str(user_id), int(limit)),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reminder_delete(reminder_id, user_id):
    """Supprime si appartient au user (anti hijack)."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?",
              (int(reminder_id), str(user_id)))
    deleted = c.rowcount > 0
    conn.commit(); conn.close()
    return deleted


# ===== Tempvoice helpers =====
def tempvoice_config_get(guild_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM tempvoice_config WHERE guild_id = ?", (str(guild_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def tempvoice_config_set(guild_id, lobby_channel_id, category_id=None, default_name=None):
    conn = get_db(); c = conn.cursor()
    c.execute('''
        INSERT INTO tempvoice_config (guild_id, lobby_channel_id, category_id, default_name, updated_at)
        VALUES (?, ?, ?, COALESCE(?, 'Vocal de {user}'), CURRENT_TIMESTAMP)
        ON CONFLICT(guild_id) DO UPDATE SET
            lobby_channel_id = excluded.lobby_channel_id,
            category_id      = excluded.category_id,
            default_name     = COALESCE(excluded.default_name, tempvoice_config.default_name),
            updated_at       = CURRENT_TIMESTAMP
    ''', (str(guild_id), str(lobby_channel_id),
          str(category_id) if category_id else None,
          default_name))
    conn.commit(); conn.close()


def tempvoice_config_disable(guild_id):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM tempvoice_config WHERE guild_id = ?", (str(guild_id),))
    conn.commit(); conn.close()


def tempvoice_track(channel_id, guild_id, owner_id):
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO tempvoice_active (channel_id, guild_id, owner_id, created_at)
                 VALUES (?, ?, ?, CURRENT_TIMESTAMP)''',
              (str(channel_id), str(guild_id), str(owner_id)))
    conn.commit(); conn.close()


def tempvoice_untrack(channel_id):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM tempvoice_active WHERE channel_id = ?", (str(channel_id),))
    conn.commit(); conn.close()


def tempvoice_owner_of(channel_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT owner_id FROM tempvoice_active WHERE channel_id = ?", (str(channel_id),)).fetchone()
    conn.close()
    return r["owner_id"] if r else None


def tempvoice_transfer(channel_id, new_owner_id):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE tempvoice_active SET owner_id = ? WHERE channel_id = ?",
              (str(new_owner_id), str(channel_id)))
    conn.commit(); conn.close()


def tempvoice_list_active(guild_id):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM tempvoice_active WHERE guild_id = ?",
                     (str(guild_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_bot_profile_get(guild_id):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM guild_bot_profile WHERE guild_id = ?", (str(guild_id),))
    r = c.fetchone(); conn.close()
    return dict(r) if r else None


def guild_bot_profile_set(guild_id, *, nick=None, avatar_url=None, banner_url=None,
                          about_me=None, status=None, activity_type=None, activity_text=None):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO guild_bot_profile (guild_id) VALUES (?)", (str(guild_id),))
    fields, values = [], []
    if nick          is not None: fields.append("nick = ?");          values.append(nick or None)
    if avatar_url    is not None: fields.append("avatar_url = ?");    values.append(avatar_url or None)
    if banner_url    is not None: fields.append("banner_url = ?");    values.append(banner_url or None)
    if about_me      is not None: fields.append("about_me = ?");      values.append(about_me or None)
    if status        is not None: fields.append("status = ?");        values.append(status or None)
    if activity_type is not None: fields.append("activity_type = ?"); values.append(activity_type or None)
    if activity_text is not None: fields.append("activity_text = ?"); values.append(activity_text or None)
    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        c.execute(f"UPDATE guild_bot_profile SET {', '.join(fields)} WHERE guild_id = ?",
                  (*values, str(guild_id)))
    conn.commit(); conn.close()


def guild_bot_profile_mark_applied(guild_id, applied_by=None):
    """Marque le profile comme applique. Si applied_by fourni, le trace
    pour pouvoir le revoke automatiquement a expiration TookBot+ du user."""
    conn = get_db(); c = conn.cursor()
    if applied_by is not None:
        c.execute(
            "UPDATE guild_bot_profile SET applied_at = CURRENT_TIMESTAMP, applied_by = ? WHERE guild_id = ?",
            (str(applied_by), str(guild_id)),
        )
    else:
        c.execute(
            "UPDATE guild_bot_profile SET applied_at = CURRENT_TIMESTAMP WHERE guild_id = ?",
            (str(guild_id),),
        )
    conn.commit(); conn.close()


def guild_bot_profile_clear(guild_id):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM guild_bot_profile WHERE guild_id = ?", (str(guild_id),))
    conn.commit(); conn.close()


def guild_bot_profile_list_all():
    """Pour le re-apply au boot : retourne tous les profils enregistres."""
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM guild_bot_profile")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows


def service_uptime_log(component: str, ok: bool):
    """Enregistre un check uptime pour un component dans le bucket de l'heure courante.

    UPSERT par (component, hour_bucket). On garde checks total + oks pour calculer
    le ratio par heure, et last_ok pour determiner la couleur de la barre.
    """
    conn = get_db()
    c = conn.cursor()
    hour_bucket = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:00")
    c.execute("""
        INSERT INTO service_uptime_check (component, hour_bucket, checks, oks, last_ok, updated_at)
        VALUES (?, ?, 1, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(component, hour_bucket) DO UPDATE SET
            checks  = checks + 1,
            oks     = oks + ?,
            last_ok = ?,
            updated_at = CURRENT_TIMESTAMP
    """, (component, hour_bucket, int(bool(ok)), int(bool(ok)), int(bool(ok)), int(bool(ok))))
    conn.commit()
    conn.close()


def service_uptime_history(component: str, hours: int = 24) -> list:
    """Retourne les checks des N dernieres heures pour un component.

    Liste de dicts {hour_bucket, checks, oks, last_ok}. Ordre chronologique ascendant.
    Les heures sans check sont absentes (a padder cote frontend si besoin).
    """
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT hour_bucket, checks, oks, last_ok
        FROM service_uptime_check
        WHERE component = ?
          AND hour_bucket >= strftime('%Y-%m-%d %H:00', datetime('now', ?))
        ORDER BY hour_bucket ASC
    """, (component, f"-{int(hours)} hours"))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def music_play_log(guild_id, user_id, track_title, track_url=None, source=None, duration=None):
    """Enregistre une lecture musicale (appele depuis play_next sur succes)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO music_plays
                 (guild_id, user_id, track_title, track_url, source, duration)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (str(guild_id), str(user_id) if user_id else None,
               (track_title or "")[:500], track_url, source, duration))
    conn.commit()
    conn.close()


def music_stats_top_tracks(guild_id=None, days=30, limit=10):
    conn = get_db()
    c = conn.cursor()
    where = "played_at >= datetime('now', ?)"
    params = [f"-{int(days)} days"]
    if guild_id:
        where += " AND guild_id = ?"
        params.append(str(guild_id))
    c.execute(f"""SELECT track_title, COUNT(*) AS plays, MAX(track_url) AS url
                  FROM music_plays
                  WHERE {where}
                  GROUP BY track_title
                  ORDER BY plays DESC
                  LIMIT ?""", (*params, int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def music_stats_top_requesters(guild_id=None, days=30, limit=10):
    conn = get_db()
    c = conn.cursor()
    where = "played_at >= datetime('now', ?) AND user_id IS NOT NULL"
    params = [f"-{int(days)} days"]
    if guild_id:
        where += " AND guild_id = ?"
        params.append(str(guild_id))
    c.execute(f"""SELECT user_id, COUNT(*) AS plays
                  FROM music_plays
                  WHERE {where}
                  GROUP BY user_id
                  ORDER BY plays DESC
                  LIMIT ?""", (*params, int(limit)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def music_stats_summary(guild_id=None, days=30):
    """Retourne {total_plays, unique_tracks, unique_users, total_seconds, by_source}."""
    conn = get_db()
    c = conn.cursor()
    where = "played_at >= datetime('now', ?)"
    params = [f"-{int(days)} days"]
    if guild_id:
        where += " AND guild_id = ?"
        params.append(str(guild_id))
    c.execute(f"""SELECT COUNT(*) AS total_plays,
                         COUNT(DISTINCT track_title) AS unique_tracks,
                         COUNT(DISTINCT user_id) AS unique_users,
                         COALESCE(SUM(duration), 0) AS total_seconds
                  FROM music_plays
                  WHERE {where}""", tuple(params))
    base = dict(c.fetchone() or {})
    c.execute(f"""SELECT COALESCE(source, 'youtube') AS source, COUNT(*) AS plays
                  FROM music_plays
                  WHERE {where}
                  GROUP BY source
                  ORDER BY plays DESC""", tuple(params))
    base["by_source"] = [dict(r) for r in c.fetchall()]
    conn.close()
    return base


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

def get_guild_analytics_overview(guild_id):
    """Stats overview pour la page Analytics serveur.

    Compte les logs (toutes activites : commands + actions + msg events)
    par fenetre temporelle. Active users = distinct user_id avec >= 1 log.
    """
    conn = get_db(); c = conn.cursor()
    g = (str(guild_id),)

    def _count(extra_where, params=()):
        sql = f"SELECT COUNT(*) AS n FROM logs WHERE guild_id = ? AND {extra_where}"
        return c.execute(sql, g + params).fetchone()["n"]

    def _distinct_users(extra_where, params=()):
        sql = f"SELECT COUNT(DISTINCT user_id) AS n FROM logs WHERE guild_id = ? AND user_id IS NOT NULL AND {extra_where}"
        return c.execute(sql, g + params).fetchone()["n"]

    out = {
        "msgs_today":         _count("date(ts) = date('now')"),
        "msgs_yesterday":     _count("date(ts) = date('now', '-1 day')"),
        "msgs_7d":            _count("ts >= datetime('now', '-7 days')"),
        "msgs_30d":           _count("ts >= datetime('now', '-30 days')"),
        "active_users_today": _distinct_users("date(ts) = date('now')"),
        "active_users_7d":    _distinct_users("ts >= datetime('now', '-7 days')"),
        "new_members_7d":     _count("type = 'action_member_join' AND ts >= datetime('now', '-7 days')"),
        "left_members_7d":    _count("type = 'action_member_leave' AND ts >= datetime('now', '-7 days')"),
    }
    conn.close()
    return out


def get_msg_per_day(guild_id, days=30):
    """Series temporelle : nb logs par jour sur les N derniers jours.

    Retourne liste de {date: 'YYYY-MM-DD', count: int} ordonnee du plus ancien
    au plus recent, avec zeros pour les jours sans activite.
    """
    import datetime as _dtmod
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        """SELECT date(ts) AS d, COUNT(*) AS n FROM logs
           WHERE guild_id = ? AND ts >= datetime('now', ?)
           GROUP BY d ORDER BY d ASC""",
        (str(guild_id), f"-{int(days)} days"),
    ).fetchall()
    conn.close()
    counts = {r["d"]: r["n"] for r in rows}
    # Comble les trous
    today = _dtmod.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - _dtmod.timedelta(days=i)
        ds = d.isoformat()
        out.append({"date": ds, "count": counts.get(ds, 0)})
    return out


def get_cohort_retention(guild_id, weeks=12):
    """Cohort retention par semaine. Pour chaque cohort (semaine de join),
    on calcule % de membres encore actifs (1+ log) dans les semaines suivantes.

    Retourne liste de dicts :
    [{cohort_week: 'YYYY-Www', cohort_size: int,
      week_offsets: [pct_w0, pct_w1, ..., pct_wN]}]
    """
    import datetime as _dtmod
    conn = get_db(); c = conn.cursor()
    # Liste cohorts : tous les member_join groupes par ISO week
    rows = c.execute(
        """SELECT strftime('%Y-%W', ts) AS week, user_id, ts
           FROM logs WHERE guild_id = ? AND type = 'action_member_join'
             AND ts >= datetime('now', ?)
           ORDER BY ts ASC""",
        (str(guild_id), f"-{int(weeks * 7 + 14)} days"),
    ).fetchall()
    cohorts: dict[str, list[str]] = {}
    cohort_join_ts: dict[str, dict[str, str]] = {}
    for r in rows:
        w = r["week"]
        cohorts.setdefault(w, []).append(r["user_id"])
        cohort_join_ts.setdefault(w, {})[r["user_id"]] = r["ts"]
    out = []
    today = _dtmod.date.today()
    for week, members in sorted(cohorts.items())[-weeks:]:
        cohort_size = len(members)
        # Pour chaque offset (semaine N apres join), compte combien sont actifs
        join_dates = {uid: r for uid, r in cohort_join_ts[week].items()}
        offsets = []
        for w_offset in range(weeks):
            start_iso = None
            end_iso = None
            # On utilise la semaine du 1er joiner comme reference
            try:
                ref = list(join_dates.values())[0]
                ref_dt = _dtmod.datetime.strptime(ref[:10], "%Y-%m-%d")
                start = ref_dt + _dtmod.timedelta(days=w_offset * 7)
                end   = start + _dtmod.timedelta(days=7)
                if start.date() > today:
                    break
                start_iso = start.strftime("%Y-%m-%d %H:%M:%S")
                end_iso   = end.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            # Count distinct user_id (members de la cohort) actifs sur cette fenetre
            ph = ",".join("?" * len(members))
            params = [str(guild_id), start_iso, end_iso] + members
            active = c.execute(
                f"""SELECT COUNT(DISTINCT user_id) AS n FROM logs
                    WHERE guild_id = ? AND ts >= ? AND ts < ?
                      AND user_id IN ({ph})""",
                params,
            ).fetchone()["n"]
            pct = round(100.0 * active / cohort_size, 1) if cohort_size else 0
            offsets.append(pct)
        out.append({
            "cohort_week": week,
            "cohort_size": cohort_size,
            "week_offsets": offsets,
        })
    conn.close()
    return out


def export_logs_csv_rows(guild_id, days=90):
    """Yield rows pour CSV : ts, type, user_id, username, channel_name, content.
    Generateur."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        """SELECT ts, type, user_id, username, channel_name, content
           FROM logs WHERE guild_id = ? AND ts >= datetime('now', ?)
           ORDER BY ts DESC""",
        (str(guild_id), f"-{int(days)} days"),
    )
    for r in rows:
        yield (r["ts"], r["type"], r["user_id"], r["username"],
               r["channel_name"], (r["content"] or "")[:500])
    conn.close()


def get_member_growth(guild_id, days=30):
    """Series temporelle membres : joins, leaves, net cumule par jour.

    Necessite que on_member_join/remove ait loggue action_member_join /
    action_member_leave avant. Pas de cumul absolu (on n'a pas le total
    initial), juste les variations.
    """
    import datetime as _dtmod
    conn = get_db(); c = conn.cursor()
    joins = c.execute(
        """SELECT date(ts) AS d, COUNT(*) AS n FROM logs
           WHERE guild_id = ? AND type = 'action_member_join'
             AND ts >= datetime('now', ?)
           GROUP BY d""",
        (str(guild_id), f"-{int(days)} days"),
    ).fetchall()
    leaves = c.execute(
        """SELECT date(ts) AS d, COUNT(*) AS n FROM logs
           WHERE guild_id = ? AND type = 'action_member_leave'
             AND ts >= datetime('now', ?)
           GROUP BY d""",
        (str(guild_id), f"-{int(days)} days"),
    ).fetchall()
    conn.close()
    j = {r["d"]: r["n"] for r in joins}
    l = {r["d"]: r["n"] for r in leaves}
    today = _dtmod.date.today()
    out = []
    cum = 0
    for i in range(days - 1, -1, -1):
        d = today - _dtmod.timedelta(days=i)
        ds = d.isoformat()
        joins_n  = j.get(ds, 0)
        leaves_n = l.get(ds, 0)
        cum += (joins_n - leaves_n)
        out.append({"date": ds, "joins": joins_n, "leaves": leaves_n, "net_cumulative": cum})
    return out


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

# ===== AI USAGE & SITE VISITS =====
def _ensure_analytics_tables():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS ai_usage (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           TEXT,
        guild_id          TEXT,
        model             TEXT,
        prompt_tokens     INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens      INTEGER DEFAULT 0,
        ts                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ai_usage_ts ON ai_usage(ts)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON ai_usage(user_id, ts)')

    c.execute('''CREATE TABLE IF NOT EXISTS site_visits (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        site    TEXT NOT NULL,
        path    TEXT,
        ip_hash TEXT,
        user_id TEXT,
        ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_visits_site_ts ON site_visits(site, ts)')

    # Tracking riche par pageview : durée active, scroll, device, referrer.
    c.execute('''CREATE TABLE IF NOT EXISTS page_views (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        vid        TEXT UNIQUE,
        site       TEXT NOT NULL,
        path       TEXT,
        referrer   TEXT,
        device     TEXT,
        browser    TEXT,
        os         TEXT,
        screen     TEXT,
        lang       TEXT,
        active_ms  INTEGER DEFAULT 0,
        scroll_pct INTEGER DEFAULT 0,
        ip_hash    TEXT,
        user_id    TEXT,
        ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pv_site_ts ON page_views(site, ts)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pv_vid ON page_views(vid)')

    # Dons Ko-fi (recus via webhook). txn_id unique pour eviter les doublons.
    c.execute('''CREATE TABLE IF NOT EXISTS donations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        txn_id      TEXT UNIQUE,
        kofi_type   TEXT,
        donor_name  TEXT,
        amount      REAL DEFAULT 0,
        currency    TEXT,
        message     TEXT,
        is_public   INTEGER DEFAULT 1,
        is_subscription INTEGER DEFAULT 0,
        tier_name   TEXT,
        email       TEXT,
        ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_don_ts ON donations(ts)')

    conn.commit()
    conn.close()

_ensure_analytics_tables()


def ai_usage_add(user_id, guild_id, model, prompt_tokens, completion_tokens, total_tokens):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO ai_usage
                 (user_id, guild_id, model, prompt_tokens, completion_tokens, total_tokens)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (str(user_id) if user_id else None,
               str(guild_id) if guild_id else None,
               model,
               int(prompt_tokens or 0),
               int(completion_tokens or 0),
               int(total_tokens or 0)))
    conn.commit()
    conn.close()


def ai_usage_stats():
    """Retourne stats agrégées : total, last 24h, 7j, 30j, top users."""
    conn = get_db()
    c = conn.cursor()
    out = {}
    for label, since in (("total", None), ("h24", "-1 day"),
                         ("d7", "-7 days"), ("d30", "-30 days")):
        if since:
            row = c.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS n, COUNT(*) AS calls "
                "FROM ai_usage WHERE ts >= datetime('now', ?)", (since,)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS n, COUNT(*) AS calls FROM ai_usage"
            ).fetchone()
        out[label] = {"tokens": int(row["n"]), "calls": int(row["calls"])}
    top = c.execute(
        '''SELECT user_id, SUM(total_tokens) AS tokens, COUNT(*) AS calls
           FROM ai_usage WHERE user_id IS NOT NULL AND ts >= datetime('now', '-30 days')
           GROUP BY user_id ORDER BY tokens DESC LIMIT 10'''
    ).fetchall()
    out["top_users_30d"] = [
        {"user_id": r["user_id"], "tokens": int(r["tokens"]), "calls": int(r["calls"])}
        for r in top
    ]
    by_day = c.execute(
        '''SELECT DATE(ts) AS day, SUM(total_tokens) AS tokens
           FROM ai_usage WHERE ts >= datetime('now', '-30 days')
           GROUP BY day ORDER BY day'''
    ).fetchall()
    out["by_day_30d"] = [{"day": r["day"], "tokens": int(r["tokens"])} for r in by_day]
    conn.close()
    return out


def visit_log(site: str, path: str, ip_hash: str, user_id=None):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO site_visits (site, path, ip_hash, user_id) VALUES (?, ?, ?, ?)''',
              (site, path[:200] if path else None, ip_hash[:64] if ip_hash else None,
               str(user_id) if user_id else None))
    conn.commit()
    conn.close()


def visits_stats(site: str):
    """Retourne stats agrégées : total, h24, 7j, 30j, uniques (ip_hash distinct)."""
    conn = get_db()
    c = conn.cursor()
    out = {}
    for label, since in (("total", None), ("h24", "-1 day"),
                         ("d7", "-7 days"), ("d30", "-30 days")):
        if since:
            row = c.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT ip_hash) AS u "
                "FROM site_visits WHERE site = ? AND ts >= datetime('now', ?)",
                (site, since)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT ip_hash) AS u FROM site_visits WHERE site = ?",
                (site,)
            ).fetchone()
        out[label] = {"visits": int(row["n"]), "unique": int(row["u"])}
    by_day = c.execute(
        '''SELECT DATE(ts) AS day, COUNT(*) AS n, COUNT(DISTINCT ip_hash) AS u
           FROM site_visits WHERE site = ? AND ts >= datetime('now', '-30 days')
           GROUP BY day ORDER BY day''', (site,)
    ).fetchall()
    out["by_day_30d"] = [{"day": r["day"], "visits": int(r["n"]), "unique": int(r["u"])} for r in by_day]
    conn.close()
    return out


def pageview_upsert(vid, site, path=None, referrer=None, device=None, browser=None,
                    os_name=None, screen=None, lang=None, active_ms=None,
                    scroll_pct=None, ip_hash=None, user_id=None):
    """Insere une pageview (1er hit) ou met a jour duree/scroll (heartbeat/unload).

    Le 1er appel cree la ligne avec les metadonnees device/referrer.
    Les appels suivants (meme vid) ne mettent a jour que active_ms (max) et scroll_pct (max).
    """
    if not vid:
        return
    conn = get_db()
    c = conn.cursor()
    row = c.execute("SELECT id, active_ms, scroll_pct FROM page_views WHERE vid = ?", (vid,)).fetchone()
    if row is None:
        c.execute('''INSERT INTO page_views
                     (vid, site, path, referrer, device, browser, os, screen, lang,
                      active_ms, scroll_pct, ip_hash, user_id)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (vid, site,
                   (path or "")[:200] or None,
                   (referrer or "")[:300] or None,
                   (device or "")[:20] or None,
                   (browser or "")[:40] or None,
                   (os_name or "")[:40] or None,
                   (screen or "")[:20] or None,
                   (lang or "")[:10] or None,
                   int(active_ms or 0),
                   int(scroll_pct or 0),
                   (ip_hash or "")[:64] or None,
                   str(user_id) if user_id else None))
    else:
        new_active = max(int(row["active_ms"] or 0), int(active_ms or 0))
        new_scroll = max(int(row["scroll_pct"] or 0), int(scroll_pct or 0))
        c.execute('''UPDATE page_views
                     SET active_ms = ?, scroll_pct = ?, updated_at = CURRENT_TIMESTAMP
                     WHERE vid = ?''', (new_active, min(100, new_scroll), vid))
    conn.commit()
    conn.close()


def pageview_stats(site: str):
    """Stats d'engagement riches pour un site (landing/dashboard).

    Retourne : compteurs par periode, duree active moyenne/mediane, taux de rebond,
    scroll moyen, split device/browser/os, top referrers, top pages, repartition horaire.
    """
    conn = get_db()
    c = conn.cursor()
    out = {}

    # --- Compteurs + engagement par periode ---
    for label, since in (("h24", "-1 day"), ("d7", "-7 days"), ("d30", "-30 days"), ("total", None)):
        where = "WHERE site = ?"
        params = [site]
        if since:
            where += " AND ts >= datetime('now', ?)"
            params.append(since)
        row = c.execute(
            f"""SELECT COUNT(*) AS n,
                       COUNT(DISTINCT ip_hash) AS u,
                       COALESCE(AVG(active_ms), 0) AS avg_ms,
                       COALESCE(AVG(scroll_pct), 0) AS avg_scroll,
                       COALESCE(SUM(CASE WHEN active_ms < 10000 THEN 1 ELSE 0 END), 0) AS bounces
                FROM page_views {where}""", params
        ).fetchone()
        n = int(row["n"])
        out[label] = {
            "visits": n,
            "unique": int(row["u"]),
            "avg_sec": round(float(row["avg_ms"]) / 1000, 1),
            "avg_scroll": round(float(row["avg_scroll"])),
            "bounce_pct": round(float(row["bounces"]) / n * 100) if n else 0,
        }

    # --- Mediane duree active (30j) ---
    durs = [int(r["active_ms"]) for r in c.execute(
        "SELECT active_ms FROM page_views WHERE site = ? AND ts >= datetime('now','-30 days') ORDER BY active_ms",
        (site,)
    ).fetchall()]
    if durs:
        mid = len(durs) // 2
        median_ms = durs[mid] if len(durs) % 2 else (durs[mid - 1] + durs[mid]) / 2
        out["median_sec_30d"] = round(median_ms / 1000, 1)
    else:
        out["median_sec_30d"] = 0

    # --- Visites par jour (30j) ---
    by_day = c.execute(
        '''SELECT DATE(ts) AS day, COUNT(*) AS n, COUNT(DISTINCT ip_hash) AS u,
                  COALESCE(AVG(active_ms),0) AS avg_ms
           FROM page_views WHERE site = ? AND ts >= datetime('now', '-30 days')
           GROUP BY day ORDER BY day''', (site,)
    ).fetchall()
    out["by_day_30d"] = [
        {"day": r["day"], "visits": int(r["n"]), "unique": int(r["u"]),
         "avg_sec": round(float(r["avg_ms"]) / 1000, 1)}
        for r in by_day
    ]

    # --- Helper split (30j) ---
    def _split(col):
        rows = c.execute(
            f"""SELECT COALESCE({col}, 'inconnu') AS k, COUNT(*) AS n
                FROM page_views WHERE site = ? AND ts >= datetime('now','-30 days')
                GROUP BY k ORDER BY n DESC LIMIT 12""", (site,)
        ).fetchall()
        return [{"key": r["k"], "n": int(r["n"])} for r in rows]

    out["by_device_30d"] = _split("device")
    out["by_browser_30d"] = _split("browser")
    out["by_os_30d"] = _split("os")

    # --- Top referrers (30j, hors self) ---
    refs = c.execute(
        """SELECT COALESCE(referrer,'direct') AS r, COUNT(*) AS n
           FROM page_views WHERE site = ? AND ts >= datetime('now','-30 days')
           GROUP BY r ORDER BY n DESC LIMIT 10""", (site,)
    ).fetchall()
    out["top_referrers_30d"] = [{"ref": r["r"], "n": int(r["n"])} for r in refs]

    # --- Top pages (30j) ---
    pages = c.execute(
        """SELECT COALESCE(path,'/') AS p, COUNT(*) AS n,
                  COALESCE(AVG(active_ms),0) AS avg_ms
           FROM page_views WHERE site = ? AND ts >= datetime('now','-30 days')
           GROUP BY p ORDER BY n DESC LIMIT 10""", (site,)
    ).fetchall()
    out["top_pages_30d"] = [
        {"path": r["p"], "n": int(r["n"]), "avg_sec": round(float(r["avg_ms"]) / 1000, 1)}
        for r in pages
    ]

    # --- Pages ou on reste le plus longtemps (30j) ---
    # Triees par temps actif moyen. Seuil min 3 visites pour eviter le bruit statistique.
    time_pages = c.execute(
        """SELECT COALESCE(path,'/') AS p, COUNT(*) AS n,
                  COALESCE(AVG(active_ms),0) AS avg_ms,
                  COALESCE(AVG(scroll_pct),0) AS avg_scroll
           FROM page_views WHERE site = ? AND ts >= datetime('now','-30 days')
           GROUP BY p HAVING n >= 3 ORDER BY avg_ms DESC LIMIT 10""", (site,)
    ).fetchall()
    out["top_time_pages_30d"] = [
        {"path": r["p"], "n": int(r["n"]),
         "avg_sec": round(float(r["avg_ms"]) / 1000, 1),
         "avg_scroll": round(float(r["avg_scroll"]))}
        for r in time_pages
    ]

    # --- Repartition horaire (30j, heure locale serveur) ---
    hours = c.execute(
        """SELECT CAST(strftime('%H', ts) AS INTEGER) AS h, COUNT(*) AS n
           FROM page_views WHERE site = ? AND ts >= datetime('now','-30 days')
           GROUP BY h""", (site,)
    ).fetchall()
    hour_map = {int(r["h"]): int(r["n"]) for r in hours}
    out["by_hour_30d"] = [{"hour": h, "n": hour_map.get(h, 0)} for h in range(24)]

    conn.close()
    return out


def donation_add(txn_id, kofi_type=None, donor_name=None, amount=0, currency=None,
                 message=None, is_public=1, is_subscription=0, tier_name=None, email=None):
    """Enregistre un don Ko-fi. Idempotent via txn_id (ON CONFLICT IGNORE).

    Retourne True si insere, False si doublon (txn_id deja vu).
    """
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('''INSERT OR IGNORE INTO donations
                     (txn_id, kofi_type, donor_name, amount, currency, message,
                      is_public, is_subscription, tier_name, email)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (txn_id, kofi_type,
                   (donor_name or "")[:100] or None,
                   float(amount or 0),
                   (currency or "")[:8] or None,
                   (message or "")[:500] or None,
                   1 if is_public else 0,
                   1 if is_subscription else 0,
                   (tier_name or "")[:60] or None,
                   (email or "")[:120] or None))
        inserted = c.rowcount > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def donation_delete(donation_id):
    """Supprime un don par son id. Retourne True si supprime."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM donations WHERE id = ?", (int(donation_id),))
        deleted = c.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def donations_stats():
    """Stats dons : totaux par periode, compteurs, top donateurs, liste recente, par jour."""
    conn = get_db()
    c = conn.cursor()
    out = {}
    for label, since in (("h24", "-1 day"), ("d7", "-7 days"),
                         ("d30", "-30 days"), ("total", None)):
        if since:
            row = c.execute(
                "SELECT COALESCE(SUM(amount),0) AS s, COUNT(*) AS n "
                "FROM donations WHERE ts >= datetime('now', ?)", (since,)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COALESCE(SUM(amount),0) AS s, COUNT(*) AS n FROM donations"
            ).fetchone()
        out[label] = {"amount": round(float(row["s"]), 2), "count": int(row["n"])}

    # Devise principale (la plus frequente)
    cur = c.execute(
        "SELECT currency, COUNT(*) AS n FROM donations WHERE currency IS NOT NULL "
        "GROUP BY currency ORDER BY n DESC LIMIT 1"
    ).fetchone()
    out["currency"] = cur["currency"] if cur else "EUR"

    # Don moyen (total)
    avg = c.execute("SELECT COALESCE(AVG(amount),0) AS a FROM donations").fetchone()
    out["avg_amount"] = round(float(avg["a"]), 2)

    # Top donateurs (cumul, tous temps)
    top = c.execute(
        '''SELECT COALESCE(donor_name,'Anonyme') AS name,
                  SUM(amount) AS total, COUNT(*) AS n
           FROM donations GROUP BY name ORDER BY total DESC LIMIT 10'''
    ).fetchall()
    out["top_donors"] = [
        {"name": r["name"], "total": round(float(r["total"]), 2), "count": int(r["n"])}
        for r in top
    ]

    # Dons recents (50 derniers)
    recent = c.execute(
        '''SELECT id, donor_name, amount, currency, message, is_subscription,
                  tier_name, ts
           FROM donations ORDER BY ts DESC LIMIT 50'''
    ).fetchall()
    out["recent"] = [
        {"id": int(r["id"]),
         "name": r["donor_name"] or "Anonyme",
         "amount": round(float(r["amount"]), 2),
         "currency": r["currency"] or "EUR",
         "message": r["message"],
         "is_subscription": bool(r["is_subscription"]),
         "tier_name": r["tier_name"],
         "ts": r["ts"]}
        for r in recent
    ]

    # Par jour (30j)
    by_day = c.execute(
        '''SELECT DATE(ts) AS day, COALESCE(SUM(amount),0) AS s, COUNT(*) AS n
           FROM donations WHERE ts >= datetime('now','-30 days')
           GROUP BY day ORDER BY day'''
    ).fetchall()
    out["by_day_30d"] = [
        {"day": r["day"], "amount": round(float(r["s"]), 2), "count": int(r["n"])}
        for r in by_day
    ]

    conn.close()
    return out


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
    # IA (Groq) — config globale, owner-only via dashboard
    "ai_enabled":           "0",
    "ai_model":             "llama-3.3-70b-versatile",
    "ai_system_prompt":     "Tu es TookBot, l'assistant officiel d'un bot Discord polyvalent. Tu es concis, utile, sympa, et tu parles français. Tu réponds en quelques phrases max sauf si on te demande un détail. Évite les listes interminables.",
    "ai_allowed_user_ids":  "",   # CSV
    "ai_max_tokens":        "400",
    # Modele vision (utilise si l'utilisateur joint une image/GIF a son message).
    # Doit etre un modele Groq qui supporte la vision (multimodal).
    "ai_vision_model":      "meta-llama/llama-4-scout-17b-16e-instruct",
    # Mode vocal IA : si "1", l'IA repond avec un message vocal (TTS) au lieu de texte.
    # Voix Microsoft Edge TTS (gratuit). Voix FR dispo :
    #   fr-FR-DeniseNeural (femme), fr-FR-HenriNeural (homme),
    #   fr-FR-EloiseNeural (jeune femme), fr-FR-VivienneMultilingualNeural (multi).
    "ai_voice_enabled":     "0",
    "ai_voice_name":        "fr-FR-DeniseNeural",
    # Provider TTS : "edge" (Microsoft Edge gratuit, robotique) ou "elevenlabs"
    # (qualite top, free tier 10k chars/mois, fallback auto vers edge si quota epuise).
    # ELEVENLABS_API_KEY doit etre defini dans .env pour "elevenlabs".
    "ai_voice_provider":    "edge",
    # Voice ID ElevenLabs (premade voices, fonctionnent en FR via le modele multilingual).
    "ai_elevenlabs_voice_id": "XB0fDUnXU5powFXDhCwa",  # Charlotte (femme, naturelle)
    "ai_elevenlabs_model":    "eleven_multilingual_v2",
    # Message soutien Ko-fi (poste quand un membre recoit un role de donateur)
    "soutien_message":      "<user> A décidé de filer un coup de main ! Merci pour ton soutien !",
    "soutien_role_ids":     "",   # CSV d'IDs de roles ; vide = fallback noms par defaut
    "soutien_channel_id":   "",   # vide = fallback env SOUTIEN_CHANNEL_ID
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
    # Feature toggles
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
    # XP — configurables par serveur
    "xp_min":              "1",
    "xp_max":              "5",
    "xp_cooldown_seconds": "30",
    # Courbe de difficulte : exposant E dans xp_for_level(L) = L^E.
    # Plage utile 2.0 a 8.0. Defaut 5.0 (= ancien comportement).
    # Plus bas = montee facile (level 10 atteignable rapidement).
    # Plus haut = montee dure (chaque level demande beaucoup plus de XP).
    "xp_curve_exponent":   "5.0",
    # Message de bienvenue par défaut du serveur
    "welcome_template": "👋 Bienvenue {user} !\nBienvenue sur **{guild}** ! Tu es le membre numéro **{count}**.",
    # Setup initial (configuré via /setup)
    "setup_completed":            "0",
    "setup_welcome_channel_id":   "",
    "setup_logs_channel_id":      "",
    "setup_alerts_channel_id":    "",
    "setup_admin_channel_id":     "",
    # Présentations membres
    "presentation_enabled":       "0",
    "presentation_channel_id":    "",
    # Permissions modérateurs (configurees par le server owner)
    "mod_role_id":                "",
    "mod_access_configured":      "0",
    # Toggleables slash commands
    "mod_perm_warn":              "0",
    "mod_perm_rolereaction":      "0",
    "mod_perm_ticket":            "0",
    "mod_perm_giveaway":          "0",
    "mod_perm_clear":             "0",
    "mod_perm_kick":              "0",
    "mod_perm_poll":              "0",
    "mod_perm_modlogs":           "0",
    "mod_perm_setwelcome":        "0",
    "mod_perm_reaction":          "0",
    "mod_perm_socialalert":       "0",
    "mod_perm_ban":               "0",
    "mod_perm_setup":             "0",
    "mod_perm_xp":                "0",
    "mod_perm_note":              "0",
    # Toggleables pages dashboard (sans slash equivalent)
    "mod_perm_features":          "0",
    "mod_perm_settings":          "0",
    "mod_perm_logs":              "0",
    "mod_perm_custom_commands":   "0",
    "mod_perm_music":             "0",
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


# ===== Member roles (cache pour gating mod perms) =====
def member_roles_set(guild_id, user_id, role_ids):
    """Remplace tous les role_ids d'un member pour cette guild."""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM member_roles WHERE guild_id = ? AND user_id = ?",
        (str(guild_id), str(user_id)),
    )
    for rid in (role_ids or []):
        c.execute(
            "INSERT OR IGNORE INTO member_roles (guild_id, user_id, role_id) VALUES (?, ?, ?)",
            (str(guild_id), str(user_id), str(rid)),
        )
    conn.commit()
    conn.close()


def member_roles_clear(guild_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM member_roles WHERE guild_id = ? AND user_id = ?",
        (str(guild_id), str(user_id)),
    )
    conn.commit()
    conn.close()


def member_has_role(guild_id, user_id, role_id) -> bool:
    if not role_id:
        return False
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT 1 FROM member_roles WHERE guild_id = ? AND user_id = ? AND role_id = ? LIMIT 1",
        (str(guild_id), str(user_id), str(role_id)),
    ).fetchone()
    conn.close()
    return bool(row)


def member_get_roles(guild_id, user_id) -> list[str]:
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT role_id FROM member_roles WHERE guild_id = ? AND user_id = ?",
        (str(guild_id), str(user_id)),
    ).fetchall()
    conn.close()
    return [r["role_id"] for r in rows]


def mod_has_perm(guild_id, user_id, perm_key: str, mod_role_id: str | None = None) -> bool:
    """True si user a le mod_role configure ET la perm_key est activee.

    `perm_key` sans le prefixe 'mod_perm_'. Ex: 'kick', 'ticket'.
    """
    # Lit mod_role_id si non fourni
    if mod_role_id is None:
        mod_role_id = guild_setting_get(guild_id, "mod_role_id", "") or ""
    if not mod_role_id:
        return False
    if not member_has_role(guild_id, user_id, mod_role_id):
        return False
    val = guild_setting_get(guild_id, f"mod_perm_{perm_key}", "0")
    return val == "1"


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


def add_premium_grant(user_id, feature="all", granted_by=None, note=None, expires_at=None):
    """Accorde manuellement la feature premium a un utilisateur.

    `expires_at` (ISO TEXT) : grant temporaire (trial, abo). None = permanent.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO premium_grants (user_id, feature, granted_by, granted_at, note, expires_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(user_id, feature) DO UPDATE SET
            granted_by = excluded.granted_by,
            granted_at = CURRENT_TIMESTAMP,
            note       = excluded.note,
            expires_at = excluded.expires_at
    ''', (str(user_id), feature, str(granted_by) if granted_by else None, note, expires_at))
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
    """True si l'user a un grant manuel pour cette feature, non expire.

    Par defaut, un grant feature='all' compte aussi (master pack premium).
    Passer `inherit_all=False` pour exiger un grant strictement sur la feature
    demandee. Utile pour les abonnements distincts (ex. Battle Pass) qui ne
    doivent PAS etre auto-debloques par le grant 'all' du /niveau Premium.

    expires_at NULL = grant permanent. expires_at <= now = expire (ignore).
    """
    conn = get_db()
    c = conn.cursor()
    # Filtre expires_at : NULL ou futur. Comparaison ISO TEXT lexicographique
    # OK car format datetime('now') = 'YYYY-MM-DD HH:MM:SS'.
    if inherit_all:
        row = c.execute(
            """SELECT 1 FROM premium_grants
               WHERE user_id = ? AND feature IN (?, 'all')
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
               LIMIT 1""",
            (str(user_id), feature),
        ).fetchone()
    else:
        row = c.execute(
            """SELECT 1 FROM premium_grants
               WHERE user_id = ? AND feature = ?
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
               LIMIT 1""",
            (str(user_id), feature),
        ).fetchone()
    conn.close()
    return bool(row)


def start_tookbot_plus_trial(user_id, days: int = 7) -> dict:
    """Demarre un trial TookBot+ de N jours pour cet user. 1 seul trial / user.

    Retourne {ok: bool, error: str|None, expires_at: str|None}.
    """
    import datetime as _dtmod
    conn = get_db()
    c = conn.cursor()

    # Verifie qu'il n'y a pas deja un trial use (premium_settings.trial_used_at)
    row = c.execute(
        "SELECT trial_used_at FROM premium_settings WHERE user_id = ?",
        (str(user_id),),
    ).fetchone()
    if row and row["trial_used_at"]:
        conn.close()
        return {"ok": False, "error": "trial_already_used", "expires_at": None}

    # Verifie qu'il n'a pas deja TookBot+ actif (grant permanent ou trial actif)
    active = c.execute(
        """SELECT 1 FROM premium_grants
           WHERE user_id = ? AND feature = 'tookbot_plus'
             AND (expires_at IS NULL OR expires_at > datetime('now'))
           LIMIT 1""",
        (str(user_id),),
    ).fetchone()
    if active:
        conn.close()
        return {"ok": False, "error": "already_active", "expires_at": None}

    expires = (_dtmod.datetime.utcnow() + _dtmod.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    # Insere le grant temporaire
    c.execute('''
        INSERT INTO premium_grants (user_id, feature, granted_by, granted_at, note, expires_at)
        VALUES (?, 'tookbot_plus', NULL, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(user_id, feature) DO UPDATE SET
            granted_at = CURRENT_TIMESTAMP,
            note       = excluded.note,
            expires_at = excluded.expires_at
    ''', (str(user_id), f"trial_{days}j", expires))
    # Marque trial_used_at dans premium_settings (cree row si absente)
    now_iso = _dtmod.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''
        INSERT INTO premium_settings (user_id, trial_used_at)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET trial_used_at = excluded.trial_used_at
    ''', (str(user_id), now_iso))
    conn.commit()
    conn.close()
    return {"ok": True, "error": None, "expires_at": expires}


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


# ===== GUILD BOOST + =====
def guild_boost_get_for_user(user_id) -> list[dict]:
    """Retourne toutes les assignations actives d'un user (1 max sauf owner)."""
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT guild_id, assigned_at FROM guild_boost WHERE user_id = ? ORDER BY assigned_at DESC",
        (str(user_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_boost_assign(user_id, guild_id):
    """Assigne le Guild Boost + d'un user a une guild (upsert).

    Le caller doit verifier la capacite (user_max_guild_slots) AVANT d'appeler.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO guild_boost (user_id, guild_id, assigned_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, guild_id) DO UPDATE SET
            assigned_at = CURRENT_TIMESTAMP
    ''', (str(user_id), str(guild_id)))
    conn.commit()
    conn.close()


def guild_boost_unassign(user_id, guild_id=None):
    """Retire l'assignation. Si guild_id None, retire toutes celles du user."""
    conn = get_db()
    c = conn.cursor()
    if guild_id is None:
        c.execute("DELETE FROM guild_boost WHERE user_id = ?", (str(user_id),))
    else:
        c.execute(
            "DELETE FROM guild_boost WHERE user_id = ? AND guild_id = ?",
            (str(user_id), str(guild_id)),
        )
    conn.commit()
    conn.close()


def user_max_guild_slots(user_id, *, sku_solo=None, sku_duo=None, sku_squad=None,
                          owner_id=None) -> int:
    """Nb total de serveurs que ce user peut booster simultanement.

    Slots cumulatifs (stackent) :
    - Owner -> 999 (illimite)
    - Solo  (entitlement OU grant 'guild_boost')        : +1
    - Duo   (entitlement OU grant 'guild_boost_duo')    : +2
    - Squad (entitlement OU grant 'guild_boost_squad')  : +5
    """
    if not user_id:
        return 0
    uid = str(user_id)
    if owner_id and uid == str(owner_id):
        return 999
    slots = 0
    if (sku_solo and user_has_active_entitlement(uid, sku_id=sku_solo)) \
            or has_premium_grant(uid, feature="guild_boost", inherit_all=False):
        slots += 1
    if (sku_duo and user_has_active_entitlement(uid, sku_id=sku_duo)) \
            or has_premium_grant(uid, feature="guild_boost_duo", inherit_all=False):
        slots += 2
    if (sku_squad and user_has_active_entitlement(uid, sku_id=sku_squad)) \
            or has_premium_grant(uid, feature="guild_boost_squad", inherit_all=False):
        slots += 5
    return slots


def guild_has_active_boost(guild_id, *, sku_solo=None, sku_duo=None, sku_squad=None,
                            owner_id=None) -> bool:
    """True si au moins un user a assigne son Guild Boost + actif a cette guild.

    Verifie que les users qui ont assigne ont toujours des slots disponibles
    (grant ou entitlement valide sur un des 3 tiers).
    """
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT user_id FROM guild_boost WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchall()
    conn.close()
    if not rows:
        return False
    for r in rows:
        uid = r["user_id"]
        if user_max_guild_slots(
            uid, sku_solo=sku_solo, sku_duo=sku_duo, sku_squad=sku_squad,
            owner_id=owner_id,
        ) > 0:
            return True
    return False


def user_can_assign_guild_boost(user_id, *, sku_solo=None, sku_duo=None, sku_squad=None,
                                 owner_id=None) -> bool:
    """True si le user a au moins 1 slot Guild Boost + utilisable."""
    return user_max_guild_slots(
        user_id, sku_solo=sku_solo, sku_duo=sku_duo, sku_squad=sku_squad,
        owner_id=owner_id,
    ) > 0


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
    (2,  "title",     {"title_idx": 0},                  None),
    (3,  "boost_xp",  {"hours": 1, "multiplier": 2.0},   "Boost XP ×2 pendant 1h"),
    (4,  "bg",        {"index": 0},                      None),
    (5,  "emoji",     {"emoji_idx": 0},                  None),
    (6,  "title",     {"title_idx": 1},                  None),
    (7,  "boost_xp",  {"hours": 1, "multiplier": 2.0},   "Boost XP ×2 pendant 1h"),
    (8,  "emoji",     {"emoji_idx": 1},                  None),
    (9,  "bg",        {"index": 1},                      None),
    (10, "sabre",     {"rarete": "R"},                   None),
    (11, "emoji",     {"emoji_idx": 2},                  None),
    (12, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (13, "title",     {"title_idx": 2},                  None),
    (14, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (15, "bg",        {"index": 2},                      None),
    (16, "emoji",     {"emoji_idx": 3},                  None),
    (17, "title",     {"title_idx": 3},                  None),
    (18, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (19, "boost_xp",  {"hours": 2, "multiplier": 2.0},   "Boost XP ×2 pendant 2h"),
    (20, "sabre",     {"rarete": "SR"},                  None),
    (21, "emoji",     {"emoji_idx": 4},                  None),
    (22, "boost_xp",  {"hours": 3, "multiplier": 2.0},   "Boost XP ×2 pendant 3h"),
    (23, "bg",        {"index": 3},                      None),
    (24, "emoji",     {"emoji_idx": 5},                  None),
    (25, "title",     {"title_idx": 4},                  None),
    (26, "boost_xp",  {"hours": 3, "multiplier": 2.0},   "Boost XP ×2 pendant 3h"),
    (27, "title",     {"title_idx": 5},                  None),
    (28, "emoji",     {"emoji_idx": 6},                  None),
    (29, "bg",        {"index": 4},                      None),
    (30, "sabre",     {"rarete": "SSR"},                 None),
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
    """Insere les 30 lignes pass_rewards pour une saison. Idempotent.

    Tout est themed par mois (titres, emojis, sabres, BGs) : la map
    _PASS_TIER_MAP utilise des indices (title_idx, emoji_idx) ou des
    references (rarete, bg index) que seasonal_themes resout en valeurs
    concretes selon le theme du mois."""
    from seasonal_themes import sabre_skin, _theme_for, tier_title, tier_emoji
    conn = get_db()
    c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM pass_rewards WHERE season_id = ?", (season_id,)).fetchone()["n"]
    if n > 0:
        conn.close()
        return
    theme = _theme_for(month_key)
    bg_labels_dict = theme["bg_labels"]
    rows = []
    for tier, rtype, payload, label in _PASS_TIER_MAP:
        if rtype == "bg":
            bg_name = _SEASONAL_BG_NAMES[payload["index"]]
            payload = {"bg_id": f"seasonal:{month_key}:{bg_name}", "bg_name": bg_name}
            themed_name = bg_labels_dict.get(bg_name, bg_name.replace("_", " ").title())
            label = f"Background : {themed_name}"
        elif rtype == "sabre":
            skin = sabre_skin(month_key, payload["rarete"])
            label = f"Sabre {payload['rarete']} : {skin['nom']}"
        elif rtype == "title":
            t = tier_title(month_key, payload["title_idx"])
            payload = {"title": t}
            label = f"Titre : {t}"
        elif rtype == "emoji":
            e = tier_emoji(month_key, payload["emoji_idx"])
            payload = {"emoji": e}
            label = f"Emoji {e}"
        rows.append((season_id, tier, rtype, _json.dumps(payload), label))
    c.executemany(
        "INSERT INTO pass_rewards (season_id, tier, type, payload, label) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"[seed] pass_rewards x{len(rows)} for season {season_id} ({month_key})")


def seed_seasonal_sabres(month_key: str):
    """Cree 3 sabres cosmetiques (R/SR/SSR) saisonniers.

    Pour conserver le principe anti-P2W, chaque sabre saisonnier COPIE l'effet
    + la description detaillee d'un sabre f2p existant. MAIS le sabre f2p
    source change chaque mois (cf. seasonal_themes.MONTH_THEMES) ce qui donne
    une vraie variete gameplay au Pass entre saisons. Le nom du sabre,
    l'emoji, le nom de la speciale et son emoji viennent du theme du mois.

    IDs : season_<YYYY-MM>_<R|SR|SSR>"""
    from seasonal_themes import sabre_skin
    conn = get_db()
    c = conn.cursor()
    rows = []
    for rarete in ("R", "SR", "SSR"):
        skin = sabre_skin(month_key, rarete)
        source_id = skin["source_id"]
        # Lit le sabre f2p source pour copier effet + description
        src = c.execute("SELECT * FROM sabres WHERE id = ?", (source_id,)).fetchone()
        if not src:
            print(f"[seed sabres saisonniers] source f2p manquant: {source_id} (mois {month_key} {rarete}) — skip")
            continue
        desc_generale = f"Skin saisonnier du Battle Pass · stats identiques au {src['nom']}."
        rows.append((
            f"season_{month_key}_{rarete}",
            skin["nom"], skin["emoji_sabre"], rarete, 0, desc_generale,
            skin["nom_special"], src["speciale_description"], skin["emoji_special"], src["speciale_effet"],
        ))
    # UPSERT : si le sabre existe deja (saison generee precedemment avec un
    # ancien template) on met a jour ses champs visuels pour refleter le theme
    # courant. Mecanique (speciale_effet) reste identique = anti-P2W.
    for s in rows:
        try:
            c.execute('''
                INSERT INTO sabres
                    (id, nom, emoji, rarete, prix, description,
                     speciale_nom, speciale_description, speciale_emoji, speciale_effet)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    nom                  = excluded.nom,
                    emoji                = excluded.emoji,
                    description          = excluded.description,
                    speciale_nom         = excluded.speciale_nom,
                    speciale_description = excluded.speciale_description,
                    speciale_emoji       = excluded.speciale_emoji,
                    speciale_effet       = excluded.speciale_effet
            ''', s)
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
    # Titles + emojis sont permanents (gardes entre saisons), donc on ignore
    # expires_at qui pouvait etre set par d'anciens bugs ou rotations de saison.
    unlocks = list_user_pass_unlocks(user_id, include_expired=True)
    titles_owned = set()
    emojis_owned = set()
    for u in unlocks:
        if u["type"] not in ("title", "emoji"):
            continue
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
    """Retourne les listes 'titles' et 'emojis' que l'user possede via Pass.

    Permanents : on ignore expires_at (anciens unlocks pouvaient avoir une date
    set par bug ou par d'anciennes rotations de saison).
    """
    unlocks = list_user_pass_unlocks(user_id, include_expired=True)
    titles, emojis = [], []
    for u in unlocks:
        if u["type"] not in ("title", "emoji"):
            continue
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
                       created_by=None, label: str = None, position: int = 0,
                       delivery: str = "reaction", style: str = "embed"):
    """Insere un mapping reaction-role. UPSERT sur (guild, message, emoji).

    delivery : 'reaction' (emojis sous le message) ou 'button' (boutons).
    style    : 'embed' ou 'text' (message normal).
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO reaction_roles
            (guild_id, message_id, channel_id, emoji, role_id, mode, group_key, created_by, label, position, delivery, style)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, message_id, emoji) DO UPDATE SET
            channel_id = excluded.channel_id,
            role_id    = excluded.role_id,
            mode       = excluded.mode,
            group_key  = excluded.group_key,
            label      = excluded.label,
            position   = excluded.position,
            delivery   = excluded.delivery,
            style      = excluded.style
    ''', (str(guild_id), str(message_id), str(channel_id), emoji,
          str(role_id), mode, group_key, str(created_by) if created_by else None,
          label, int(position), delivery, style))
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
