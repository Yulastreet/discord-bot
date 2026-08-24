import os
import sqlite3
import datetime as _dt
from typing import Optional

from services.i18n import t

# DB file configurable via env DB_PATH (dev = bot_database_dev.db by default)
DB_FILE = os.getenv("DB_PATH") or "bot_database.db"


def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL: readers no longer block during a write (autocomplete, etc.)
    # busy_timeout: wait instead of raising 'database is locked' immediately.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
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

    # ===== MIGRATION users: switch to composite PK (guild_id, user_id) =====
    # Detection: if the table exists without a guild_id column, drop+recreate.
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

    # ===== guilds table (registry of Discord servers seen by the bot) =====
    c.execute('''CREATE TABLE IF NOT EXISTS guilds (
        guild_id     TEXT PRIMARY KEY,
        name         TEXT,
        icon_url     TEXT,
        member_count INTEGER DEFAULT 0,
        owner_id     TEXT,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        active       INTEGER DEFAULT 1
    )''')
    # Migration: add owner_id if the table already existed without that column.
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
    # Global catalog of pop culture cards (Anime, Manga, Video game, Star
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
    # Migration: source_image_url (original URL before the overlay)
    try:
        c.execute("ALTER TABLE cards ADD COLUMN source_image_url TEXT")
    except Exception:
        pass
    # Migration: not_tradeable flag on user_cards
    try:
        c.execute("ALTER TABLE user_cards ADD COLUMN not_tradeable INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration: from_cheat (owner cheat) -> excluded from the live drop feed
    try:
        c.execute("ALTER TABLE user_cards ADD COLUMN from_cheat INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration: profile color (cardprofile) + guild emblem
    try:
        c.execute("ALTER TABLE card_profile ADD COLUMN color TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE card_guild ADD COLUMN emblem TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE card_guild ADD COLUMN renamed_at TEXT")
    except Exception:
        pass
    for _gcol, _gddl in (("min_level", "INTEGER DEFAULT 0"),
                         ("min_power", "INTEGER DEFAULT 0"),
                         ("open_join", "INTEGER DEFAULT 0")):
        try:
            c.execute(f"ALTER TABLE card_guild ADD COLUMN {_gcol} {_gddl}")
        except Exception:
            pass
    # Migration: not_obtainable flag on cards (hidden from catalog + roll)
    try:
        c.execute("ALTER TABLE cards ADD COLUMN not_obtainable INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration: event_key -> card tied to a global event. The card stays
    # ALWAYS obtainable; during the matching event its drop rate is just boosted.
    try:
        c.execute("ALTER TABLE cards ADD COLUMN event_key TEXT")
    except Exception:
        pass
    # Migration : alt_image_url -> skin alternatif premium (achetable en boutique event).
    try:
        c.execute("ALTER TABLE cards ADD COLUMN alt_image_url TEXT")
    except Exception:
        pass
    # Migration: flavor_subtitle (subtitle displayed under the name)
    try:
        c.execute("ALTER TABLE cards ADD COLUMN flavor_subtitle TEXT")
    except Exception:
        pass
    # Migration: element (random per card) for the combat system
    try:
        c.execute("ALTER TABLE cards ADD COLUMN element TEXT")
    except Exception:
        pass
    # Backfill: assign a random element to cards that have none (one-shot)
    try:
        c.execute(
            "UPDATE cards SET element = CASE ABS(RANDOM()) % 5 "
            "WHEN 0 THEN 'eclat' WHEN 1 THEN 'abysse' WHEN 2 THEN 'fracture' "
            "WHEN 3 THEN 'vif' ELSE 'neant' END "
            "WHERE element IS NULL OR element = ''")
    except Exception:
        pass
    # Migration: winning_emoji on card_event_log
    try:
        c.execute("ALTER TABLE card_event_log ADD COLUMN winning_emoji TEXT")
    except Exception:
        pass
    # Migration: claim_code (text captcha)
    try:
        c.execute("ALTER TABLE card_event_log ADD COLUMN claim_code TEXT")
    except Exception:
        pass
    # Migration: role to ping on event drop / boss (cards feature fans)
    try:
        c.execute("ALTER TABLE guild_card_config ADD COLUMN ping_role_id TEXT")
    except Exception:
        pass
    # Migration: card_scale_pct on borders (card scale inside the frame)
    try:
        c.execute("ALTER TABLE borders ADD COLUMN card_scale_pct INTEGER DEFAULT 100")
    except Exception:
        pass
    # Migration: qty on user_borders (consumable borders, copies in stock)
    try:
        c.execute("ALTER TABLE user_borders ADD COLUMN qty INTEGER DEFAULT 1")
    except Exception:
        pass
    # Migration: fusion_level on card_customizations (star prestige 0-5)
    try:
        c.execute("ALTER TABLE card_customizations ADD COLUMN fusion_level INTEGER DEFAULT 0")
    except Exception:
        pass

    # Ownership: a user can own several copies of the same card.
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

    # Card suggestions from the community (owner approves via dashboard)
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
    # Migration: suggestion type + target for edits + proposed_rarity
    for col, ddl in (
        ("suggestion_type", "TEXT DEFAULT 'new'"),
        ("target_card_id", "INTEGER"),
        ("proposed_rarity", "TEXT"),
        ("original_image_url", "TEXT"),
        ("forward_message_id", "TEXT"),
        ("votes_up", "INTEGER DEFAULT 0"),
        ("votes_down", "INTEGER DEFAULT 0"),
    ):
        try:
            c.execute(f"ALTER TABLE card_suggestions ADD COLUMN {col} {ddl}")
        except Exception:
            pass

    # Cards Events: random card drops in a channel, first reaction wins
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

    # ===== Essence economy: global per-user currency =====
    c.execute('''CREATE TABLE IF NOT EXISTS user_currency (
        user_id   TEXT PRIMARY KEY,
        essences  INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Borders: catalog + placement config (owner) =====
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

    # Cosmetics owned per user (borders for now)
    c.execute('''CREATE TABLE IF NOT EXISTS user_borders (
        user_id     TEXT NOT NULL,
        border_key  TEXT NOT NULL,
        acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, border_key)
    )''')

    # Border applied by a user on a given card
    c.execute('''CREATE TABLE IF NOT EXISTS card_customizations (
        user_id     TEXT NOT NULL,
        card_id     INTEGER NOT NULL,
        border_key  TEXT,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, card_id)
    )''')

    # ===== Cooperative boss fight =====
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
    try:
        c.execute("ALTER TABLE card_boss_participant ADD COLUMN heal INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE card_boss_participant ADD COLUMN taken INTEGER DEFAULT 0")
    except Exception:
        pass
    # taken_raw = RAW damage taken (before the Guardian reduction) -> tank grade
    try:
        c.execute("ALTER TABLE card_boss_participant ADD COLUMN taken_raw INTEGER DEFAULT 0")
    except Exception:
        pass
    # died = 1 if the player dropped to 0 HP during the fight -> caps the grade at C
    try:
        c.execute("ALTER TABLE card_boss_participant ADD COLUMN died INTEGER DEFAULT 0")
    except Exception:
        pass
    # Combat event stream (for the live dashboard: party_hit, boss_aoe,
    # boss_smash, enrage, heal, end...). The front-end replays them as animations.
    c.execute('''CREATE TABLE IF NOT EXISTS card_boss_event (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        boss_id   INTEGER NOT NULL,
        etype     TEXT NOT NULL,
        data      TEXT,
        ts        REAL DEFAULT 0
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_boss_event ON card_boss_event(boss_id, id)")
    # Automatic boss schedule: next spawn per Discord server.
    c.execute('''CREATE TABLE IF NOT EXISTS boss_auto_schedule (
        guild_id TEXT PRIMARY KEY,
        next_at  REAL
    )''')
    # Live chat of the boss fight (dashboard).
    c.execute('''CREATE TABLE IF NOT EXISTS card_boss_chat (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        boss_id  INTEGER NOT NULL,
        user_id  TEXT,
        name     TEXT,
        text     TEXT,
        ts       REAL DEFAULT 0
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_boss_chat ON card_boss_chat(boss_id, id)")

    # ===== Roll charges (multi-roll/h) + bonus rolls offerts =====
    c.execute('''CREATE TABLE IF NOT EXISTS roll_events (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   TEXT NOT NULL,
        guild_id  TEXT NOT NULL,
        rolled_at REAL NOT NULL
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_roll_events_uig ON roll_events(user_id, guild_id, rolled_at)")
    # Lifetime counter of rolls performed (roll_events is purged after 2h)
    c.execute('''CREATE TABLE IF NOT EXISTS card_roll_total (
        user_id TEXT PRIMARY KEY,
        total   INTEGER DEFAULT 0
    )''')
    # Anti-abuse: "solo" servers (user alone with the bot) where a user already rolled.
    # Caps the number of such servers per account -> kills farming via fake servers.
    c.execute('''CREATE TABLE IF NOT EXISTS card_roll_solo_guild (
        user_id    TEXT NOT NULL,
        guild_id   TEXT NOT NULL,
        first_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, guild_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS roll_grant_state (
        user_id   TEXT PRIMARY KEY,
        consumed  INTEGER DEFAULT 0
    )''')
    try:
        c.execute("ALTER TABLE roll_grant_state ADD COLUMN credits INTEGER DEFAULT 0")
    except Exception:
        pass

    # ===== Card Wishlist: cards wanted by a user =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_wishlist (
        user_id   TEXT NOT NULL,
        card_id   INTEGER NOT NULL,
        added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, card_id)
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_card ON card_wishlist(card_id)")

    # ===== Card Profile: 3 showcase cards per user =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_profile (
        user_id   TEXT PRIMARY KEY,
        left_id   INTEGER,
        mid_id    INTEGER,
        right_id  INTEGER,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Card Shop: 6 slots configurable by the owner =====
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
    # Seed slots 1-6 if missing
    for _slot in range(1, 7):
        c.execute("INSERT OR IGNORE INTO card_shop_slots (slot, enabled) VALUES (?, 0)", (_slot,))
    # Seed default borders (5 provided)
    _default_borders = [
        ("gold",  t("data.border.gold"),  "gold_border.png",  1),
        ("leaf",  t("data.border.leaf"),  "leaf_border.png",  2),
        ("frost", t("data.border.frost"), "frost_border.png", 3),
        ("hell",  t("data.border.hell"),  "hell_border.png",  4),
        ("void",  t("data.border.void"),  "void_border.png",  5),
    ]
    for _bk, _bn, _bf, _so in _default_borders:
        c.execute("INSERT OR IGNORE INTO borders (border_key, name, filename, sort_order) "
                   "VALUES (?, ?, ?, ?)", (_bk, _bn, _bf, _so))

    # Card trades between players (multi-card, non-equivalent)
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

    # ===== PLAYER GUILDS (cross-server clubs) =====
    c.execute('''CREATE TABLE IF NOT EXISTS card_guild (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        tag          TEXT,
        owner_id     TEXT NOT NULL,
        level        INTEGER DEFAULT 1,
        xp           INTEGER DEFAULT 0,
        bank         INTEGER DEFAULT 0,
        color        TEXT,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS card_guild_member (
        guild_id        INTEGER NOT NULL,
        user_id         TEXT NOT NULL,
        role            TEXT DEFAULT 'member',
        joined_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        xp_contributed  INTEGER DEFAULT 0,
        daily_xp        INTEGER DEFAULT 0,
        daily_date      TEXT,
        PRIMARY KEY (guild_id, user_id)
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_cguild_member_user ON card_guild_member(user_id)")
    c.execute('''CREATE TABLE IF NOT EXISTS card_guild_invite (
        guild_id   INTEGER NOT NULL,
        user_id    TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS card_guild_left (
        user_id  TEXT PRIMARY KEY,
        left_at  TEXT
    )''')
    # Applications to a guild
    c.execute('''CREATE TABLE IF NOT EXISTS card_guild_application (
        guild_id   INTEGER NOT NULL,
        user_id    TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id)
    )''')
    # Daily quests (personal, per member) and weekly quests (collective, per guild)
    c.execute('''CREATE TABLE IF NOT EXISTS guild_quest_daily (
        user_id   TEXT NOT NULL,
        guild_id  INTEGER NOT NULL,
        day       TEXT NOT NULL,
        quest_key TEXT NOT NULL,
        progress  INTEGER DEFAULT 0,
        done      INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, day, quest_key)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS guild_quest_weekly (
        guild_id  INTEGER NOT NULL,
        week      TEXT NOT NULL,
        quest_key TEXT NOT NULL,
        progress  INTEGER DEFAULT 0,
        done      INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, week, quest_key)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS guild_quest_weekly_contrib (
        guild_id  INTEGER NOT NULL,
        week      TEXT NOT NULL,
        quest_key TEXT NOT NULL,
        user_id   TEXT NOT NULL,
        contrib   INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, week, quest_key, user_id)
    )''')
    # Guild XP history: who earned how much, through which action.
    c.execute('''CREATE TABLE IF NOT EXISTS card_guild_xp_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id   INTEGER NOT NULL,
        user_id    TEXT NOT NULL,
        amount     INTEGER NOT NULL,
        source     TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_cguild_xplog ON card_guild_xp_log(guild_id, id DESC)")

    # Roll cooldown per (user, guild) - 1h per server
    c.execute('''CREATE TABLE IF NOT EXISTS user_guild_roll_cooldown (
        user_id        TEXT NOT NULL,
        guild_id       TEXT NOT NULL,
        last_roll_at   TEXT,
        PRIMARY KEY (user_id, guild_id)
    )''')

    # Per-guild config: channel required to use /roll and /collection
    c.execute('''CREATE TABLE IF NOT EXISTS guild_card_config (
        guild_id     TEXT PRIMARY KEY,
        channel_id   TEXT,
        enabled      INTEGER DEFAULT 1,
        updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ===== Dashboard notifications : cloche header =====
    # Stored per user_id. Type: 'automod_alert', 'entitlement', 'milestone',
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

    # ===== Reminders: /remind <duration> <text> =====
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

    # ===== Tempvoice: temporary voice channels =====
    # Per-guild config: lobby_channel_id = the "Create your channel" voice
    # channel users join to trigger creation; category_id = category where the
    # created channel is placed (null = same category as the lobby).
    c.execute('''CREATE TABLE IF NOT EXISTS tempvoice_config (
        guild_id          TEXT PRIMARY KEY,
        lobby_channel_id  TEXT NOT NULL,
        category_id       TEXT,
        default_name      TEXT DEFAULT 'Vocal de {user}',
        updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Active tempvoice channels: tracks created channels so we know who the
    # owner is + cleanup at boot if the bot was offline on the last "empty".
    c.execute('''CREATE TABLE IF NOT EXISTS tempvoice_active (
        channel_id   TEXT PRIMARY KEY,
        guild_id     TEXT NOT NULL,
        owner_id     TEXT NOT NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_tempvoice_guild ON tempvoice_active(guild_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tempvoice_owner ON tempvoice_active(owner_id)")

    # ===== Bot personalizer: custom bot profile per server =====
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
    # Migration: add status/activity columns if the table already existed
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

    # ===== Uptime checks (page /status.html, hour-by-hour bars) =====
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

    # ===== bot_commands table (web -> bot queue, 1.5s polling) =====
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

    # ===== logs table (commands + actions per server) =====
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

    # ===== guild_channels table (channel cache per server) =====
    c.execute('''CREATE TABLE IF NOT EXISTS guild_channels (
        guild_id   TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        name       TEXT,
        type       TEXT,
        position   INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, channel_id)
    )''')

    # ===== settings table (dynamic config) =====
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

    # Promo codes (owner creates them, users redeem via /redeem CODE)
    # reward_type : 'tookcoins' | 'pass_xp' | 'premium_grant_days'
    # reward_value: int (TC amount, XP, or days depending on type)
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

    # Scout sessions: shareable link with scout data of the 5 opponents
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

    # ===== guild_members table (member cache per server) =====
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

    # Roles of a member (for mod perms gating on dashboard/bot side)
    c.execute('''CREATE TABLE IF NOT EXISTS member_roles (
        guild_id TEXT NOT NULL,
        user_id  TEXT NOT NULL,
        role_id  TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id, role_id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_member_roles_user ON member_roles(guild_id, user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_member_roles_role ON member_roles(guild_id, role_id)')

    # ===== dm_messages table (DMs between users and the bot, global cross-guild) =====
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

    # welcome table
    c.execute('''CREATE TABLE IF NOT EXISTS welcome (
        guild_id TEXT PRIMARY KEY,
        channel_id INTEGER,
        message TEXT
    )''')
    try:
        c.execute("ALTER TABLE welcome ADD COLUMN message TEXT")
    except Exception:
        pass

    # Duel profile table
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

    # Lightsaber collection table
    c.execute('''CREATE TABLE IF NOT EXISTS duel_collection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        sabre_id TEXT,
        obtenu_le TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, sabre_id),
        FOREIGN KEY(user_id) REFERENCES duel_profil(user_id)
    )''')

    # Duel history table
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

    # sabres table (editable through the web dashboard)
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

    # Migration: new combat system columns
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
            pass  # Column already exists

    # ===== Role cache per guild (for dashboard pickers) =====
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
    # "Open a ticket" panel: message with a button in a public channel.
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

    # Tickets opened by members
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
    # Notifies a Discord channel when a creator posts on a platform.
    # platform: 'twitch' (live) | 'youtube' (new video) | 'reddit' (new post)
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
    # Mapping (guild, message, emoji) -> role. mode='toggle' = standard add/remove,
    # standard, 'add_only' = does not remove the role when the user removes the reaction,
    # 'unique' = within the same group_key, only one active role (radio).
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

    # Migration: label + position columns for advanced reaction_roles
    # + delivery (reaction|button) and style (embed|text) of the message
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

    # ===== CS2: linked profiles (steam / faceit / declared premier elo) =====
    c.execute('''CREATE TABLE IF NOT EXISTS cs_profiles (
        discord_id  TEXT PRIMARY KEY,
        steam_id    TEXT,
        faceit_id   TEXT,
        faceit_nick TEXT,
        premier_elo INTEGER,
        linked_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at  TEXT
    )''')
    # Rank-role config per guild (enabled or not, plus the role ID per tier)
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
    # Premier queue lobbies (temporary voice channels). Auto-deleted when empty.
    c.execute('''CREATE TABLE IF NOT EXISTS cs_queue_lobbies (
        channel_id TEXT PRIMARY KEY,
        guild_id   TEXT NOT NULL,
        creator_id TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Light cache (skin prices, stats) to reduce API hits
    c.execute('''CREATE TABLE IF NOT EXISTS cs_cache (
        cache_key  TEXT PRIMARY KEY,
        data       TEXT,
        fetched_at REAL
    )''')

    # ===== MONETIZATION (Discord SKU / entitlements) =====
    # Stores every Discord entitlement received (user purchase).
    # For a "durable" SKU (one-time purchase), starts_at is filled, ends_at NULL and deleted=0
    # mean permanent premium. For "subscription", ends_at bounds the active period.
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

    # Premium settings per user (custom /niveau card, etc.).
    c.execute('''CREATE TABLE IF NOT EXISTS premium_settings (
        user_id           TEXT PRIMARY KEY,
        niveau_background TEXT DEFAULT 'default',
        updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Migration: new Pass cosmetic columns + TookBot+ trial flag
    for col, ddl in [
        ("pass_selected_title", "TEXT DEFAULT NULL"),
        ("pass_selected_emoji", "TEXT DEFAULT NULL"),
        ("trial_used_at",       "TEXT DEFAULT NULL"),  # ISO timestamp 1er trial
    ]:
        try:
            c.execute(f"ALTER TABLE premium_settings ADD COLUMN {col} {ddl}")
        except Exception:
            pass

    # Manual premium grants (owner gives the feature for free, test accounts, etc.).
    # feature='all' = pack complet, 'pass' = Battle Pass, 'guild_boost' = Guild Boost +, etc.
    c.execute('''CREATE TABLE IF NOT EXISTS premium_grants (
        user_id    TEXT NOT NULL,
        feature    TEXT NOT NULL DEFAULT 'all',
        granted_by TEXT,
        granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
        note       TEXT,
        PRIMARY KEY (user_id, feature)
    )''')
    # Migration: expires_at for temporary grants (7-day trial, etc.)
    try:
        c.execute("ALTER TABLE premium_grants ADD COLUMN expires_at TEXT DEFAULT NULL")
    except Exception:
        pass

    # TookBot+ activation keys: generated by the owner (promo codes page),
    # single use, redeemable on the /subscription page. Each key grants
    # TookBot+ for `duration_days` days starting from redemption.
    c.execute('''CREATE TABLE IF NOT EXISTS tookbot_plus_keys (
        code          TEXT PRIMARY KEY,
        duration_days INTEGER NOT NULL,
        created_by    TEXT,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        note          TEXT,
        redeemed_by   TEXT DEFAULT NULL,
        redeemed_at   TEXT DEFAULT NULL,
        redeemed_username TEXT DEFAULT NULL,
        redeemed_avatar   TEXT DEFAULT NULL,
        revoked_at        TEXT DEFAULT NULL
    )''')
    # Migrations for tookbot_plus_keys tables already created without these columns
    for _col in ("redeemed_username TEXT", "redeemed_avatar TEXT", "revoked_at TEXT"):
        try:
            c.execute(f"ALTER TABLE tookbot_plus_keys ADD COLUMN {_col} DEFAULT NULL")
        except Exception:
            pass

    # Guild Boost + assignments: a user assigns their purchase/grant to one (or
    # several if owner) guild. Composite PK so the owner can have several
    # assignments; for other users we delete the previous ones
    # rows before insert (1 user = 1 assignment max).
    c.execute('''CREATE TABLE IF NOT EXISTS guild_boost (
        user_id     TEXT NOT NULL,
        guild_id    TEXT NOT NULL,
        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, guild_id)
    )''')

    # ===== BATTLE PASS =====
    # A season = 1 calendar month. month_key in 'YYYY-MM' format.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_seasons (
        season_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        month_key   TEXT UNIQUE NOT NULL,
        name        TEXT,
        started_at  TEXT NOT NULL,
        ends_at     TEXT NOT NULL,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Reward definition per tier (1..30) and per season.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_rewards (
        season_id   INTEGER NOT NULL,
        tier        INTEGER NOT NULL,
        type        TEXT NOT NULL,            -- 'bg' | 'sabre' | 'title' | 'emoji' | 'boost_xp' | 'tookcoins'
        payload     TEXT,                     -- JSON: {bg_id} or {sabre_id} or {title} etc.
        label       TEXT,
        PRIMARY KEY (season_id, tier),
        FOREIGN KEY (season_id) REFERENCES pass_seasons(season_id) ON DELETE CASCADE
    )''')

    # Pool of quest templates; we draw from it on daily/weekly reset.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_quest_templates (
        template_id INTEGER PRIMARY KEY AUTOINCREMENT,
        type        TEXT NOT NULL,            -- 'send_messages' | 'play_duels' | 'earn_xp' | 'use_commands'
        period      TEXT NOT NULL,            -- 'daily' | 'weekly'
        target      INTEGER NOT NULL,
        label       TEXT NOT NULL,
        xp_reward   INTEGER NOT NULL DEFAULT 50
    )''')

    # Active quests of a user for the current period.
    c.execute('''CREATE TABLE IF NOT EXISTS pass_user_quests (
        user_id      TEXT NOT NULL,
        period       TEXT NOT NULL,           -- 'daily' | 'weekly'
        slot         INTEGER NOT NULL,        -- 0..N to distinguish several quests in the same period
        template_id  INTEGER,
        type         TEXT NOT NULL,
        target       INTEGER NOT NULL,
        progress     INTEGER NOT NULL DEFAULT 0,
        period_start TEXT NOT NULL,           -- ISO period start date (YYYY-MM-DD for daily, YYYY-Www for weekly)
        claimed      INTEGER NOT NULL DEFAULT 0,
        xp_reward    INTEGER NOT NULL DEFAULT 50,
        PRIMARY KEY (user_id, period, slot, period_start)
    )''')

    # User progression in the season (Pass XP != message XP).
    c.execute('''CREATE TABLE IF NOT EXISTS pass_progress (
        user_id          TEXT NOT NULL,
        season_id        INTEGER NOT NULL,
        xp               INTEGER NOT NULL DEFAULT 0,
        claimed_max_tier INTEGER NOT NULL DEFAULT 0,
        updated_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, season_id)
    )''')

    # Rewards unlocked by a user. expires_at NULL = permanent
    # (sabers, titles). Otherwise expiry date (seasonal BGs = end of next month).
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

    # Seed the sabres table if empty (from duel_sabres.SABRES_DEFAULT)
    seed_sabres_si_vide()
    # Migration: add the new f2p SSR sabers (obsidienne, celeste)
    # introduced after the initial seed of existing DBs.
    ensure_extra_default_sabres()
    seed_pass_quest_templates_si_vide()
    # Migration: first clean up broken seasonal sabers (invalid rarities)
    cleanup_legacy_seasonal_sabres()
    # Re-seed seasonal sabers + pass_rewards for existing seasons
    _migrate_pass_rewards_and_sabres()
    # Initial cards seed if the table is empty
    try:
        from services.cards_seed import seed_initial_cards
        seed_initial_cards()
    except Exception as e:
        print(f"[cards seed] erreur: {e!r}")
    print("[OK] Base de donnees initialisee !")


# ===== DUEL - SABERS (DB) =====
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
    """Delete a saber. Also cleans up collections and resets equipped sabers."""
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
    """Import the default sabers from duel_sabres.SABRES_DEFAULT if the table is empty."""
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
    """Migration: for existing DBs created before the new f2p sabers were
    added to the SSR pool (obsidienne, celeste). INSERT OR IGNORE so nothing
    already modified gets overwritten."""
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
    """Update the allowed fields of the duel profile via the dashboard."""
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
    """Return profile + collection + history for the dashboard."""
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
    # If it was their equipped saber, reset to blue
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


# ===== MESSAGE XP (per-guild) - clean rework 2026-06 =====
# Canonical formula: xp_for_level(L) = L^E ; get_level(xp) = floor(xp^(1/E))
# E = curve exponent, configurable per server via 'xp_curve_exponent'
# (default 5.0). Useful range 2.0 to 8.0.

_DEFAULT_XP_EXPONENT = 5.0
_XP_EXP_MIN = 2.0
_XP_EXP_MAX = 8.0


def get_xp_curve_exponent(guild_id=None) -> float:
    """Read the XP curve exponent for this server. Clamped to [2.0, 8.0]."""
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
    """Canonical upsert: recomputes level from xp using the server curve."""
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
    """Increment XP. Returns (new_xp, old_level, new_level, leveled_up)."""
    cur = get_xp(guild_id, user_id)
    old_level = get_level(cur, guild_id)
    new_xp = max(0, cur + int(delta or 0))
    set_xp(guild_id, user_id, new_xp, username=username)
    new_level = get_level(new_xp, guild_id)
    return (new_xp, old_level, new_level, new_level > old_level)

def get_progress(xp, guild_id=None) -> tuple:
    """Return (level, xp_in_level, xp_needed_in_level, percent_0_100)."""
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

# ===== MESSAGE XP (cross-guild aggregates for the general Dashboard) =====
def get_global_xp_stats():
    """Cross-guild aggregates deduplicated by user_id (the same user in several servers counts once)."""
    conn = get_db()
    c = conn.cursor()
    # Total unique accounts (a distinct user_id = 1 person, no matter how many servers)
    c.execute("SELECT COUNT(DISTINCT user_id) AS n FROM users")
    total_users = c.fetchone()["n"]
    # Cumulative XP = full sum (a user active on 3 servers really does accumulate more XP)
    c.execute("SELECT COALESCE(SUM(xp), 0) AS total_xp FROM users")
    total_xp = c.fetchone()["total_xp"]
    # Average level computed on XP aggregated per user, not per row
    c.execute("""SELECT AVG(lvl_per_user) AS avg_level FROM (
                   SELECT user_id, MAX(level) AS lvl_per_user
                   FROM users GROUP BY user_id
                 )""")
    avg_level = c.fetchone()["avg_level"] or 0
    # Deduplicated top 10: aggregate by user_id (XP sum, max level, last username seen)
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
    """Return {user_id: emoji} for a server."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, emoji FROM reactions WHERE guild_id = ?", (str(guild_id),))
    rows = c.fetchall()
    conn.close()
    return {int(r["user_id"]): r["emoji"] for r in rows}

def get_all_reactions_index():
    """Return {(guild_id, user_id): emoji} for the bot (initial load)."""
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
    base = (
        "SELECT g.*, (SELECT gm.username FROM guild_members gm "
        " WHERE gm.user_id = g.owner_id LIMIT 1) AS owner_name FROM guilds g "
    )
    if active_only:
        c.execute(base + "WHERE g.active = 1 ORDER BY g.name COLLATE NOCASE")
    else:
        c.execute(base + "ORDER BY g.active DESC, g.name COLLATE NOCASE")
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


# ===== MUSIC - queue & state (DB = source of truth) =====
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
    """Move a track to position 1 (it will be the next one played).
    Shifts the other positions by +1. Returns True if OK."""
    conn = get_db()
    c = conn.cursor()
    # Check that the track exists and belongs to the guild
    row = c.execute(
        "SELECT id FROM music_queue WHERE guild_id = ? AND id = ?",
        (str(guild_id), int(track_id)),
    ).fetchone()
    if not row:
        conn.close()
        return False
    # Current min position (will be our new target pos - 1)
    min_pos = c.execute(
        "SELECT MIN(position) AS p FROM music_queue WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchone()["p"] or 1
    # Put the track at (min_pos - 1) -> it will come out first
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
    """UPSERT by discord_user_id. None fields are ignored (preserve existing)."""
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
    """UPSERT automod config. Accepts a subset of the fields."""
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
    "secret":    0,   # weight 0 = never auto-rolled, owner-give only
}

# Elements (combat). Simple cycle: each element beats the NEXT one and loses to
# the PREVIOUS one (a single weakness). Order = eclat>abysse>fracture>vif>neant>eclat.
CARD_ELEMENTS = ["eclat", "abysse", "fracture", "vif", "neant"]
CARD_ELEMENT_LABELS = {
    "eclat": t("data.element.eclat"), "abysse": t("data.element.abysse"),
    "fracture": t("data.element.fracture"),
    "vif": t("data.element.vif"), "neant": t("data.element.neant"),
}
# Unicode emoji fallback (until the custom support-server emojis are available)
CARD_ELEMENT_EMOJI = {
    "eclat": "🔆", "abysse": "🌊", "fracture": "⛓", "vif": "🩸", "neant": "🕳",
}
# Name of the custom support emoji (looked up by name, else unicode fallback)
CARD_ELEMENT_EMOJI_NAME = {
    "eclat": "elem_eclat", "abysse": "elem_abysse", "fracture": "elem_fracture",
    "vif": "elem_vif", "neant": "elem_neant",
}


def element_matchup(attacker: str, defender: str) -> float:
    """Simple circle: each element beats the NEXT one (+25%) and loses to the
    PREVIOUS one (-20%). A single weakness per element. Neutral otherwise.
    Cycle: eclat > abysse > fracture > vif > neant > eclat."""
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
    """Total rows matching the same filters as card_list_all."""
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


# ===== GLOBAL EVENTS (seasonal or one-off, owner-controlled) =====
# key -> {name, emoji}. A card with cards.event_key = <key> stays ALWAYS
# obtainable; during the matching event its drop rate is boosted (except
# for a user who already owns the card at 5 stars -> normal rate for them).
GLOBAL_EVENTS = {
    "summer":    {"name": t("data.global_event.summer.name"),    "emoji": "☀️",
                  "coin": t("data.global_event.summer.coin"),    "coin_emoji": "🥥"},
    "halloween": {"name": t("data.global_event.halloween.name"), "emoji": "🎃",
                  "coin": t("data.global_event.halloween.coin"), "coin_emoji": "🍬"},
    "noel":      {"name": t("data.global_event.noel.name"),      "emoji": "🎄",
                  "coin": t("data.global_event.noel.coin"),      "coin_emoji": "❄️"},
    "winter":    {"name": t("data.global_event.winter.name"),    "emoji": "❄️",
                  "coin": t("data.global_event.winter.coin"),    "coin_emoji": "🧊"},
    "valentine": {"name": t("data.global_event.valentine.name"), "emoji": "💖",
                  "coin": t("data.global_event.valentine.coin"), "coin_emoji": "💝"},
}


def global_event_get() -> dict:
    """State of the active global event. {key, name, emoji, active, drop_boost}."""
    key = (get_setting("global_event_key", "") or "").strip()
    try:
        boost = float(get_setting("global_event_drop_boost", "2.0") or 2.0)
    except (ValueError, TypeError):
        boost = 1.0
    try:
        rar_boost = float(get_setting("global_event_rarity_boost", "1.0") or 1.0)
    except (ValueError, TypeError):
        rar_boost = 1.0
    meta = GLOBAL_EVENTS.get(key, {})
    return {"key": key, "active": bool(key), "drop_boost": boost,
            "rarity_boost": rar_boost,
            "name": meta.get("name", ""), "emoji": meta.get("emoji", ""),
            "coin": meta.get("coin", t("data.global_event.coin_default")),
            "coin_emoji": meta.get("coin_emoji", "🎟️")}


def global_event_set(key: str, drop_boost=None, rarity_boost=None):
    """Enable/disable the global event. Empty or invalid key = no event."""
    key = (key or "").strip()
    if key and key not in GLOBAL_EVENTS:
        key = ""
    set_setting("global_event_key", key)
    if drop_boost is not None:
        try:
            set_setting("global_event_drop_boost", max(1.0, float(drop_boost)))
        except (ValueError, TypeError):
            pass
    if rarity_boost is not None:
        try:
            set_setting("global_event_rarity_boost", max(1.0, float(rarity_boost)))
        except (ValueError, TypeError):
            pass


def global_event_test_guilds() -> set:
    """Test servers: if not empty, the event is active ONLY on those servers."""
    raw = get_setting("global_event_test_guilds", "") or ""
    return {g.strip() for g in raw.replace(",", " ").split() if g.strip()}


def global_event_for_guild(guild_id) -> dict:
    """Event active FOR this server (handles test mode). active=False if the event
    is in test mode and this server is not in the list."""
    ev = global_event_get()
    if not ev.get("active"):
        return ev
    tg = global_event_test_guilds()
    if tg and str(guild_id) not in tg:
        return {**ev, "active": False}
    return ev


def global_event_card_counts() -> dict:
    """Number of cards tagged per event (for the dashboard)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT event_key, COUNT(*) AS n FROM cards "
                     "WHERE event_key IS NOT NULL AND event_key != '' "
                     "GROUP BY event_key").fetchall()
    conn.close()
    return {r["event_key"]: int(r["n"]) for r in rows}


# ===== EVENT CURRENCY & ECONOMY (tokens, fights, shop) =====
EVENT_DAILY_COINS        = 5     # tokens given by /daily during an event
EVENT_FIGHT_MAX_PER_DAY  = 3     # event fights per day
EVENT_FIGHT_WIN_COINS    = 4     # tokens per fight won
EVENT_FIGHT_ADV_BONUS    = 2     # bonus if elemental advantage
# Shop: ~20 tokens/day (5 daily + 3x5 fights) -> 6 skins at 50 = 300 = ~15 days.
EVENT_SHOP_SKIN_COST     = 50    # cost of an alt skin (all the same price)
EVENT_SHOP_ROLL_COST     = 8     # cost of 1 free roll
EVENT_SHOP_GOLDEN_COST   = 20    # cost of 1 golden roll (guaranteed legendary)
EVENT_SHOP_ESS10_COST    = 15    # cost of the essence bonus for 1 day (cumulative)
EVENT_SHOP_ESS10_PCT     = 5     # +5% essences/day per purchase


def _ensure_event_econ_tables():
    conn = get_db(); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS event_wallet (
        user_id   TEXT NOT NULL,
        event_key TEXT NOT NULL,
        coins     INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, event_key)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS event_fight_daily (
        user_id   TEXT NOT NULL,
        event_key TEXT NOT NULL,
        day       TEXT NOT NULL,
        used      INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, event_key, day)
    )''')
    # Alt skins unlocked per user (event shop purchase).
    c.execute('''CREATE TABLE IF NOT EXISTS event_skin (
        user_id    TEXT NOT NULL,
        card_id    INTEGER NOT NULL,
        bought_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, card_id)
    )''')
    conn.commit(); conn.close()


def card_alt_set(card_id, alt_url):
    """Owner: set the alt skin URL of a card (or None to remove it)."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE cards SET alt_image_url = ? WHERE id = ?",
              (alt_url or None, int(card_id)))
    conn.commit(); conn.close()


def event_skin_has(user_id, card_id) -> bool:
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM event_skin WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id))).fetchone()
    conn.close()
    return bool(r)


def event_skin_owned_set(user_id) -> set:
    """Set of card_ids for which this user unlocked the alt skin."""
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT card_id FROM event_skin WHERE user_id = ?",
                     (str(user_id),)).fetchall()
    conn.close()
    return {int(r["card_id"]) for r in rows}


def event_skin_grant(user_id, card_id):
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO event_skin (user_id, card_id) VALUES (?, ?)",
              (str(user_id), int(card_id)))
    conn.commit(); conn.close()


def event_shop_skins(user_id, event_key) -> list:
    """All event cards WITH an alt skin. Buyable even without owning the
    card (the arts are only obtainable during the event, the cards remain)."""
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT cards.id, cards.name, cards.rarity, "
        "  (SELECT 1 FROM event_skin es WHERE es.user_id = ? AND es.card_id = cards.id) AS owned_skin "
        "FROM cards WHERE event_key = ? AND alt_image_url IS NOT NULL AND alt_image_url != '' "
        "ORDER BY name COLLATE NOCASE",
        (str(user_id), event_key)).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "rarity": r["rarity"],
             "owned_skin": bool(r["owned_skin"])} for r in rows]


def event_coins_get(user_id, event_key) -> int:
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT coins FROM event_wallet WHERE user_id = ? AND event_key = ?",
                  (str(user_id), event_key)).fetchone()
    conn.close()
    return int(r["coins"]) if r else 0


def event_coins_add(user_id, event_key, n) -> int:
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO event_wallet (user_id, event_key, coins) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, event_key) DO UPDATE SET coins = coins + ?",
              (str(user_id), event_key, int(n), int(n)))
    conn.commit()
    r = c.execute("SELECT coins FROM event_wallet WHERE user_id = ? AND event_key = ?",
                  (str(user_id), event_key)).fetchone()
    conn.close()
    return int(r["coins"]) if r else 0


def event_coins_set(user_id, event_key, n):
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO event_wallet (user_id, event_key, coins) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, event_key) DO UPDATE SET coins = excluded.coins",
              (str(user_id), event_key, max(0, int(n))))
    conn.commit(); conn.close()


def event_coins_spend(user_id, event_key, n) -> bool:
    """Spend n tokens if the balance is enough. Returns True if ok."""
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE event_wallet SET coins = coins - ? "
              "WHERE user_id = ? AND event_key = ? AND coins >= ?",
              (int(n), str(user_id), event_key, int(n)))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


def event_fight_used(user_id, event_key) -> int:
    """Event fights already done today (Paris time)."""
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT used FROM event_fight_daily "
                  "WHERE user_id = ? AND event_key = ? AND day = ?",
                  (str(user_id), event_key, _today_str())).fetchone()
    conn.close()
    return int(r["used"]) if r else 0


def event_fight_inc(user_id, event_key):
    _ensure_event_econ_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO event_fight_daily (user_id, event_key, day, used) "
              "VALUES (?, ?, ?, 1) "
              "ON CONFLICT(user_id, event_key, day) DO UPDATE SET used = used + 1",
              (str(user_id), event_key, _today_str()))
    conn.commit(); conn.close()


def essence_bonus_add(user_id, pct) -> int:
    """Add (cumulative) an essence % bonus for the day. Returns the total."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO essence_bonus_daily (user_id, day, pct) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, day) DO UPDATE SET pct = pct + ?",
              (str(user_id), _today_str(), int(pct), int(pct)))
    conn.commit()
    r = c.execute("SELECT pct FROM essence_bonus_daily WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return int(r["pct"]) if r else 0


def card_roll_random(universe: str | None = None, user_id=None, guild_id=None):
    """Draw a card according to the rarity weights.
    If universe is provided: filter on that category only.
    Active global event: (1) GENERAL BOOST of rares (epic/legendary/mythic more
    frequent) AND (2) cards tagged to the event get a boosted drop -- except for
    a user who already owns the card at 5 stars. The boost only applies on test
    servers if the event is in test mode.
    Returns None if the cards table (or the category) is empty."""
    # Event active for this context (test mode handled)
    ev = (get_setting("global_event_key", "") or "").strip()
    if ev:
        _tg = global_event_test_guilds()
        if _tg and str(guild_id) not in _tg:
            ev = ""
    # Rarity weights (+ general boost of rares during the event)
    weights = dict(_ROLL_WEIGHTS)
    if ev:
        try:
            rar_boost = float(get_setting("global_event_rarity_boost", "1.0") or 1.0)
        except (ValueError, TypeError):
            rar_boost = 1.0
        if rar_boost > 1:
            for _r in ("epic", "legendary", "mythic"):
                if _r in weights:
                    weights[_r] = weights[_r] * rar_boost
    rarity = _rd_cards.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
    conn = get_db(); c = conn.cursor()
    if universe:
        rows = c.execute("SELECT * FROM cards WHERE rarity = ? AND universe = ? "
                          "AND COALESCE(not_obtainable, 0) = 0",
                          (rarity, universe)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM cards WHERE universe = ? "
                              "AND COALESCE(not_obtainable, 0) = 0 "
                              "ORDER BY RANDOM() LIMIT 1", (universe,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM cards WHERE rarity = ? "
                          "AND COALESCE(not_obtainable, 0) = 0",
                          (rarity,)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM cards WHERE COALESCE(not_obtainable, 0) = 0 "
                              "ORDER BY RANDOM() LIMIT 1").fetchall()
    if not rows:
        conn.close()
        return None
    # Per-CARD event boost: weights the cards tagged to the event (except those
    # THIS user already maxed at 5 stars -> normal rate for them). ev already computed.
    if ev:
        try:
            boost = float(get_setting("global_event_drop_boost", "2.0") or 2.0)
        except (ValueError, TypeError):
            boost = 1.0
        if boost > 1 and any((r["event_key"] or "") == ev for r in rows):
            maxed = set()
            if user_id:
                mrows = c.execute(
                    "SELECT card_id FROM card_customizations "
                    "WHERE user_id = ? AND fusion_level >= 5", (str(user_id),)).fetchall()
                maxed = {int(m["card_id"]) for m in mrows}
            weights = [boost if ((r["event_key"] or "") == ev and r["id"] not in maxed)
                       else 1.0 for r in rows]
            conn.close()
            return dict(_rd_cards.choices(rows, weights=weights, k=1)[0])
    conn.close()
    return dict(_rd_cards.choice(rows))


def user_card_add(user_id, card_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_cards (user_id, card_id) VALUES (?, ?)",
              (str(user_id), int(card_id)))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def user_card_add_cheat(user_id, card_id):
    """Owner-cheat add: from_cheat=1 flag -> does not show up in the feed."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_cards (user_id, card_id, from_cheat) VALUES (?, ?, 1)",
              (str(user_id), int(card_id)))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def forced_roll_set(user_id, card_id):
    """Owner cheat: force the card of this user's NEXT /roll."""
    conn = get_db(); c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS forced_roll (user_id TEXT PRIMARY KEY, card_id INTEGER)")
    c.execute("INSERT INTO forced_roll (user_id, card_id) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET card_id = excluded.card_id",
              (str(user_id), int(card_id)))
    conn.commit(); conn.close()


def forced_roll_get(user_id):
    """Return the forced card WITHOUT consuming it (None if there is none)."""
    conn = get_db(); c = conn.cursor()
    try:
        r = c.execute("SELECT card_id FROM forced_roll WHERE user_id = ?",
                      (str(user_id),)).fetchone()
    except Exception:
        conn.close(); return None
    conn.close()
    return int(r["card_id"]) if r else None


def forced_roll_clear(user_id):
    """Consume/clear the forced card."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("DELETE FROM forced_roll WHERE user_id = ?", (str(user_id),))
        conn.commit()
    except Exception as e:
        print(f"[forced_roll_clear] echec suppression user={user_id}: {e!r}")
    conn.close()


def forced_roll_pop(user_id):
    """Return and CONSUME the forced card (None if there is none)."""
    cid = forced_roll_get(user_id)
    if cid is not None:
        forced_roll_clear(user_id)
    return cid


def user_card_add_with_flag(user_id, card_id, not_tradeable=False):
    """Same as user_card_add but with the not_tradeable flag."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_cards (user_id, card_id, not_tradeable) VALUES (?, ?, ?)",
              (str(user_id), int(card_id), 1 if not_tradeable else 0))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def user_card_list(user_id, rarity=None, categorie=None):
    """All the user's copies, joined with the card. ORDER BY rarity desc.
    categorie: optional filter matching the universe OR the origin (subtitle), case-insensitive."""
    conn = get_db(); c = conn.cursor()
    where = "uc.user_id = ?"
    params = [str(user_id)]
    if rarity:
        where += " AND c.rarity = ?"
        params.append(rarity)
    if categorie:
        where += " AND (LOWER(c.universe) = LOWER(?) OR LOWER(c.subtitle) = LOWER(?))"
        params.append(categorie); params.append(categorie)
    # Order by rarity (mythic first), then by card name
    rarity_order = ("CASE c.rarity "
                    "WHEN 'mythic' THEN 0 "
                    "WHEN 'legendary' THEN 1 "
                    "WHEN 'epic' THEN 2 "
                    "WHEN 'rare' THEN 3 "
                    "WHEN 'common' THEN 4 ELSE 5 END")
    rows = c.execute(
        f"SELECT uc.*, c.name, c.universe, c.subtitle, c.rarity, c.image_url, c.element, c.event_key "
        f"FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
        f"WHERE {where} ORDER BY {rarity_order} ASC, c.name ASC", params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_owners_count(card_id):
    """Number of distinct users owning this card."""
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(DISTINCT user_id) AS n FROM user_cards WHERE card_id = ?",
                   (int(card_id),)).fetchone()["n"]
    conn.close()
    return int(n)


def card_owners_list(card_id, limit=50):
    """List of owners with their count. Descending order by count."""
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


def card_suggestion_set_forward(sid, message_id):
    """Store the id of the message forwarded to the support channel (to react on it)."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_suggestions SET forward_message_id = ? WHERE id = ?",
              (str(message_id) if message_id else None, int(sid)))
    conn.commit(); conn.close()


def card_suggestion_get_by_forward(message_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_suggestions WHERE forward_message_id = ?",
                  (str(message_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_suggestion_set_votes(sid, up, down):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_suggestions SET votes_up = ?, votes_down = ? WHERE id = ?",
              (int(up), int(down), int(sid)))
    conn.commit(); conn.close()


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
    """How many copies the user owns of this card.
    only_tradeable=True: excludes the not_tradeable ones (for trade checks)."""
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
    """Transfer ONE copy of a tradeable card. Skips the not_tradeable ones.
    Returns True if OK, False if from_user has no tradeable copy."""
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
    """Delete an event (e.g. message send failed -> no ghost)."""
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
    """List pending events in a channel (for captcha matching)."""
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
    """Atomic claim. Returns True if OK, False if already claimed."""
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


# ===== ESSENCE ECONOMY =====
# Essences earned per /roll depending on the card rarity. Duplicate = x2.
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
    """Add (or remove if negative) essences. Returns the new balance."""
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
    """Debit if the balance is enough. Returns True if OK, False otherwise. Atomic."""
    amount = int(amount)
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_currency SET essences = essences - ?, updated_at = CURRENT_TIMESTAMP "
              "WHERE user_id = ? AND essences >= ?",
              (amount, str(user_id), amount))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


# ===== DAILY WHEEL OF FORTUNE =====
# Rewards: essence % bonus for the day (essence_bonus_daily) OR
# free rolls (via roll_give_user). 1 spin / day / user.
def _today_str():
    """Today's date in FRENCH time (wheel resets at midnight Europe/Paris)."""
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
    c.execute('''CREATE TABLE IF NOT EXISTS daily_booster_claims (
        user_id    TEXT NOT NULL,
        day        TEXT NOT NULL,
        claimed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, day)
    )''')
    # Wheel winnings log (append-only, feeds the "live" feed)
    c.execute('''CREATE TABLE IF NOT EXISTS wheel_wins (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      TEXT NOT NULL,
        reward_type  TEXT,
        reward_value INTEGER,
        won_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit(); conn.close()


def wheel_win_log(user_id, reward_type, reward_value):
    """Add a win to the log (live feed)."""
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
    """Owner: clear the wheel winnings log. Returns the number deleted."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM wheel_wins")
    n = c.rowcount
    conn.commit(); conn.close()
    return int(n)


def daily_roll_claimed_today(user_id) -> bool:
    """True if the user already claimed their free daily roll today (FR)."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM daily_roll_claims WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return r is not None


def daily_roll_grant(user_id) -> bool:
    """Grant 1 free roll for the day. False if already claimed today."""
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


def daily_booster_claimed_today(user_id) -> bool:
    """True if the user already opened their free daily booster today (FR)."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM daily_booster_claims WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return r is not None


def daily_booster_ever_claimed(user_id) -> bool:
    """True if the user already opened at least one booster (any day)."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM daily_booster_claims WHERE user_id = ? LIMIT 1",
                  (str(user_id),)).fetchone()
    conn.close()
    return r is not None


def daily_booster_claim(user_id) -> bool:
    """Mark the daily booster as claimed. False if already done today.
    (Granting the cards is handled by the caller.)"""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO daily_booster_claims (user_id, day) VALUES (?, ?)",
                  (str(user_id), _today_str()))
        conn.commit(); ok = True
    except Exception:
        ok = False
    conn.close()
    return ok


def wheel_claim_today(user_id):
    """Return today's spin (dict) or None if not spun yet."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM wheel_daily WHERE user_id = ? AND day = ?",
                  (str(user_id), _today_str())).fetchone()
    conn.close()
    return dict(r) if r else None


def wheel_record(user_id, reward_type, reward_value) -> bool:
    """Record today's spin. False if already spun today."""
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
    """Owner: reset today's wheel for EVERYONE (everyone can spin again).
    Returns the number of spins cleared."""
    _ensure_wheel_tables()
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM wheel_daily WHERE day = ?", (_today_str(),))
    n = c.rowcount
    conn.commit(); conn.close()
    return int(n)


def essence_bonus_get(user_id) -> int:
    """Essence % bonus active today for this user (0 if none)."""
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
    """Add essences applying today's % bonus. Returns the actual gain."""
    base = int(base_amount)
    pct = essence_bonus_get(user_id)
    gain = base + (base * pct) // 100
    currency_add(user_id, gain)
    return gain


# ===== BORDERS =====
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
    """Add qty copies to the inventory (increments if already present)."""
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
    """True if at least 1 copy is in stock."""
    return user_border_qty(user_id, border_key) > 0


def user_border_consume(user_id, border_key) -> bool:
    """Remove 1 copy from stock. True if OK, False if the stock is empty. Atomic."""
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
    """Owner: remove qty copies (or all of them if qty is None)."""
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
    """Inventory: borders in stock (qty > 0)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT ub.border_key, ub.qty, ub.acquired_at, b.name, b.filename "
        "FROM user_borders ub JOIN borders b ON b.border_key = ub.border_key "
        "WHERE ub.user_id = ? AND ub.qty > 0 ORDER BY ub.acquired_at DESC",
        (str(user_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_card_customizations_map(user_id):
    """Return {card_id: border_key} for the cards customized by the user."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT card_id, border_key FROM card_customizations "
        "WHERE user_id = ? AND border_key IS NOT NULL", (str(user_id),)).fetchall()
    conn.close()
    return {int(r["card_id"]): r["border_key"] for r in rows}


# ===== FUSION (star prestige) =====
def card_fusion_get(user_id, card_id) -> int:
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT fusion_level FROM card_customizations WHERE user_id = ? AND card_id = ?",
                  (str(user_id), int(card_id))).fetchone()
    conn.close()
    return int(r["fusion_level"]) if r and r["fusion_level"] else 0


def card_fusion_set(user_id, card_id, level):
    """Upsert the fusion level (stars) of a card for a user."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_customizations (user_id, card_id, fusion_level, updated_at) "
              "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
              "ON CONFLICT(user_id, card_id) DO UPDATE SET fusion_level = excluded.fusion_level, "
              "updated_at = CURRENT_TIMESTAMP",
              (str(user_id), int(card_id), int(level)))
    conn.commit(); conn.close()


def user_card_fusion_map(user_id):
    """Return {card_id: fusion_level} for the cards having >=1 star."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT card_id, fusion_level FROM card_customizations "
        "WHERE user_id = ? AND fusion_level > 0", (str(user_id),)).fetchall()
    conn.close()
    return {int(r["card_id"]): int(r["fusion_level"]) for r in rows}


def user_card_set_not_tradeable(user_id, card_id, value=1):
    """Mark all copies of a card owned by a user as (non) tradeable."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_cards SET not_tradeable = ? WHERE user_id = ? AND card_id = ?",
              (1 if value else 0, str(user_id), int(card_id)))
    conn.commit(); conn.close()


def user_card_lock_one(user_id, card_id):
    """Lock ONE single copy (the card carrying the stars). The extra duplicates
    stay tradeable/recyclable. No-op if a copy is already locked."""
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
    """Delete n copies (rows) of a card for a user. Returns the number actually
    deleted. Does not check the 'keep' (to be done by the caller)."""
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


# Profile color palette. lvl = required GUILD level (one unlocked every 5).
PROFILE_COLORS = [
    {"key": "lime",   "name": t("data.profile_color.lime"),   "hex": 0xB9F23A, "lvl": 0},
    {"key": "rouge",  "name": t("data.profile_color.rouge"),  "hex": 0xFF4D4D, "lvl": 5},
    {"key": "bleu",   "name": t("data.profile_color.bleu"),   "hex": 0x4C8DFF, "lvl": 10},
    {"key": "vert",   "name": t("data.profile_color.vert"),   "hex": 0x4ADE80, "lvl": 15},
    {"key": "jaune",  "name": t("data.profile_color.jaune"),  "hex": 0xF2D23A, "lvl": 20},
    {"key": "violet", "name": t("data.profile_color.violet"), "hex": 0xA86DFF, "lvl": 25},
    {"key": "rose",   "name": t("data.profile_color.rose"),   "hex": 0xFF5FA2, "lvl": 30},
    {"key": "orange", "name": t("data.profile_color.orange"), "hex": 0xFFA726, "lvl": 35},
    {"key": "cyan",   "name": t("data.profile_color.cyan"),   "hex": 0x4DD0E1, "lvl": 40},
    {"key": "blanc",  "name": t("data.profile_color.blanc"),  "hex": 0xECEFF4, "lvl": 45},
]


def profile_color_hex(key, default=0xB9F23A):
    for c in PROFILE_COLORS:
        if c["key"] == key:
            return c["hex"]
    return default


def card_profile_get(user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT left_id, mid_id, right_id, color FROM card_profile WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_profile_set_color(user_id, color):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_profile (user_id, color, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
              "ON CONFLICT(user_id) DO UPDATE SET color = excluded.color, updated_at = CURRENT_TIMESTAMP",
              (str(user_id), color))
    conn.commit(); conn.close()


def guild_set_color(gid, color):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_guild SET color = ? WHERE id = ?", (color, int(gid)))
    conn.commit(); conn.close()


def guild_set_emblem(gid, emblem):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_guild SET emblem = ? WHERE id = ?", (emblem, int(gid)))
    conn.commit(); conn.close()


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
    """Return {rarity: copy_count} for a user."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT c.rarity AS rarity, COUNT(*) AS n FROM user_cards uc "
        "JOIN cards c ON c.id = uc.card_id WHERE uc.user_id = ? GROUP BY c.rarity",
        (str(user_id),)).fetchall()
    conn.close()
    return {r["rarity"]: int(r["n"]) for r in rows}


