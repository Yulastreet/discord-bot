# discord-bot

## What this codebase does

Personal Discord bot plus Flask dashboard. The bot uses discord.py 2.7,
SQLite, yt-dlp/Deno music playback, XP/levels, welcome builder, tickets,
reaction roles, social alerts, duels, pass/premium features, and a DB-backed
command queue so the web process can ask the bot process to perform Discord
actions. The dashboard is Flask + Jinja and is intended for the owner and
server moderators/admins.

## Auth shape

- `web.py` owns the Flask app, session config, Discord OAuth constants, and
  `@app.before_request` middleware `_ctx`.
- `_ctx` enforces login, page/API authorization, selected guild access, and
  rejects inaccessible guilds from the session.
- `_is_owner_session`, `_accessible_guild_ids`, `_user_can_access_page`, and
  `_is_admin_of_current_guild` are the main dashboard authorization helpers.
- Discord OAuth is implemented in `web_app/routes/auth.py`; password login is
  only fallback when OAuth is disabled.
- Slash command permissions live in `commandes/*` via Discord permissions such
  as administrator/manage roles, while web-originated Discord actions go
  through `bot_command_enqueue` and the bot worker in `tasks/runtime.py`.

## Threat model

High impact: a non-owner or low-privileged Discord user gaining access to
owner-only dashboard routes, cross-guild data, premium grants, DM/log views, or
queued bot actions. Medium impact: a moderator acting outside the selected
guild scope, altering XP/reaction roles/tickets/social alerts for another
server, or causing the bot to post unwanted content. Also watch file upload,
log tail, OAuth callback/session handling, and external URL handling for music
and social integrations.

## Project-specific patterns to flag

- Any route under `web_app/routes/*` that changes state should rely on `_ctx`
  plus route-level owner/admin/guild checks when needed; owner-only APIs should
  call `_is_owner_session`.
- Any per-guild API must use `gid()`/selected guild scope and pass `guild_id`
  into DB helpers; cross-guild queries are expected only for owner/global pages.
- Bot command queue payloads are trusted by the bot process, so web routes that
  enqueue commands must validate channel IDs, role IDs, message IDs, mode, and
  user privilege before calling `bot_command_enqueue`.
- `send_file` is used for generated images/log buffers; flag any future direct
  user-controlled filesystem path.
- Dynamic SQL in `database.py` is usually built from allowlisted field names or
  fixed column lists; flag any dynamic table/column names derived from request
  data.

## Known false-positives

- `/privacy`, `/terms`, `/api/public-stats`, static files, OAuth login/callback,
  and login page are intended public endpoints.
- `app.secret_key` falls back to `os.urandom(24)` only for local/dev when
  `FLASK_SECRET` is missing; production should set `FLASK_SECRET`.
- `database.py` contains migration SQL and some f-string SQL for fixed internal
  column names; not every f-string there is request-controlled.
- `scripts/generate_*.py` are local asset generation scripts, not web-exposed
  handlers.
- `.deepsec/`, tests, and generated assets are not part of the running bot/web
  attack surface.
