import datetime as _dt


def _dict(row):
    return dict(row) if row else None


def _best_user_identity(db, user_id):
    user_id = str(user_id)
    member = db.execute(
        """SELECT username, avatar_url
             FROM guild_members
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1""",
        (user_id,),
    ).fetchone()
    if member:
        return {"username": member["username"], "avatar_url": member["avatar_url"]}

    dm = db.execute(
        """SELECT username, avatar_url
             FROM dm_messages
            WHERE user_id = ?
            ORDER BY ts DESC
            LIMIT 1""",
        (user_id,),
    ).fetchone()
    if dm:
        return {"username": dm["username"], "avatar_url": dm["avatar_url"]}

    user = db.execute(
        """SELECT username
             FROM users
            WHERE user_id = ?
            ORDER BY xp DESC
            LIMIT 1""",
        (user_id,),
    ).fetchone()
    if user:
        return {"username": user["username"], "avatar_url": None}

    duel = db.execute("SELECT username FROM duel_profil WHERE user_id = ?", (user_id,)).fetchone()
    if duel:
        return {"username": duel["username"], "avatar_url": None}

    return {"username": None, "avatar_url": None}


def _activity(db, user_id, guild_id=None):
    if guild_id:
        rows = db.execute(
            """SELECT DATE(ts) AS day, COUNT(*) AS n
                 FROM logs
                WHERE guild_id = ? AND user_id = ?
                  AND ts >= datetime('now', '-14 days')
                GROUP BY day""",
            (str(guild_id), str(user_id)),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT DATE(ts) AS day, COUNT(*) AS n
                 FROM logs
                WHERE user_id = ?
                  AND ts >= datetime('now', '-14 days')
                GROUP BY day""",
            (str(user_id),),
        ).fetchall()
    by_day = {r["day"]: r["n"] for r in rows}
    today = _dt.date.today()
    return [
        {"date": (today - _dt.timedelta(days=i)).isoformat(), "count": by_day.get((today - _dt.timedelta(days=i)).isoformat(), 0)}
        for i in range(13, -1, -1)
    ]


def _fav_channels(db, user_id, guild_id=None):
    if not guild_id:
        return []
    rows = db.execute(
        """SELECT channel_id, MAX(channel_name) AS name, COUNT(*) AS n
             FROM logs
            WHERE guild_id = ? AND user_id = ? AND channel_id IS NOT NULL
              AND ts >= datetime('now', '-30 days')
            GROUP BY channel_id
            ORDER BY n DESC
            LIMIT 5""",
        (str(guild_id), str(user_id)),
    ).fetchall()
    return [dict(r) for r in rows]


def _type_counts(db, user_id, guild_id=None):
    if guild_id:
        rows = db.execute(
            """SELECT type, COUNT(*) AS n
                 FROM logs
                WHERE guild_id = ? AND user_id = ?
                  AND ts >= datetime('now', '-30 days')
                GROUP BY type
                ORDER BY n DESC""",
            (str(guild_id), str(user_id)),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT type, COUNT(*) AS n
                 FROM logs
                WHERE user_id = ?
                  AND ts >= datetime('now', '-30 days')
                GROUP BY type
                ORDER BY n DESC""",
            (str(user_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def build_user_profile_payload(db, user_id, guild_id=None, is_owner=False):
    user_id = str(user_id)
    selected = None
    if guild_id:
        selected = db.execute(
            "SELECT user_id, username, level, xp FROM users WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), user_id),
        ).fetchone()

    scope = "guild"
    source_guild_id = str(guild_id) if selected and guild_id else None
    if selected:
        user = dict(selected)
    elif is_owner:
        agg = db.execute(
            """SELECT user_id,
                      MAX(username) AS username,
                      SUM(xp) AS xp,
                      MAX(level) AS level
                 FROM users
                WHERE user_id = ?
                GROUP BY user_id""",
            (user_id,),
        ).fetchone()
        if agg:
            user = dict(agg)
            user["xp"] = int(user.get("xp") or 0)
            user["level"] = int(user.get("level") or 0)
            scope = "global"
        else:
            duel = db.execute(
                "SELECT user_id, username FROM duel_profil WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not duel:
                return None
            user = {"user_id": user_id, "username": duel["username"], "xp": 0, "level": 0}
            scope = "global"
    else:
        return None

    identity = _best_user_identity(db, user_id)
    if identity.get("username"):
        user["username"] = identity["username"]
    user["avatar_url"] = identity.get("avatar_url")

    guild_rows = db.execute(
        """SELECT u.guild_id, g.name AS guild_name, u.username, u.level, u.xp, gm.avatar_url
             FROM users u
             LEFT JOIN guilds g ON g.guild_id = u.guild_id
             LEFT JOIN guild_members gm ON gm.guild_id = u.guild_id AND gm.user_id = u.user_id
            WHERE u.user_id = ?
            ORDER BY u.xp DESC""",
        (user_id,),
    ).fetchall()

    duel = db.execute("SELECT * FROM duel_profil WHERE user_id = ?", (user_id,)).fetchone()

    activity_guild = source_guild_id if scope == "guild" else None
    return {
        "user": user,
        "scope": scope,
        "source_guild_id": source_guild_id,
        "guilds": [dict(r) for r in guild_rows],
        "activity": _activity(db, user_id, activity_guild),
        "fav_channels": _fav_channels(db, user_id, activity_guild),
        "type_counts": _type_counts(db, user_id, activity_guild),
        "duel": _dict(duel),
    }