def all_card_origins():
    """All origins (subtitle) in the catalog + number of (unique) cards."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT subtitle AS origin, COUNT(*) AS n FROM cards "
        "WHERE subtitle IS NOT NULL AND subtitle != '' "
        "GROUP BY subtitle ORDER BY subtitle COLLATE NOCASE").fetchall()
    conn.close()
    return [(r["origin"], int(r["n"])) for r in rows]


def user_collection_origins(user_id):
    """Origins (subtitle) present in a user's collection + number of unique cards."""
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
    """Return {rarity: unique_card_count} (distinct) for a user."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT c.rarity AS rarity, COUNT(DISTINCT uc.card_id) AS n FROM user_cards uc "
        "JOIN cards c ON c.id = uc.card_id WHERE uc.user_id = ? GROUP BY c.rarity",
        (str(user_id),)).fetchall()
    conn.close()
    return {r["rarity"]: int(r["n"]) for r in rows}


# ===== COMBAT STATS (player): derived from unique cards weighted by rarity =====
PLAYER_HP_BASE = 50
PLAYER_ATK_BASE = 25
# Big numbers = more impressive. Target: rare card ~500 HP / 270 ATK.
PLAYER_HP_WEIGHTS = {
    "common": 300, "rare": 500, "epic": 900, "legendary": 1800, "mythic": 4000, "secret": 6000,
}
PLAYER_ATK_WEIGHTS = {
    "common": 150, "rare": 270, "epic": 500, "legendary": 1000, "mythic": 2200, "secret": 3500,
}


# Collection soft cap: past SOFT_T owned cards (total), each extra card
# only counts for SOFT_DECAY. Smooths the gap between players and avoids
# absurd number inflation. (Does NOT affect difficulty because the boss
# scales on that same value - see team_scaled_boss_stats.)
COLLECTION_SOFT_T = 3000
COLLECTION_SOFT_DECAY = 0.7


def _collection_soft_factor(total_cards):
    n = int(total_cards or 0)
    if n <= COLLECTION_SOFT_T:
        return 1.0
    eff = COLLECTION_SOFT_T + (n - COLLECTION_SOFT_T) * COLLECTION_SOFT_DECAY
    return eff / n


# Fusion bonus (% on base HP+ATK). Linear 1%/star up to 15, then a
# logarithmic curve with diminishing returns, with NO cap (slope continuous at 15).
FUSION_CURVE_KNEE = 15
FUSION_CURVE_A = 30.0


def fusion_bonus_pct(stars) -> float:
    """Return the fusion bonus in PERCENT (e.g. 23.4) for a total number of stars."""
    import math
    s = max(0, int(stars))
    if s <= FUSION_CURVE_KNEE:
        return float(s)
    return FUSION_CURVE_KNEE + FUSION_CURVE_A * math.log(
        1 + (s - FUSION_CURVE_KNEE) / FUSION_CURVE_A)


def compute_player_combat_stats(user_id):
    """BASE HP + ATK of a player based on their UNIQUE cards weighted by rarity,
    + bonus from fusion stars (fusion_bonus_pct curve, no cap).
    This is the 'collection' baseline (rewards playtime). The ENGAGED card
    then applies a multiplier (see engaged_combat_stats).
    Returns {hp, atk, unique_total, stars, bonus_pct}."""
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


# ATK multiplier based on the RARITY of the card engaged in combat.
# Creates the trade-off "card that counters the element (weak)" vs "big card (neutral)".
# IMPORTANT: applies to ATK ONLY. HP comes from the collection (= your
# depth of play = your wall), otherwise the big card would be both
# tankier AND stronger, and the elemental counter would never be worth it.
# Ancre : epic = 1.0. common contre-element (0.8 x1.25 = 1.0) ~ epic neutre.
CARD_RARITY_COMBAT_MULT = {
    "common": 0.80, "rare": 0.92, "epic": 1.05,
    "legendary": 1.25, "mythic": 1.55, "secret": 2.50,
}

# Combat power (flashy display) = HP + ATK x weight. Capped at 999999999999999.
COMBAT_POWER_ATK_WEIGHT = 2
COMBAT_POWER_MAX = 999999999999999


def combat_power(hp, atk) -> int:
    p = int(hp) + int(atk) * COMBAT_POWER_ATK_WEIGHT
    return max(0, min(COMBAT_POWER_MAX, p))
# +20%/star (cap 5 = +100%, x2.0). FUSION is the real power axis (rewards
# investment) rather than roll luck. So a common 5* (0.80x2.0=1.60)
# beats a raw mythic (1.55). The 0* values do not change -> boss balance preserved.
CARD_STAR_COMBAT_BONUS = 0.20


def engaged_combat_stats(user_id, card_id):
    """REAL combat stats. HP = collection baseline (unchanged). ATK = baseline x
    modifier of the engaged card (rarity + fusion stars of THAT card).
    Returns {hp, atk, mult, rarity}."""
    base = compute_player_combat_stats(user_id)
    card = card_get(int(card_id)) if card_id else None
    rar = (card or {}).get("rarity")
    rar_mult = CARD_RARITY_COMBAT_MULT.get(rar, 1.0)
    stars = int(card_fusion_get(user_id, int(card_id))) if card_id else 0
    star_mult = 1.0 + min(5, stars) * CARD_STAR_COMBAT_BONUS
    mult = rar_mult * star_mult
    # Secret card at 5 stars: ultimate multiplier (rewards maxing the fusion
    # of a secret, the hardest rarity to obtain).
    secret_max = (rar == "secret" and stars >= 5)
    if secret_max:
        mult = 999.0
    return {
        "secret_max": secret_max,
        "hp": max(1, int(base["hp"])),
        "atk": max(1, int(base["atk"] * mult)),
        "mult": mult,
        "rarity": rar,
        "rar_mult": rar_mult,
        "stars": stars,
        "star_mult": star_mult,
    }


# ===== ROLL CHARGES (multi-roll per hour, per server) =====
import time as _roll_time


def roll_events_count(user_id, guild_id, window_sec=3600) -> int:
    """Number of 'normal' (rechargeable) rolls consumed within the window."""
    cutoff = _roll_time.time() - window_sec
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM roll_events "
                  "WHERE user_id = ? AND guild_id = ? AND rolled_at > ?",
                  (str(user_id), str(guild_id), cutoff)).fetchone()["n"]
    conn.close()
    return int(n)


def roll_events_oldest_ts(user_id, guild_id, window_sec=3600):
    """Epoch timestamp of the oldest roll still inside the window (or None)."""
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
    # purge old events (> 2h) so the table does not grow
    c.execute("DELETE FROM roll_events WHERE rolled_at < ?", (_roll_time.time() - 7200,))
    conn.commit(); conn.close()


def roll_total_inc(user_id, n=1):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_roll_total (user_id, total) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET total = total + ?",
              (str(user_id), int(n), int(n)))
    conn.commit(); conn.close()


def roll_total_get(user_id) -> int:
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT total FROM card_roll_total WHERE user_id = ?",
                  (str(user_id),)).fetchone()
    conn.close()
    return int(r["total"]) if r else 0


def roll_solo_guild_has(user_id, guild_id) -> bool:
    """True if this user already rolled in this solo server (already counted)."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM card_roll_solo_guild WHERE user_id = ? AND guild_id = ?",
                  (str(user_id), str(guild_id))).fetchone()
    conn.close()
    return bool(r)


def roll_solo_guild_count(user_id) -> int:
    """Number of distinct solo servers where this user already rolled."""
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM card_roll_solo_guild WHERE user_id = ?",
                  (str(user_id),)).fetchone()["n"]
    conn.close()
    return int(n)


def roll_solo_guild_add(user_id, guild_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO card_roll_solo_guild (user_id, guild_id) VALUES (?, ?)",
              (str(user_id), str(guild_id)))
    conn.commit(); conn.close()


def roll_events_reset_all() -> int:
    """Owner: reset every roll cooldown (everyone can roll again)."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM roll_events")
    n = c.rowcount
    conn.commit(); conn.close()
    return n


# ===== BONUS ROLLS (rolls gifted by the owner, not rechargeable) =====
# Available = unconsumed share of the global grant + individual credits.
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
    """Consume 1 bonus roll (global grant first, then credits). True if available."""
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
    """Owner: give n bonus rolls to ONE user (individual credits). Returns availability."""
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_grant_state (user_id, credits) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET credits = COALESCE(credits,0) + excluded.credits",
              (str(user_id), int(n)))
    conn.commit(); conn.close()
    return roll_bonus_available(user_id)


def roll_set_user(user_id, n):
    """Owner: set the EXACT number of bonus rolls available for a user.
    We cancel their share of the global grant (consumed = grant) and set credits = n."""
    grant = int(get_setting("roll_global_grant", "0") or 0)
    n = max(0, int(n))
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_grant_state (user_id, consumed, credits) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET consumed = ?, credits = ?",
              (str(user_id), grant, n, grant, n))
    conn.commit(); conn.close()


def roll_reset_user_cooldown(user_id) -> int:
    """Owner: reset the roll cooldown of ONE user (all servers)."""
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM roll_events WHERE user_id = ?", (str(user_id),))
    n = c.rowcount
    conn.commit(); conn.close()
    return n


def roll_reset_user_grant(user_id):
    """Owner: remove the bonus rolls of ONE user (credits 0 + realigned on the global grant)."""
    grant = int(get_setting("roll_global_grant", "0") or 0)
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO roll_grant_state (user_id, consumed, credits) VALUES (?, ?, 0) "
              "ON CONFLICT(user_id) DO UPDATE SET consumed = ?, credits = 0",
              (str(user_id), grant, grant))
    conn.commit(); conn.close()


def roll_grant_give_all(n: int) -> int:
    """Owner: give n bonus rolls to everyone (cumulative grant). Returns the new grant."""
    grant = int(get_setting("roll_global_grant", "0") or 0) + int(n)
    set_setting("roll_global_grant", grant)
    return grant


def roll_grant_reset():
    """Reset the grant and the consumption to zero (removes bonus rolls for everyone)."""
    set_setting("roll_global_grant", 0)
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM roll_grant_state")
    conn.commit(); conn.close()


# ===== BOSS FIGHT =====
# Boss stats depending on the tier (1-5)
# Balance (engaged card x rarity from now on). Player reference:
#   base collection ATK ~ 60k (average) to ~100k (heavy roller), x card 0.8..1.55,
#   x matchup 0.8..1.25. T1 soloable by a big player, T3 requires a real team.
BOSS_TIERS = {
    1: {"hp": 550000,   "atk": 7000,   "label": "Tier 1"},
    2: {"hp": 1250000,  "atk": 12000,  "label": "Tier 2"},
    3: {"hp": 2400000,  "atk": 15000,  "label": "Tier 3"},
    4: {"hp": 3600000,  "atk": 17000,  "label": "Tier 4"},
    5: {"hp": 4600000,  "atk": 20000,  "label": "Tier 5"},
}

# Anti-powercreep scaling: when the fight starts, the boss HP/ATK are
# recomputed from the REAL power of the team present.
#   boss HP  = HP_FACTOR[tier] x sum(base ATK of the team)
#   boss ATK = ATK_FACTOR[tier] x (average base HP of the team)
# The base = collection baseline (without card/star/element/aptitude mult), so
# fusing/countering/bringing a high rarity stays an UNBUDGETED advantage = you
# win. Rolling more grows the boss accordingly => never trivial.
# Factors calibrated to reproduce the reference balance (see BOSS_TIERS).
BOSS_TIER_SCALE = {
    1: {"hp": 3.0,  "atk": 0.06},
    2: {"hp": 5.5,  "atk": 0.10},
    3: {"hp": 7.9,  "atk": 0.13},
    4: {"hp": 11.8, "atk": 0.15},
    5: {"hp": 15.1, "atk": 0.17},
}


def card_boss_set_stats(boss_id, max_hp, atk):
    """Set the boss HP (= max and current) and ATK (scaling on the team at launch)."""
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
    """Remove n units if available (atomic). True if consumed."""
    _ensure_user_items()
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE user_items SET qty = qty - ? WHERE user_id = ? AND item_key = ? AND qty >= ?",
              (int(n), str(user_id), item_key, int(n)))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


def user_item_set(user_id, item_key, qty):
    """Owner: set the exact quantity of an item."""
    _ensure_user_items()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO user_items (user_id, item_key, qty) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, item_key) DO UPDATE SET qty = excluded.qty",
              (str(user_id), item_key, max(0, int(qty))))
    conn.commit(); conn.close()


def currency_set(user_id, amount):
    """Owner: set the exact essence balance."""
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
    """Return the elements that have the advantage against `element` (that beat it)."""
    return [e for e in CARD_ELEMENTS if element_matchup(e, element) > 1.0]


def card_boss_get(boss_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_boss WHERE id = ?", (int(boss_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def card_boss_list_active():
    """Boss still running (recruiting or fighting), for resuming at boot."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM card_boss WHERE status IN ('recruiting','fighting') "
                     "ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def card_boss_guild_has_active(guild_id) -> bool:
    """True if this server already has a boss recruiting or fighting."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM card_boss WHERE guild_id = ? "
                  "AND status IN ('recruiting','fighting') LIMIT 1",
                  (str(guild_id),)).fetchone()
    conn.close()
    return r is not None


def boss_chat_add(boss_id, user_id, name, text):
    import time as _time
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_boss_chat (boss_id, user_id, name, text, ts) VALUES (?, ?, ?, ?, ?)",
              (int(boss_id), str(user_id), str(name)[:40], str(text)[:300], _time.time()))
    conn.commit()
    cid = c.lastrowid
    # keep the last 200 messages per boss
    c.execute("DELETE FROM card_boss_chat WHERE boss_id = ? AND id NOT IN "
              "(SELECT id FROM card_boss_chat WHERE boss_id = ? ORDER BY id DESC LIMIT 200)",
              (int(boss_id), int(boss_id)))
    conn.commit(); conn.close()
    return cid


def boss_chat_recent(boss_id, after_id=0, limit=80):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT id, user_id, name, text, ts FROM card_boss_chat "
                     "WHERE boss_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                     (int(boss_id), int(after_id), int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def boss_auto_get_next(guild_id):
    """Timestamp (epoch) of the next automatic spawn for this server, or None."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT next_at FROM boss_auto_schedule WHERE guild_id = ?",
                  (str(guild_id),)).fetchone()
    conn.close()
    return float(r["next_at"]) if r and r["next_at"] is not None else None


def boss_auto_set_next(guild_id, next_at):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO boss_auto_schedule (guild_id, next_at) VALUES (?, ?) "
              "ON CONFLICT(guild_id) DO UPDATE SET next_at = excluded.next_at",
              (str(guild_id), float(next_at)))
    conn.commit(); conn.close()


def max_guild_level_for_users(user_ids) -> int:
    """Highest card-guild level among these users (0 if none of them is in a
    guild). Used to calibrate the tier of an automatic boss on the server strength."""
    uids = [str(u) for u in user_ids]
    if not uids:
        return 0
    conn = get_db(); c = conn.cursor()
    best = 0
    # chunks to stay under the SQLite variable limit
    for i in range(0, len(uids), 400):
        chunk = uids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        r = c.execute(f"SELECT MAX(g.level) AS lv FROM card_guild g "
                      f"JOIN card_guild_member m ON m.guild_id = g.id "
                      f"WHERE m.user_id IN ({ph})", chunk).fetchone()
        if r and r["lv"]:
            best = max(best, int(r["lv"]))
    conn.close()
    return best


def avg_guild_level_for_users(user_ids) -> int:
    """AVERAGE card-guild level of the members who are in a guild
    (each member counts for the level of their best guild; members without a
    guild are ignored). Returns 0 if nobody is in a guild. Used to calibrate the
    tier of an automatic boss on the TYPICAL server strength (not just the whale)."""
    uids = [str(u) for u in user_ids]
    if not uids:
        return 0
    conn = get_db(); c = conn.cursor()
    per_user = []
    for i in range(0, len(uids), 400):
        chunk = uids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        rows = c.execute(f"SELECT m.user_id AS uid, MAX(g.level) AS lv FROM card_guild g "
                         f"JOIN card_guild_member m ON m.guild_id = g.id "
                         f"WHERE m.user_id IN ({ph}) GROUP BY m.user_id", chunk).fetchall()
        per_user.extend(int(r["lv"]) for r in rows if r["lv"])
    conn.close()
    if not per_user:
        return 0
    return int(round(sum(per_user) / len(per_user)))


def avg_combat_power_for_users(user_ids) -> int:
    """Average REAL combat power (combat_power = HP + ATK*weight, computed from
    compute_player_combat_stats) of the members owning at least one card.
    0 if nobody has a card. Used to gate the spawn of the hardest avatars
    (secret) on the real combat strength of the server, not the tier level."""
    uids = [str(u) for u in user_ids]
    if not uids:
        return 0
    powers = []
    for u in uids:
        st = compute_player_combat_stats(u)
        if st.get("unique_total", 0) > 0:
            powers.append(combat_power(st["hp"], st["atk"]))
    if not powers:
        return 0
    return int(round(sum(powers) / len(powers)))


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
    """Remove dmg from the boss (atomic). Returns the remaining HP (>=0)."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_boss SET hp = MAX(0, hp - ?) WHERE id = ?", (int(dmg), int(boss_id)))
    r = c.execute("SELECT hp FROM card_boss WHERE id = ?", (int(boss_id),)).fetchone()
    conn.commit(); conn.close()
    return int(r["hp"]) if r else 0


def card_boss_heal(boss_id, amount) -> int:
    """Heal the boss (capped at max_hp). Returns the HP after healing."""
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


def boss_event_add(boss_id, etype, data=None):
    """Record a combat event for the live dashboard."""
    import json as _json, time as _time
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_boss_event (boss_id, etype, data, ts) VALUES (?, ?, ?, ?)",
              (int(boss_id), str(etype), _json.dumps(data or {}), _time.time()))
    conn.commit(); conn.close()


def boss_events_since(boss_id, after_id=0, limit=200):
    """Boss events with id > after_id, chronological order."""
    import json as _json
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT id, etype, data, ts FROM card_boss_event "
                     "WHERE boss_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                     (int(boss_id), int(after_id), int(limit))).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            d = _json.loads(r["data"] or "{}")
        except Exception:
            d = {}
        out.append({"id": r["id"], "type": r["etype"], "data": d, "ts": r["ts"]})
    return out


def boss_participant_add(boss_id, user_id, name, element, hp, atk, card_id=None) -> bool:
    """Add a participant. False if already present."""
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
                              element=None, card_id=None, aptitude=None, atk=None, max_hp=None,
                              add_heal=None, add_taken=None, add_taken_raw=None, died=None):
    conn = get_db(); c = conn.cursor()
    sets, vals = [], []
    if add_taken_raw is not None:
        sets.append("taken_raw = COALESCE(taken_raw,0) + ?"); vals.append(int(add_taken_raw))
    if died is not None:
        sets.append("died = ?"); vals.append(1 if died else 0)
    if hp is not None:
        sets.append("hp = ?"); vals.append(max(0, int(hp)))
    if max_hp is not None:
        sets.append("max_hp = ?"); vals.append(max(1, int(max_hp)))
    if atk is not None:
        sets.append("atk = ?"); vals.append(max(1, int(atk)))
    if add_damage is not None:
        sets.append("damage = damage + ?"); vals.append(int(add_damage))
    if add_heal is not None:
        sets.append("heal = COALESCE(heal,0) + ?"); vals.append(int(add_heal))
    if add_taken is not None:
        sets.append("taken = COALESCE(taken,0) + ?"); vals.append(int(add_taken))
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
    """Add if missing, remove if present. Returns True if added, False if removed."""
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
        "SELECT w.card_id, w.added_at, c.name, c.rarity, c.universe, c.image_url "
        "FROM card_wishlist w JOIN cards c ON c.id = w.card_id "
        "WHERE w.user_id = ? ORDER BY c.name", (str(user_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wishlist_users_for_card(card_id, exclude_user=None):
    """List of user_ids that have this card in their wishlist (excluding exclude_user)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT user_id FROM card_wishlist WHERE card_id = ?",
                     (int(card_id),)).fetchall()
    conn.close()
    out = [r["user_id"] for r in rows]
    if exclude_user is not None:
        out = [u for u in out if u != str(exclude_user)]
    return out


# ===== LEADERBOARDS (cards) =====
LEADERBOARD_RARITY_POINTS = {
    "common": 1, "rare": 2, "epic": 5, "legendary": 25, "mythic": 100, "secret": 200,
}


def leaderboard_card_aggregates():
    """Per user: {user_id: {total, pts, mythic}}. Over the whole database."""
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
    """Top users by number of fused cards (>=1 star) + total stars."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT user_id, COUNT(*) AS cards, SUM(fusion_level) AS stars "
        "FROM card_customizations WHERE fusion_level > 0 "
        "GROUP BY user_id ORDER BY stars DESC, cards DESC LIMIT ?",
        (int(limit),)).fetchall()
    conn.close()
    return [(r["user_id"], int(r["cards"]), int(r["stars"] or 0)) for r in rows]


# Essences refunded when recycling a duplicate (~50% of the roll gain)
ESSENCE_RECYCLE = {
    "common":    6,
    "rare":      14,
    "epic":      32,
    "legendary": 110,
    "mythic":    325,
    "secret":    500,
}

# Duplicate cost to go from star level L to L+1 (index = current level)
FUSION_STAR_COSTS = [2, 3, 4, 5, 6]  # 20 duplicates total for 5 stars
FUSION_MAX_STARS = 5

# Tier-up (/cardup): consumes N duplicates of a rarity -> 1 card of the rarity above
CARDUP_NEXT = {"common": "rare", "rare": "epic", "epic": "legendary", "legendary": "mythic"}
CARDUP_COST = {"common": 5, "rare": 5, "epic": 5, "legendary": 5}


def user_duplicate_count_by_rarity(user_id, rarity) -> int:
    """Number of extra copies (beyond 1 per card) of this rarity, ONLY
    for the cards already maxed (fusion 5 stars)."""
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
    """Delete n extra copies (keeps 1 per card) of this rarity, ONLY
    on maxed cards (5 stars). Keeps the starred copy. Returns the number deleted."""
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
    # Per card: keep the first one (locked one in priority), the rest = deletable
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
    """Random obtainable card of an exact rarity. Optional element filter."""
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
    """Update the slot. Every field present in fields is written, including
    None/empty (allows clearing a slot). Only the keys not listed are ignored."""
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
    """Draw a random card among those >= min_rarity (skips not_obtainable + secret)."""
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
    """offer_items / request_items: list[(card_id, qty)].
    Returns trade_id."""
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
            "SELECT ti.*, c.name, c.rarity, c.universe, c.subtitle, c.image_url "
            "FROM card_trade_items ti JOIN cards c ON c.id = ti.card_id "
            "WHERE ti.trade_id = ? AND ti.side = ?",
            (int(trade_id), side)).fetchall()
    else:
        rows = c.execute(
            "SELECT ti.*, c.name, c.rarity, c.universe, c.subtitle, c.image_url "
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
    """Return last_roll_at ISO or None for (user, guild)."""
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


# ===== Cards: per-guild config (required channel) =====
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
    # sentinel ... -> do not touch; None -> clear the role
    if ping_role_id is not ...:
        fields.append("ping_role_id = ?"); values.append(str(ping_role_id) if ping_role_id else None)
    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        c.execute(f"UPDATE guild_card_config SET {', '.join(fields)} WHERE guild_id = ?",
                  (*values, str(guild_id)))
    conn.commit(); conn.close()


# ===== Dashboard notifications helpers =====
def dash_notif_add(user_id, type_, title, message=None, link_url=None, guild_id=None):
    """Create a notification for a user. Returns the id."""
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
    """Mark as read. If notif_id is None: mark all of them."""
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
    """Global purge of notifications older than N days (daily cron)."""
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
    """Delete if it belongs to the user (anti hijack)."""
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
    """Mark the profile as applied. If applied_by is provided, trace it
    so it can be revoked automatically when the user's TookBot+ expires."""
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
    """For the re-apply at boot: returns every registered profile."""
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM guild_bot_profile")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows


def service_uptime_log(component: str, ok: bool):
    """Record an uptime check for a component in the current hour bucket.

    UPSERT by (component, hour_bucket). We keep total checks + oks to compute
    the ratio per hour, and last_ok to determine the color of the bar.
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
    """Return the checks of the last N hours for a component.

    List of dicts {hour_bucket, checks, oks, last_ok}. Ascending chronological order.
    Hours without a check are absent (to be padded on the frontend if needed).
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
    """Record a music play (called from play_next on success)."""
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
    """Return {total_plays, unique_tracks, unique_users, total_seconds, by_source}."""
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
    """Limit the logs table to the last `keep` entries per guild (anti DB blow-up)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""DELETE FROM logs WHERE id IN (
                   SELECT id FROM logs WHERE guild_id = ?
                   ORDER BY ts DESC LIMIT -1 OFFSET ?
                 )""", (str(guild_id), keep))
    conn.commit()
    conn.close()

def get_activity_by_day(guild_id=None, days=14):
    """Count the logs per day over the last `days` days.
       guild_id=None -> cross-server. Returns [{date, count}, ...] (ASC, dates as YYYY-MM-DD strings).
       Includes days without activity (count=0)."""
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
    # Generate the complete list
    import datetime as _dt
    today = _dt.date.today()
    out = []
    for i in range(int(days) - 1, -1, -1):
        d = today - _dt.timedelta(days=i)
        ds = d.isoformat()
        out.append({"date": ds, "count": by_day.get(ds, 0)})
    return out

def get_xp_by_day(guild_id=None, days=14):
    """Approximation: we have no per-event XP log, but activity can be deduced from logs of type 'command' + message actions.
       For now we return the COUNT of logs of type action_message_* + command per day as an activity proxy."""
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

def get_logs_by_day(guild_id=None, days=8, types=None, user_id=None):
    """Generic series counting logs per day over `days` days.
       types = list of `type` values to filter on (None = all).
       user_id = filter on a specific user (None = all).
       Returns [{date, count}, ...] ASC, empty days included (count=0)."""
    conn = get_db()
    c = conn.cursor()
    clauses = ["ts >= datetime('now', ?)"]
    params = [f"-{int(days)} days"]
    if guild_id is not None:
        clauses.append("guild_id = ?")
        params.append(str(guild_id))
    if types:
        placeholders = ",".join("?" for _ in types)
        clauses.append(f"type IN ({placeholders})")
        params.extend(types)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(str(user_id))
    where = " AND ".join(clauses)
    c.execute(f"SELECT DATE(ts) AS day, COUNT(*) AS n FROM logs WHERE {where} GROUP BY day", params)
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
    """7 days x 24h heatmap over the last `weeks` weeks.
       Returns [[count_mon_h0, count_mon_h1, ...], [count_tue_h0, ...], ...] (7 rows x 24 cols)."""
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
        # SQLite: strftime %w => 0=Sunday..6=Saturday. We reorder to 0=Monday..6=Sunday
        dow = (r["dow"] - 1) % 7
        matrix[dow][r["hour"]] = r["n"]
    conn.close()
    return matrix

def get_guild_analytics_overview(guild_id):
    """Overview stats for the server Analytics page.

    Counts the logs (all activity: commands + actions + msg events)
    per time window. Active users = distinct user_id with >= 1 log.
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
    """Time series: number of logs per day over the last N days.

    Returns a list of {date: 'YYYY-MM-DD', count: int} ordered from oldest
    to most recent, with zeros for days without activity.
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
    # Fill the gaps
    today = _dtmod.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - _dtmod.timedelta(days=i)
        ds = d.isoformat()
        out.append({"date": ds, "count": counts.get(ds, 0)})
    return out


def get_cohort_retention(guild_id, weeks=12):
    """Cohort retention per week. For each cohort (join week),
    we compute the % of members still active (1+ log) in the following weeks.

    Returns a list of dicts:
    [{cohort_week: 'YYYY-Www', cohort_size: int,
      week_offsets: [pct_w0, pct_w1, ..., pct_wN]}]
    """
    import datetime as _dtmod
    conn = get_db(); c = conn.cursor()
    # List cohorts: every member_join grouped by ISO week
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
        # For each offset (week N after join), count how many are active
        join_dates = {uid: r for uid, r in cohort_join_ts[week].items()}
        offsets = []
        for w_offset in range(weeks):
            start_iso = None
            end_iso = None
            # We use the week of the first joiner as the reference
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
            # Count distinct user_id (cohort members) active over this window
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
    """Yield rows for CSV: ts, type, user_id, username, channel_name, content.
    Generator."""
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
    """Member time series: joins, leaves, cumulative net per day.

    Requires on_member_join/remove to have logged action_member_join /
    action_member_leave beforehand. No absolute cumulative total (we do not have
    the initial total), only the variations.
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
    """Detail of a heatmap cell (dow + hour) over the last `weeks`
    weeks. dow=0..6 where 0=Monday (consistent with get_activity_heatmap).
    Returns (total, top_rows) where top_rows = list of {type, content, n}."""
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
    """Top of the most used commands."""
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
    """Most active users (commands sent + messages edit/delete) over N days."""
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
    """Global log purge: limit N per guild + delete older than max_age_days. Returns a dict of counts."""
    conn = get_db()
    c = conn.cursor()
    # 1. Purge by age
    c.execute("DELETE FROM logs WHERE ts < datetime('now', ?)", (f"-{int(max_age_days)} days",))
    by_age = c.rowcount
    # 2. Purge per guild (keep the N most recent)
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
    # VACUUM to reclaim disk space
    try:
        c.execute("VACUUM")
    except Exception:
        pass
    conn.close()
    return {"by_age": by_age, "by_count": by_count}


# ===== GUILD CHANNELS (cache for BotTalk + readable logs) =====
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
    """direction = 'in' (user -> bot) or 'out' (bot -> user via dashboard)."""
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
    """List of users the bot exchanged DMs with, last message + unread count."""
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
    rows.reverse()  # oldest -> most recent
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

    # Rich per-pageview tracking: active time, scroll, device, referrer.
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

    # Ko-fi donations (received via webhook). Unique txn_id to avoid duplicates.
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
    """Return aggregated stats: total, last 24h, 7d, 30d, top users."""
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
    """Return aggregated stats: total, h24, 7d, 30d, uniques (distinct ip_hash)."""
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
    """Insert a pageview (first hit) or update duration/scroll (heartbeat/unload).

    The first call creates the row with the device/referrer metadata.
    Subsequent calls (same vid) only update active_ms (max) and scroll_pct (max).
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
    """Rich engagement stats for a site (landing/dashboard).

    Returns: counters per period, average/median active time, bounce rate,
    average scroll, device/browser/os split, top referrers, top pages, hourly split.
    """
    conn = get_db()
    c = conn.cursor()
    out = {}

    # --- Counters + engagement per period ---
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

    # --- Visits per day (30d) ---
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

    # --- Top referrers (30d, excluding self) ---
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

    # --- Pages where people stay the longest (30d) ---
    # Sorted by average active time. Minimum 3 visits to avoid statistical noise.
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

    # --- Hourly split (30d, server local time) ---
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
    """Record a Ko-fi donation. Idempotent via txn_id (ON CONFLICT IGNORE).

    Returns True if inserted, False if duplicate (txn_id already seen).
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
    """Delete a donation by its id. Returns True if deleted."""
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
    """Donation stats: totals per period, counters, top donors, recent list, per day."""
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

    # Main currency (the most frequent one)
    cur = c.execute(
        "SELECT currency, COUNT(*) AS n FROM donations WHERE currency IS NOT NULL "
        "GROUP BY currency ORDER BY n DESC LIMIT 1"
    ).fetchone()
    out["currency"] = cur["currency"] if cur else "EUR"

    # Average donation (total)
    avg = c.execute("SELECT COALESCE(AVG(amount),0) AS a FROM donations").fetchone()
    out["avg_amount"] = round(float(avg["a"]), 2)

    # Top donors (all-time cumulative)
    top = c.execute(
        '''SELECT COALESCE(donor_name,'Anonyme') AS name,
                  SUM(amount) AS total, COUNT(*) AS n
           FROM donations GROUP BY name ORDER BY total DESC LIMIT 10'''
    ).fetchall()
    out["top_donors"] = [
        {"name": r["name"], "total": round(float(r["total"]), 2), "count": int(r["n"])}
        for r in top
    ]

    # Recent donations (last 50)
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

    # Per day (30d)
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
    """Delete every message exchanged with a given user. Returns the number deleted."""
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
    "welcome_template":     "👋 Welcome {user}!\nWelcome to **{guild}**! You are member number **{count}**.",
    # IA (Groq) — config globale, owner-only via dashboard
    "ai_enabled":           "0",
    "ai_model":             "llama-3.3-70b-versatile",
    "ai_system_prompt":     "You are TookBot, the official assistant of an all-in-one Discord bot. You are concise, helpful and friendly. Answer in a few sentences at most unless asked for detail. Avoid endless lists.",
    "ai_allowed_user_ids":  "",   # CSV
    "ai_max_tokens":        "400",
    # Vision model (used if the user attaches an image/GIF to their message).
    # Must be a Groq model that supports vision (multimodal).
    "ai_vision_model":      "meta-llama/llama-4-scout-17b-16e-instruct",
    # AI voice mode: if "1", the AI answers with a voice message (TTS) instead of text.
    # Microsoft Edge TTS voices (free). Available EN voices:
    #   en-US-AriaNeural (female), en-US-GuyNeural (male),
    #   en-GB-SoniaNeural (female, UK), en-US-JennyNeural (female, multilingual).
    "ai_voice_enabled":     "0",
    "ai_voice_name":        "en-US-AriaNeural",
    # TTS provider: "edge" (Microsoft Edge, free, robotic) or "elevenlabs"
    # (top quality, free tier 10k chars/month, auto fallback to edge if the quota runs out).
    # ELEVENLABS_API_KEY must be set in .env for "elevenlabs".
    "ai_voice_provider":    "edge",
    # ElevenLabs voice ID (premade voices, work in EN through the multilingual model).
    "ai_elevenlabs_voice_id": "XB0fDUnXU5powFXDhCwa",  # Charlotte (female, natural)
    "ai_elevenlabs_model":    "eleven_multilingual_v2",
    # Ko-fi support message (posted when a member gets a donor role)
    "soutien_message":      "<user> decided to lend a hand! Thanks for your support!",
    "soutien_role_ids":     "",   # CSV of role IDs; empty = fallback on default names
    "soutien_channel_id":   "",   # empty = fallback on env SOUTIEN_CHANNEL_ID
    # Cards: minimum age (days) of a server to allow /roll (anti-farm through
    # throwaway servers). 0 = disabled. Override: env ROLL_MIN_GUILD_AGE_DAYS.
    "roll_min_guild_age_days": "7",
    # Active global event (key from the GLOBAL_EVENTS catalog, or empty = none).
    "global_event_key": "",
    # Drop multiplier for the CARDS tagged to the event (1 = no boost).
    "global_event_drop_boost": "2.0",
    # GENERAL boost of rares (epic/legendary/mythic) during the event (1 = none).
    "global_event_rarity_boost": "1.0",
    # Cards: max number of "solo" servers (user alone with the bot) where an account
    # can roll. Beyond that, /roll is blocked on any new solo server. 0 = disabled.
    "roll_max_solo_guilds": "2",
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
    """Return a dict: key -> value (with defaults applied)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings")
    db = {r["key"]: r["value"] for r in c.fetchall()}
    conn.close()
    out = dict(DEFAULT_SETTINGS)
    out.update(db)
    return out


# ===== GUILD config (everything tunable by the owner) =====
DEFAULT_GUILD_CONFIG = {
    "create_cost": 10000,      # essences to create a guild
    "max_members": 30,
    "hop_cooldown_h": 24,      # delay before joining a guild again
    "daily_xp_cap": 1000,      # max XP contributed per member / day (personal actions)
    "xp": {
        "roll": 10,
        "fusion": 25,
        "wheel": 30,
        "essence_per_100": 5,   # XP per 100 essences donated
        "boss": {"1": 60, "2": 150, "3": 350, "4": 700, "5": 1400},
    },
    "level_base": 600,         # XP required to reach level 2 (cumulative lvl60 ~1.65M)
    "level_growth": 1.10,      # x per level (XP for level n = base * growth^(n-2))
    "max_level": 60,
    # Reward tiers: at a given level, ABSOLUTE bonuses. A guild applies the
    # highest tier whose level <= its own level. roll_cd_min = minutes LESS.
    "rewards": [
        {"level": 1,  "essence_pct": 0,  "xp_pct": 0,  "roll_cd_min": 0,  "charges": 0, "wishlist": 0, "boss_pct": 0,  "bank": False, "raids": False, "shop": False},
        {"level": 10, "essence_pct": 4,  "xp_pct": 5,  "roll_cd_min": 2,  "charges": 0, "wishlist": 0, "boss_pct": 2,  "bank": True,  "raids": False, "shop": False},
        {"level": 20, "essence_pct": 7,  "xp_pct": 8,  "roll_cd_min": 4,  "charges": 0, "wishlist": 1, "boss_pct": 4,  "bank": True,  "raids": True,  "shop": False},
        {"level": 35, "essence_pct": 11, "xp_pct": 12, "roll_cd_min": 6,  "charges": 0, "wishlist": 1, "boss_pct": 6,  "bank": True,  "raids": True,  "shop": True},
        {"level": 50, "essence_pct": 15, "xp_pct": 16, "roll_cd_min": 8,  "charges": 0, "wishlist": 2, "boss_pct": 9,  "bank": True,  "raids": True,  "shop": True},
        {"level": 60, "essence_pct": 20, "xp_pct": 20, "roll_cd_min": 10, "charges": 1, "wishlist": 3, "boss_pct": 12, "bank": True,  "raids": True,  "shop": True},
    ],
    # Guild shop: items paid with the BANK. type: guild_xp | rolls_all |
    # essence_all. value = guild XP, or rolls/essences given to EACH member.
    "shop": [
        {"key": "xpboost", "name": t("data.guild_shop.xpboost.name"), "cost": 5000, "type": "guild_xp",    "value": 3000, "desc": t("data.guild_shop.xpboost.desc", n=3000)},
        {"key": "rollall", "name": t("data.guild_shop.rollall.name"), "cost": 8000, "type": "rolls_all",   "value": 3,    "desc": t("data.guild_shop.rollall.desc", n=3)},
        {"key": "essall",  "name": t("data.guild_shop.essall.name"),  "cost": 6000, "type": "essence_all", "value": 500,  "desc": t("data.guild_shop.essall.desc", n=500)},
    ],
}


def get_guild_config():
    """Guild config (defaults merged with the owner override stored as JSON)."""
    import json as _j
    raw = get_setting("guild_config", None)
    cfg = dict(DEFAULT_GUILD_CONFIG)
    if raw:
        try:
            cfg.update(_j.loads(raw))
        except Exception:
            pass
    return cfg


def set_guild_config(cfg: dict):
    import json as _j
    set_setting("guild_config", _j.dumps(cfg))


def guild_level_for_xp(xp, cfg=None):
    """Level reached for a total amount of XP, following the curve (base * growth^(n-2))."""
    cfg = cfg or get_guild_config()
    base = float(cfg.get("level_base", 1000))
    growth = float(cfg.get("level_growth", 1.35))
    maxlv = int(cfg.get("max_level", 30))
    lvl = 1
    need_cum = 0.0
    for n in range(2, maxlv + 1):
        need_cum += base * (growth ** (n - 2))
        if xp >= need_cum:
            lvl = n
        else:
            break
    return lvl


def guild_rewards_for_level(level, cfg=None):
    """Effective reward tier (highest level <= the guild level)."""
    cfg = cfg or get_guild_config()
    paliers = sorted(cfg.get("rewards", []), key=lambda p: p.get("level", 0))
    eff = {}
    for p in paliers:
        if p.get("level", 0) <= level:
            eff = p
    # No tier <= level -> no bonus (NOT the lowest tier).
    return eff


# ===== GUILD CRUD =====
def guild_create(name, owner_id, tag=None, color=None):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_guild (name, tag, owner_id, color) VALUES (?, ?, ?, ?)",
              (name, tag, str(owner_id), color))
    gid = c.lastrowid
    c.execute("INSERT INTO card_guild_member (guild_id, user_id, role) VALUES (?, ?, 'master')",
              (gid, str(owner_id)))
    conn.commit(); conn.close()
    return gid


def guild_get(gid):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_guild WHERE id = ?", (int(gid),)).fetchone()
    conn.close()
    return dict(r) if r else None


def guild_get_by_name(name):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM card_guild WHERE LOWER(name) = LOWER(?)",
                  ((name or "").strip(),)).fetchone()
    conn.close()
    return dict(r) if r else None


def guild_set_name(gid, name):
    """Rename the guild + store the rename date (cooldown 1/month)."""
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_guild SET name = ?, renamed_at = ? WHERE id = ?",
              (name, _dt.datetime.utcnow().isoformat(), int(gid)))
    conn.commit(); conn.close()


def guild_list_all(search=None):
    """All guilds (admin) with their member count. Optional filter on name/tag."""
    conn = get_db(); c = conn.cursor()
    where = ""; params = []
    if search:
        where = "WHERE LOWER(g.name) LIKE ? OR LOWER(COALESCE(g.tag,'')) LIKE ?"
        like = f"%{search.lower()}%"; params = [like, like]
    rows = c.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM card_guild_member m WHERE m.guild_id = g.id) AS members "
        f"FROM card_guild g {where} ORDER BY g.level DESC, g.xp DESC", params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_GUILD_ADMIN_COLS = {"name", "tag", "level", "xp", "bank", "color", "emblem",
                     "owner_id", "renamed_at", "min_level", "min_power", "open_join"}


# ============ GUILD QUESTS ============
# Daily quests (personal, per member) + weekly quests (collective, per guild).
# metric = action incrementee : roll / fusion / boss / wheel / donate.
GUILD_DAILY_QUESTS = [
    {"key": "d_roll",   "metric": "roll",   "target": 10, "label": t("data.guild_quest.d_roll", n=10),  "xp": 60},
    {"key": "d_fusion", "metric": "fusion", "target": 1,  "label": t("data.guild_quest.d_fusion", n=1), "xp": 50},
    {"key": "d_boss",   "metric": "boss",   "target": 1,  "label": t("data.guild_quest.d_boss"),        "xp": 80},
]
GUILD_WEEKLY_QUESTS = [
    {"key": "w_roll",   "metric": "roll",   "target": 500, "label": t("data.guild_quest.w_roll", n=500),  "xp": 2500, "bank": 5000},
    {"key": "w_boss",   "metric": "boss",   "target": 15,  "label": t("data.guild_quest.w_boss", n=15),   "xp": 3000, "bank": 8000},
    {"key": "w_fusion", "metric": "fusion", "target": 30,  "label": t("data.guild_quest.w_fusion", n=30), "xp": 2000, "bank": 0},
]
_DAILY_BY_KEY = {q["key"]: q for q in GUILD_DAILY_QUESTS}
_WEEKLY_BY_KEY = {q["key"]: q for q in GUILD_WEEKLY_QUESTS}


def _quest_paris_date():
    """Today's date in FRENCH time (quests reset at midnight Europe/Paris)."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return _dt.date.today()


def _quest_day():
    return _quest_paris_date().isoformat()


def _quest_week():
    iso = _quest_paris_date().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def guild_quests_daily_get(user_id, guild_id):
    """The member's quests of the day (creates the missing rows). Returns a list of dicts."""
    day = _quest_day()
    conn = get_db(); c = conn.cursor()
    for q in GUILD_DAILY_QUESTS:
        c.execute("INSERT OR IGNORE INTO guild_quest_daily (user_id, guild_id, day, quest_key) "
                  "VALUES (?, ?, ?, ?)", (str(user_id), int(guild_id), day, q["key"]))
    conn.commit()
    rows = c.execute("SELECT quest_key, progress, done FROM guild_quest_daily "
                     "WHERE user_id = ? AND day = ?", (str(user_id), day)).fetchall()
    conn.close()
    pr = {r["quest_key"]: r for r in rows}
    out = []
    for q in GUILD_DAILY_QUESTS:
        r = pr.get(q["key"])
        out.append({**q, "progress": (r["progress"] if r else 0),
                    "done": bool(r["done"]) if r else False})
    return out


def guild_quests_weekly_get(guild_id):
    """Weekly quests of the guild + contributions per member. Creates the missing rows."""
    week = _quest_week()
    conn = get_db(); c = conn.cursor()
    for q in GUILD_WEEKLY_QUESTS:
        c.execute("INSERT OR IGNORE INTO guild_quest_weekly (guild_id, week, quest_key) "
                  "VALUES (?, ?, ?)", (int(guild_id), week, q["key"]))
    conn.commit()
    rows = c.execute("SELECT quest_key, progress, done FROM guild_quest_weekly "
                     "WHERE guild_id = ? AND week = ?", (int(guild_id), week)).fetchall()
    contribs = c.execute("SELECT quest_key, user_id, contrib FROM guild_quest_weekly_contrib "
                         "WHERE guild_id = ? AND week = ? AND contrib > 0 "
                         "ORDER BY contrib DESC", (int(guild_id), week)).fetchall()
    conn.close()
    pr = {r["quest_key"]: r for r in rows}
    cby = {}
    for cr in contribs:
        cby.setdefault(cr["quest_key"], []).append({"user_id": cr["user_id"], "contrib": cr["contrib"]})
    out = []
    for q in GUILD_WEEKLY_QUESTS:
        r = pr.get(q["key"])
        out.append({**q, "progress": (r["progress"] if r else 0),
                    "done": bool(r["done"]) if r else False,
                    "contrib": cby.get(q["key"], [])})
    return out


def guild_quest_progress(user_id, metric, amount=1):
    """Increment the daily quests (of the member) + weekly ones (of their guild) for `metric`.
    Auto-reward on completion (guild XP + bank for the weekly ones). Best-effort."""
    try:
        g = guild_of_user(user_id)
        if not g:
            return
        gid = g["id"]
        amount = max(0, int(amount))
        if amount <= 0:
            return
        day = _quest_day(); week = _quest_week()
        conn = get_db(); c = conn.cursor()
        # --- DAILY (perso) ---
        for q in GUILD_DAILY_QUESTS:
            if q["metric"] != metric:
                continue
            c.execute("INSERT OR IGNORE INTO guild_quest_daily (user_id, guild_id, day, quest_key) "
                      "VALUES (?, ?, ?, ?)", (str(user_id), gid, day, q["key"]))
            row = c.execute("SELECT progress, done FROM guild_quest_daily "
                            "WHERE user_id = ? AND day = ? AND quest_key = ?",
                            (str(user_id), day, q["key"])).fetchone()
            if row and not row["done"]:
                newp = row["progress"] + amount
                done = newp >= q["target"]
                c.execute("UPDATE guild_quest_daily SET progress = ?, done = ? "
                          "WHERE user_id = ? AND day = ? AND quest_key = ?",
                          (newp, 1 if done else 0, str(user_id), day, q["key"]))
                if done and q.get("xp"):
                    c.execute("UPDATE card_guild SET xp = xp + ? WHERE id = ?", (int(q["xp"]), gid))
                    c.execute("INSERT INTO card_guild_xp_log (guild_id, user_id, amount, source) "
                              "VALUES (?, ?, ?, ?)", (gid, str(user_id), int(q["xp"]),
                              f"quete:{q['label']}"))
        # --- WEEKLY (guild collective) ---
        for q in GUILD_WEEKLY_QUESTS:
            if q["metric"] != metric:
                continue
            c.execute("INSERT OR IGNORE INTO guild_quest_weekly (guild_id, week, quest_key) "
                      "VALUES (?, ?, ?)", (gid, week, q["key"]))
            c.execute("INSERT OR IGNORE INTO guild_quest_weekly_contrib (guild_id, week, quest_key, user_id) "
                      "VALUES (?, ?, ?, ?)", (gid, week, q["key"], str(user_id)))
            c.execute("UPDATE guild_quest_weekly_contrib SET contrib = contrib + ? "
                      "WHERE guild_id = ? AND week = ? AND quest_key = ? AND user_id = ?",
                      (amount, gid, week, q["key"], str(user_id)))
            row = c.execute("SELECT progress, done FROM guild_quest_weekly "
                            "WHERE guild_id = ? AND week = ? AND quest_key = ?",
                            (gid, week, q["key"])).fetchone()
            if row and not row["done"]:
                newp = row["progress"] + amount
                done = newp >= q["target"]
                c.execute("UPDATE guild_quest_weekly SET progress = ?, done = ? "
                          "WHERE guild_id = ? AND week = ? AND quest_key = ?",
                          (newp, 1 if done else 0, gid, week, q["key"]))
                if done:
                    if q.get("xp"):
                        c.execute("UPDATE card_guild SET xp = xp + ? WHERE id = ?", (int(q["xp"]), gid))
                        c.execute("INSERT INTO card_guild_xp_log (guild_id, user_id, amount, source) "
                                  "VALUES (?, ?, ?, ?)", (gid, str(user_id), int(q["xp"]),
                                  f"quete hebdo:{q['label']}"))
                    if q.get("bank"):
                        c.execute("UPDATE card_guild SET bank = bank + ? WHERE id = ?", (int(q["bank"]), gid))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[guild quest] {e}")


# ============ CANDIDATURES & PREREQUIS ============
def guild_application_add(gid, user_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO card_guild_application (guild_id, user_id) VALUES (?, ?)",
              (int(gid), str(user_id)))
    conn.commit(); conn.close()


def guild_application_remove(gid, user_id):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM card_guild_application WHERE guild_id = ? AND user_id = ?",
              (int(gid), str(user_id)))
    conn.commit(); conn.close()


def guild_application_list(gid):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT user_id, created_at FROM card_guild_application "
                     "WHERE guild_id = ? ORDER BY created_at", (int(gid),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_application_has(gid, user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM card_guild_application WHERE guild_id = ? AND user_id = ?",
                  (int(gid), str(user_id))).fetchone()
    conn.close()
    return bool(r)


def guild_application_count(gid):
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM card_guild_application WHERE guild_id = ?",
                  (int(gid),)).fetchone()["n"]
    conn.close()
    return int(n)


def guild_meets_requirements(gid, user_id):
    """(ok, reason). Checks min_level (collection level ~ power) and min_power."""
    g = guild_get(gid)
    if not g:
        return (False, t("data.guild_join.not_found"))
    min_pw = int(g.get("min_power") or 0)
    if min_pw > 0:
        try:
            st = compute_player_combat_stats(user_id)
            pw = combat_power(st["hp"], st["atk"])
        except Exception:
            pw = 0
        if pw < min_pw:
            return (False, t("data.guild_join.not_enough_power",
                             power=f"{pw:,}".replace(",", " "),
                             required=f"{min_pw:,}".replace(",", " ")))
    min_cards = int(g.get("min_level") or 0)
    if min_cards > 0:
        try:
            nc = user_card_count(user_id)
        except Exception:
            nc = 0
        if nc < min_cards:
            return (False, t("data.guild_join.not_enough_cards", count=nc, required=min_cards))
    return (True, None)


def guild_admin_update(gid, fields):
    """Admin update (owner dashboard) of whitelisted card_guild columns."""
    sets = []; params = []
    for k, v in (fields or {}).items():
        if k in _GUILD_ADMIN_COLS:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    params.append(int(gid))
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE card_guild SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit(); conn.close()
    return True


def guild_of_user(user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT g.* FROM card_guild g JOIN card_guild_member m ON m.guild_id = g.id "
                  "WHERE m.user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def guild_member_role(gid, user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT role FROM card_guild_member WHERE guild_id = ? AND user_id = ?",
                  (int(gid), str(user_id))).fetchone()
    conn.close()
    return r["role"] if r else None


def guild_members(gid):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM card_guild_member WHERE guild_id = ? "
                     "ORDER BY CASE role WHEN 'master' THEN 0 WHEN 'officer' THEN 1 ELSE 2 END, "
                     "xp_contributed DESC", (int(gid),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_member_count(gid):
    conn = get_db(); c = conn.cursor()
    n = c.execute("SELECT COUNT(*) AS n FROM card_guild_member WHERE guild_id = ?",
                  (int(gid),)).fetchone()["n"]
    conn.close()
    return int(n)


def guild_add_member(gid, user_id, role="member"):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO card_guild_member (guild_id, user_id, role) VALUES (?, ?, ?)",
              (int(gid), str(user_id), role))
    c.execute("DELETE FROM card_guild_invite WHERE user_id = ?", (str(user_id),))
    conn.commit(); conn.close()


def guild_remove_member(gid, user_id):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM card_guild_member WHERE guild_id = ? AND user_id = ?",
              (int(gid), str(user_id)))
    c.execute("INSERT INTO card_guild_left (user_id, left_at) VALUES (?, CURRENT_TIMESTAMP) "
              "ON CONFLICT(user_id) DO UPDATE SET left_at = CURRENT_TIMESTAMP", (str(user_id),))
    conn.commit(); conn.close()


def guild_set_role(gid, user_id, role):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_guild_member SET role = ? WHERE guild_id = ? AND user_id = ?",
              (role, int(gid), str(user_id)))
    conn.commit(); conn.close()


def guild_set_owner(gid, user_id):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_guild SET owner_id = ? WHERE id = ?", (str(user_id), int(gid)))
    c.execute("UPDATE card_guild_member SET role = 'master' WHERE guild_id = ? AND user_id = ?",
              (int(gid), str(user_id)))
    conn.commit(); conn.close()


def guild_delete(gid):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM card_guild_member WHERE guild_id = ?", (int(gid),))
    c.execute("DELETE FROM card_guild_invite WHERE guild_id = ?", (int(gid),))
    c.execute("DELETE FROM card_guild WHERE id = ?", (int(gid),))
    conn.commit(); conn.close()


def guild_left_at(user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT left_at FROM card_guild_left WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return r["left_at"] if r else None


def guild_invite_add(gid, user_id):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO card_guild_invite (guild_id, user_id) VALUES (?, ?)",
              (int(gid), str(user_id)))
    conn.commit(); conn.close()


def guild_invite_has(gid, user_id):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT 1 FROM card_guild_invite WHERE guild_id = ? AND user_id = ?",
                  (int(gid), str(user_id))).fetchone()
    conn.close()
    return bool(r)


def guild_add_xp(gid, amount):
    """Add XP to the guild, recompute the level. Returns (level, leveled_up)."""
    if amount <= 0:
        g = guild_get(gid)
        return (g["level"] if g else 1, False)
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT xp, level FROM card_guild WHERE id = ?", (int(gid),)).fetchone()
    if not r:
        conn.close(); return (1, False)
    new_xp = int(r["xp"]) + int(amount)
    new_level = guild_level_for_xp(new_xp)
    c.execute("UPDATE card_guild SET xp = ?, level = ? WHERE id = ?", (new_xp, new_level, int(gid)))
    conn.commit(); conn.close()
    return (new_level, new_level > int(r["level"]))


def guild_member_action_xp(user_id, amount, source="action"):
    """Personal action XP (roll/fusion/wheel/donation): applies the member's daily
    cap then credits their guild. Logs the credited XP (who/source/amount).
    Returns (guild, level, leveled_up) or None."""
    g = guild_of_user(user_id)
    if not g:
        return None
    cfg = get_guild_config()
    cap = int(cfg.get("daily_xp_cap", 1000))
    today = _today_str()
    conn = get_db(); c = conn.cursor()
    m = c.execute("SELECT daily_xp, daily_date FROM card_guild_member "
                  "WHERE guild_id = ? AND user_id = ?", (g["id"], str(user_id))).fetchone()
    if not m:
        conn.close(); return None
    used = int(m["daily_xp"]) if (m["daily_date"] == today) else 0
    allowed = max(0, cap - used)
    add = min(int(amount), allowed)
    c.execute("UPDATE card_guild_member SET daily_xp = ?, daily_date = ?, "
              "xp_contributed = xp_contributed + ? WHERE guild_id = ? AND user_id = ?",
              (used + add, today, add, g["id"], str(user_id)))
    if add > 0:
        c.execute("INSERT INTO card_guild_xp_log (guild_id, user_id, amount, source) "
                  "VALUES (?, ?, ?, ?)", (g["id"], str(user_id), add, source))
    conn.commit(); conn.close()
    if add <= 0:
        return (g, g["level"], False)
    lvl, up = guild_add_xp(g["id"], add)
    return (g, lvl, up)


def guild_xp_log_add(gid, user_id, amount, source):
    """Log a guild XP entry (used for quest rewards that credit the guild
    directly, outside the member cap)."""
    if int(amount) <= 0:
        return
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO card_guild_xp_log (guild_id, user_id, amount, source) "
              "VALUES (?, ?, ?, ?)", (int(gid), str(user_id), int(amount), source))
    conn.commit(); conn.close()


def guild_xp_log_list(gid, limit=30):
    """Latest guild XP entries (most recent first)."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT user_id, amount, source, created_at FROM card_guild_xp_log "
        "WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (int(gid), int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_bank_add(gid, amount):
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE card_guild SET bank = bank + ? WHERE id = ?", (int(amount), int(gid)))
    conn.commit(); conn.close()


def guild_bank_spend(gid, amount):
    """Debit the bank if the balance is enough. Returns True if ok."""
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT bank FROM card_guild WHERE id = ?", (int(gid),)).fetchone()
    if not r or int(r["bank"]) < int(amount):
        conn.close(); return False
    c.execute("UPDATE card_guild SET bank = bank - ? WHERE id = ?", (int(amount), int(gid)))
    conn.commit(); conn.close()
    return True


def guild_member_ids(gid):
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT user_id FROM card_guild_member WHERE guild_id = ?", (int(gid),)).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def guild_top(limit=20):
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM card_guild_member m WHERE m.guild_id = g.id) AS members "
        "FROM card_guild g ORDER BY g.level DESC, g.xp DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_perks_for_user(user_id):
    """Reward tier of the user's guild (empty dict if they have no guild)."""
    g = guild_of_user(user_id)
    if not g:
        return {}
    return guild_rewards_for_level(g["level"])


GUILD_DEFAULT_SETTINGS = {
    # Server language ("" = auto: Discord client language of each user)
    "locale":          "",
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
    # XP - configurable per server
    "xp_min":              "1",
    "xp_max":              "5",
    "xp_cooldown_seconds": "30",
    # Difficulty curve: exponent E in xp_for_level(L) = L^E.
    # Useful range 2.0 to 8.0. Default 5.0 (= previous behaviour).
    # Lower = easy progression (level 10 reachable quickly).
    # Higher = hard progression (each level requires a lot more XP).
    "xp_curve_exponent":   "5.0",
    # Default welcome message of the server
    "welcome_template": "👋 Welcome {user}!\nWelcome to **{guild}**! You are member number **{count}**.",
    # Initial setup (configured via /setup)
    "setup_completed":            "0",
    "setup_welcome_channel_id":   "",
    "setup_logs_channel_id":      "",
    "setup_alerts_channel_id":    "",
    "setup_admin_channel_id":     "",
    # Member presentations
    "presentation_enabled":       "0",
    "presentation_channel_id":    "",
    # Moderator permissions (configured by the server owner)
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
    # Toggleable dashboard pages (without a slash equivalent)
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
    """Current state: {last_claim_date, streak, total_claims}."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT last_claim_date, streak, total_claims FROM daily_claims WHERE user_id = ?",
              (str(user_id),))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"last_claim_date": None, "streak": 0, "total_claims": 0}
    return dict(row)

PROMO_REWARD_TYPES = {"tookcoins", "pass_xp", "premium_grant_days",
                      "roll", "epic_roll", "golden_roll"}

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
    """Check without applying: (ok, reason, promo_dict)."""
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
    """Mark the redemption (atomic). The caller must apply the reward."""
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
    """Return {pseudo, color}. If the pseudo is already taken in the session, re-use it.
    Color assigned in arrival order."""
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
    """Mark today's claim and bump the streak. Idempotent per day."""
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


# ===== Member roles (cache for mod perms gating) =====
def member_roles_set(guild_id, user_id, role_ids):
    """Replace every role_id of a member for this guild."""
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
    """True if the user has the configured mod_role AND perm_key is enabled.

    `perm_key` without the 'mod_perm_' prefix. E.g. 'kick', 'ticket'.
    """
    # Read mod_role_id if not provided
    if mod_role_id is None:
        mod_role_id = guild_setting_get(guild_id, "mod_role_id", "") or ""
    if not mod_role_id:
        return False
    if not member_has_role(guild_id, user_id, mod_role_id):
        return False
    val = guild_setting_get(guild_id, f"mod_perm_{perm_key}", "0")
    return val == "1"


def replace_guild_channels(guild_id, channels):
    """Bulk-replace the channel list of a guild. channels = list of dicts."""
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


# ===== DUEL - COMBAT XP =====
STAT_COLUMNS = {
    "force":     "stat_force",
    "agilite":   "stat_agilite",
    "defense":   "stat_defense",
    "endurance": "stat_endurance",
    "chance":    "stat_chance",
}

def get_xp_pour_prochain_niveau(level):
    """XP required to go from level `level` to the next one."""
    return int(100 * (level ** 1.3))

def get_combat_xp_progress(total_xp):
    """Return (level, xp_in_current_level, xp_required_for_next_level)."""
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
    """Add combat XP. Returns (new_level, leveled_up)."""
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
    """Assign 1 point to a stat. Returns True on success."""
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
    """Manually grant the premium feature to a user.

    `expires_at` (ISO TEXT): temporary grant (trial, subscription). None = permanent.
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
    """True if the user has a manual grant for this feature, not expired.

    By default, a feature='all' grant also counts (premium master pack).
    Pass `inherit_all=False` to require a grant strictly on the requested
    feature. Useful for separate subscriptions (e.g. Battle Pass) that must
    NOT be auto-unlocked by the 'all' grant of /niveau Premium.

    expires_at NULL = permanent grant. expires_at <= now = expired (ignored).
    """
    conn = get_db()
    c = conn.cursor()
    # expires_at filter: NULL or in the future. Lexicographic ISO TEXT comparison
    # OK because the datetime('now') format = 'YYYY-MM-DD HH:MM:SS'.
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


# ===== TookBot+ activation keys =====

def tookbot_plus_key_create(code, duration_days, created_by=None, note=None):
    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO tookbot_plus_keys (code, duration_days, created_by, note)
                 VALUES (?, ?, ?, ?)""",
              (str(code).upper(), int(duration_days),
               str(created_by) if created_by else None, note))
    conn.commit(); conn.close()

def tookbot_plus_key_get(code):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT * FROM tookbot_plus_keys WHERE code = ?",
                  (str(code).upper(),)).fetchone()
    conn.close()
    return dict(r) if r else None

def tookbot_plus_keys_list():
    conn = get_db(); c = conn.cursor()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM tookbot_plus_keys ORDER BY created_at DESC").fetchall()]
    conn.close()
    return rows

def tookbot_plus_key_delete(code):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM tookbot_plus_keys WHERE code = ?", (str(code).upper(),))
    n = c.rowcount
    conn.commit(); conn.close()
    return n

def tookbot_plus_key_redeem(code, user_id, username=None, avatar=None):
    """Atomic redeem of a single-use key. Marks the key redeemed and grants
    (or extends) the user's TookBot+ grant for `duration_days` days.
    Stores the claimer's username/avatar for the owner display.
    Returns (ok, reason, expires_at). reason: ok | code_invalid | already_redeemed."""
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT * FROM tookbot_plus_keys WHERE code = ?",
                    (str(code).upper(),)).fetchone()
    if not row:
        conn.close()
        return False, "code_invalid", None
    if row["redeemed_by"]:
        conn.close()
        return False, "already_redeemed", None
    days = int(row["duration_days"])
    # Starting point = existing active TookBot+ expiry (extension), otherwise now.
    base = c.execute(
        """SELECT expires_at FROM premium_grants
           WHERE user_id = ? AND feature = 'tookbot_plus'
             AND expires_at IS NOT NULL AND expires_at > datetime('now')""",
        (str(user_id),)).fetchone()
    if base and base["expires_at"]:
        new_expires = c.execute("SELECT datetime(?, ?)",
                                (base["expires_at"], f"+{days} days")).fetchone()[0]
    else:
        new_expires = c.execute("SELECT datetime('now', ?)",
                                (f"+{days} days",)).fetchone()[0]
    # Mark the key as consumed (atomic: re-check redeemed_by NULL)
    upd = c.execute("UPDATE tookbot_plus_keys SET redeemed_by = ?, "
                    "redeemed_at = CURRENT_TIMESTAMP, redeemed_username = ?, redeemed_avatar = ? "
                    "WHERE code = ? AND redeemed_by IS NULL",
                    (str(user_id), username, avatar, str(code).upper()))
    if upd.rowcount == 0:
        conn.close()
        return False, "already_redeemed", None
    # Apply / extend the TookBot+ grant
    c.execute('''INSERT INTO premium_grants (user_id, feature, granted_by, granted_at, note, expires_at)
                 VALUES (?, 'tookbot_plus', ?, CURRENT_TIMESTAMP, ?, ?)
                 ON CONFLICT(user_id, feature) DO UPDATE SET
                   granted_by = excluded.granted_by,
                   granted_at = CURRENT_TIMESTAMP,
                   note       = excluded.note,
                   expires_at = excluded.expires_at''',
              (str(user_id), f"key:{str(code).upper()}",
               f"Cle d'activation ({days}j)", new_expires))
    conn.commit(); conn.close()
    return True, "ok", new_expires

def tookbot_plus_key_deactivate(code):
    """Manually deactivate an already used key: removes the claimer's TookBot+
    grant and marks the key revoked. Returns (ok, redeemed_by)."""
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT redeemed_by FROM tookbot_plus_keys WHERE code = ?",
                    (str(code).upper(),)).fetchone()
    if not row or not row["redeemed_by"]:
        conn.close()
        return False, None
    uid = row["redeemed_by"]
    # Remove the claimer's TookBot+ grant (only if it came from THIS key, so we
    # do not revoke a Stripe subscription / another active key).
    c.execute("DELETE FROM premium_grants WHERE user_id = ? AND feature = 'tookbot_plus' "
              "AND granted_by = ?", (str(uid), f"key:{str(code).upper()}"))
    c.execute("UPDATE tookbot_plus_keys SET revoked_at = CURRENT_TIMESTAMP WHERE code = ?",
              (str(code).upper(),))
    conn.commit(); conn.close()
    return True, uid


def start_tookbot_plus_trial(user_id, days: int = 7) -> dict:
    """Start an N-day TookBot+ trial for this user. Only 1 trial / user.

    Returns {ok: bool, error: str|None, expires_at: str|None}.
    """
    import datetime as _dtmod
    conn = get_db()
    c = conn.cursor()

    # Check there is no trial already used (premium_settings.trial_used_at)
    row = c.execute(
        "SELECT trial_used_at FROM premium_settings WHERE user_id = ?",
        (str(user_id),),
    ).fetchone()
    if row and row["trial_used_at"]:
        conn.close()
        return {"ok": False, "error": "trial_already_used", "expires_at": None}

    # Check they do not already have TookBot+ active (permanent grant or active trial)
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
    # Insert the temporary grant
    c.execute('''
        INSERT INTO premium_grants (user_id, feature, granted_by, granted_at, note, expires_at)
        VALUES (?, 'tookbot_plus', NULL, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(user_id, feature) DO UPDATE SET
            granted_at = CURRENT_TIMESTAMP,
            note       = excluded.note,
            expires_at = excluded.expires_at
    ''', (str(user_id), f"trial_{days}j", expires))
    # Mark trial_used_at in premium_settings (creates the row if missing)
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
    """Unified combination: Discord entitlement OR manual grant OR owner ENV.

    `owner_id` (str) is read from DISCORD_OWNER_ID by the caller; passing it
    explicitly avoids importing os here.
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
    """Return every active assignment of a user (1 max except for the owner)."""
    conn = get_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT guild_id, assigned_at FROM guild_boost WHERE user_id = ? ORDER BY assigned_at DESC",
        (str(user_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def guild_boost_assign(user_id, guild_id):
    """Assign a user's Guild Boost + to a guild (upsert).

    The caller must check the capacity (user_max_guild_slots) BEFORE calling.
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
    """Remove the assignment. If guild_id is None, remove all of the user's."""
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
    """Total number of servers this user can boost at the same time.

    Cumulative slots (they stack):
    - Owner -> 999 (unlimited)
    - Solo  (entitlement OR 'guild_boost' grant)        : +1
    - Duo   (entitlement OR 'guild_boost_duo' grant)    : +2
    - Squad (entitlement OR 'guild_boost_squad' grant)  : +5
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
    """True if at least one user assigned their active Guild Boost + to this guild.

    Checks that the users who assigned still have slots available
    (valid grant or entitlement on one of the 3 tiers).
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
    """True if the user has at least 1 usable Guild Boost + slot."""
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
    """Return (start_iso, end_iso) of the given month 'YYYY-MM'."""
    y, m = map(int, month_key.split("-"))
    start = _dt.datetime(y, m, 1)
    if m == 12:
        end = _dt.datetime(y + 1, 1, 1) - _dt.timedelta(seconds=1)
    else:
        end = _dt.datetime(y, m + 1, 1) - _dt.timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def _seasonal_bg_expiry(unlocked_at: _dt.datetime) -> str:
    """Seasonal BG: expires at the end of the month AFTER the unlock month.

    Example: unlocked 28 May 2026 -> expires 30 June 2026 23:59:59.
    """
    y, m = unlocked_at.year, unlocked_at.month
    if m == 12:
        ey, em = y + 1, 12
        # Month after December = January of year+2
        if em == 12:
            ny, nm = ey, 12  # December, same year
        ny, nm = y + 2, 1
        end = _dt.datetime(y + 2, 1, 1) - _dt.timedelta(seconds=1)
    else:
        # Next month
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
        # Migration: rebalance daily earn_xp 200/500 -> 100/250 (old versions were too hardcore).
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
    """Return the period start marker:
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
    """Ensure the user has `slots` quests for the current period.

    If the period changed or no quest exists, we draw `slots` distinct random
    templates from the pool and insert them. Otherwise return them as-is.
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

    # Draw `slots` different templates for this period
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
    """Return the daily + weekly quests of the current period (generating them if needed)."""
    quests = []
    quests += ensure_user_quests(user_id, "daily",  slots=3)
    quests += ensure_user_quests(user_id, "weekly", slots=3)
    return quests


def increment_quest_progress(user_id, quest_type: str, amount: int = 1) -> list[dict]:
    """Increment the progress of ALL active quests matching this type.

    Returns the list of quests newly completed (target reached during this
    call) - useful to credit the Pass XP and notify the user.
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
                continue  # already completed
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
    """Mark the quest as claimed and return {xp_reward, ...} if OK, else None."""
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
    """Return the season of the current month, creating it if it does not exist.

    On creation, we automatically seed the 30 reward tiers and
    the 3 cosmetic sabers of the season.
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


# No TookCoin to avoid P2W (TookCoins are used for duels/sabers).
# Purely cosmetic rewards + message XP boosts (time-limited).
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
    """On every startup: ensures that all existing seasons have their
    seasonal sabers + their pass_rewards up to date with the current
    _PASS_TIER_MAP. Unlocks already granted are NOT revoked."""
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
            # Re-seed pass_rewards: drop + insert to apply the new mapping
            conn = get_db(); c = conn.cursor()
            c.execute("DELETE FROM pass_rewards WHERE season_id = ?", (s["season_id"],))
            conn.commit()
            conn.close()
            seed_pass_rewards_for_season(s["season_id"], s["month_key"])
        except Exception as e:
            print(f"[migrate] pass_rewards season {s['season_id']} error: {e!r}")


def seed_pass_rewards_for_season(season_id: int, month_key: str):
    """Insert the 30 pass_rewards rows for a season. Idempotent.

    Everything is themed per month (titles, emojis, sabers, BGs): the
    _PASS_TIER_MAP map uses indices (title_idx, emoji_idx) or references
    (rarete, bg index) that seasonal_themes resolves into concrete values
    according to the theme of the month."""
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
    """Create 3 seasonal cosmetic sabers (R/SR/SSR).

    To preserve the anti-P2W principle, each seasonal saber COPIES the effect
    + the detailed description of an existing f2p saber. BUT the source f2p
    saber changes every month (see seasonal_themes.MONTH_THEMES), which gives
    the Pass real gameplay variety across seasons. The saber name, the emoji,
    the special's name and its emoji come from the theme of the month.

    IDs: season_<YYYY-MM>_<R|SR|SSR>"""
    from seasonal_themes import sabre_skin
    conn = get_db()
    c = conn.cursor()
    rows = []
    for rarete in ("R", "SR", "SSR"):
        skin = sabre_skin(month_key, rarete)
        source_id = skin["source_id"]
        # Read the source f2p saber to copy its effect + description
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
    # UPSERT: if the saber already exists (season generated previously with an
    # old template) we update its visual fields to reflect the current theme
    # theme. Mechanics (speciale_effet) stay identical = anti-P2W.
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
    """Migration: delete the old seasonal sabers created with invalid
    rarities (rare/epique/legendaire). Also cleans up duel_collection
    and resets sabre_equipe to 'bleu' for the profiles that had them equipped."""
    conn = get_db()
    c = conn.cursor()
    bad = c.execute(
        "SELECT id FROM sabres WHERE id LIKE 'season_%_rare' OR id LIKE 'season_%_epique' OR id LIKE 'season_%_legendaire'"
    ).fetchall()
    if not bad:
        conn.close()
        return
    ids = [r["id"] for r in bad]
    # Reset sabre_equipe to 'bleu' if the user had a legacy saber equipped
    c.execute(
        f"UPDATE duel_profil SET sabre_equipe = 'bleu' WHERE sabre_equipe IN ({','.join(['?'] * len(ids))})",
        ids,
    )
    # Drop the legacy sabers from duel_collection
    c.execute(
        f"DELETE FROM duel_collection WHERE sabre_id IN ({','.join(['?'] * len(ids))})",
        ids,
    )
    # Delete the sabers themselves
    c.executemany("DELETE FROM sabres WHERE id = ?", [(i,) for i in ids])
    # Delete the pass_unlocks that pointed at those legacy sabers
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
    """Compare the user's XP to the threshold of every tier not claimed yet;
    triggers the matching rewards. Returns the list of delivered rewards.

    Instant rewards (tookcoins) are applied directly.
    The others (bg, title, emoji, boost_xp, sabre) create pass_unlocks entries
    with a suitable expires_at.
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
        # Resolution depending on type
        if rtype == "tookcoins":
            try:
                # Make sure the duel profile exists (otherwise the UPDATE is a no-op)
                if get_duel_profil(user_id) is None:
                    creer_duel_profil(user_id, str(user_id))
                ajouter_tookcoins(user_id, int(payload.get("amount") or 0))
            except Exception as e:
                print(f"[auto_claim] tookcoins error: {e!r}")
        elif rtype == "sabre":
            # Add the seasonal saber of the rarity to the collection
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
            # Also record it in pass_unlocks for visibility
            add_pass_unlock(user_id, "sabre", {"sabre_id": sabre_id, **payload}, season_id=season_id)
        elif rtype == "bg":
            # Seasonal BG: expires at the end of the NEXT month
            now = _dt.datetime.utcnow()
            exp = _seasonal_bg_expiry(now)
            add_pass_unlock(user_id, "bg", payload, season_id=season_id, expires_at=exp)
        elif rtype == "boost_xp":
            # Active immediately, expires in N hours
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
    """Return the active title and emoji (verified in the unlocks) for this user.

    Returns {'title': str|None, 'emoji': str|None}. If the user selected a
    title/emoji they no longer own (rare case: revoke), we return None.
    """
    settings = get_premium_settings(user_id)
    sel_title = settings.get("pass_selected_title")
    sel_emoji = settings.get("pass_selected_emoji")
    out = {"title": None, "emoji": None}
    if not (sel_title or sel_emoji):
        return out
    # Titles + emojis are permanent (kept across seasons), so we ignore
    # expires_at which could have been set by old bugs or season rotations.
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
    """Return the 'titles' and 'emojis' lists the user owns through the Pass.

    Permanent: we ignore expires_at (old unlocks could have a date set by a
    bug or by old season rotations).
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
    """Return the highest boost_xp multiplier still active, otherwise 1.0."""
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
    """Pass active: manual grant feature='pass' OR Discord entitlement
    on the Pass subscription SKU.

    The Battle Pass is a product independent from /niveau Premium. A
    feature='all' grant (Premium pack) does NOT unlock the Pass automatically.
    To enable the check through the Discord subscription SKU, pass `sku_pass_id`.
    The owner manages their access via _has_pass in web.py / is_premium_user in bot.py.
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
    """Increment the Pass XP and return the new total."""
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
    """Insert a reaction-role mapping. UPSERT on (guild, message, emoji).

    delivery: 'reaction' (emojis under the message) or 'button' (buttons).
    style   : 'embed' or 'text' (normal message).
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
    """Return every mapping of the same 'unique' group on this message."""
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
    """Update last_check_at without touching last_seen_id."""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE social_alerts SET last_check_at = CURRENT_TIMESTAMP WHERE id = ?',
              (int(alert_id),))
    conn.commit()
    conn.close()


def social_alert_reset(alert_id, guild_id=None) -> int:
    """Clear last_seen_id to force a re-detection on the next poll."""
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
    """Insert or update a profile. Only the non-None params are written."""
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
    """Return True if this is a new participant, False if already registered."""
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
    """List the unfinished giveaways whose date has passed."""
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
