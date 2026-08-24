"""Bot Customization: gives each guild a custom nick + avatar + banner.

Paid feature (TookBot+). Gated through has_premium_grant or user_has_active_entitlement
on SKU_TOOKBOT_PLUS.
"""

import os
from flask import render_template, request, jsonify, g, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename

# Direct DB imports (not through deps: these functions are specific to this page).
from database import (
    guild_bot_profile_get, guild_bot_profile_set,
    guild_bot_profile_mark_applied, guild_bot_profile_clear,
    guild_bot_profile_list_all,
    has_premium_grant, user_has_active_entitlement,
)
from services.i18n import t


# Repo root used to store uploads
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_UPLOAD_DIR = os.path.join(_ROOT, "uploads", "bot_profile")
os.makedirs(_UPLOAD_DIR, exist_ok=True)

_ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


def _ext_ok(filename):
    return "." in filename and filename.rsplit(".", 1)[-1].lower() in _ALLOWED_EXT


def register_bot_profile_routes(app, deps):
    globals().update(deps)

    def _is_tookbot_plus(uid):
        """Check whether the user has TookBot+ (manual owner grant or Discord entitlement)."""
        try:
            # inherit_all=False: a feature="all" grant (Premium levels) must NOT
            # unlock TookBot+. The 3 paid features (levels / pass / tookbot_plus)
            # stay independent.
            if has_premium_grant(uid, feature="tookbot_plus", inherit_all=False):
                return True
            sku = os.getenv("SKU_TOOKBOT_PLUS", "").strip() or None
            if sku and user_has_active_entitlement(uid, sku_id=sku):
                return True
            owner_id = os.getenv("DISCORD_OWNER_ID", "").strip() or None
            if owner_id and str(uid) == str(owner_id):
                return True
        except Exception:
            return False
        return False

    @app.route("/bot-profile")
    def bot_profile_page():
        uid = _current_user_id() if "_current_user_id" in globals() else ((g.discord_user.get("user_id") or g.discord_user.get("id")) if g.discord_user else None)
        if not uid:
            return redirect("/")
        # ?preview=1 forces the paywall even for owner / subscribers (handy for QA)
        force_paywall = request.args.get("preview") in ("1", "true")
        return render_template(
            "bot_profile.html",
            is_premium=(False if force_paywall else _is_tookbot_plus(uid)),
            user=session_user() if "session_user" in globals() else (g.discord_user or {}),
            active_nav="bot-profile",
        )

    @app.route("/api/bot-profile", methods=["GET"])
    def api_bot_profile_get():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        p = guild_bot_profile_get(g_id) or {}
        return jsonify(p)

    @app.route("/api/bot-profile", methods=["POST"])
    def api_bot_profile_set():
        try:
            return _api_bot_profile_set_impl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": t("api.bot_profile.server_error")}), 500

    def _api_bot_profile_set_impl():
        uid = (g.discord_user.get("user_id") or g.discord_user.get("id")) if g.discord_user else None
        if not uid or not _is_tookbot_plus(uid):
            return jsonify({"error": t("api.bot_profile.plus_required")}), 402
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400

        # multipart/form-data: nick + bio + avatar (file) + banner (file)
        # per-guild bio = experimental (the Discord endpoint is not officially
        # documented for bots). We still send it in the PATCH; if Discord
        # refuses, the body is surfaced in the response.
        nick = (request.form.get("nick") or "").strip()
        bio  = (request.form.get("bio")  or "").strip()

        avatar_path = None
        banner_path = None
        for field, label in (("avatar", "avatar"), ("banner", "banner")):
            f = request.files.get(field)
            if f and f.filename:
                if not _ext_ok(f.filename):
                    return jsonify({"error": t("api.bot_profile.unsupported_extension", field=label)}), 400
                f.stream.seek(0, 2); size = f.stream.tell(); f.stream.seek(0)
                if size > _MAX_SIZE:
                    return jsonify({"error": t("api.bot_profile.file_too_large", field=label)}), 400
                safe = secure_filename(f"{g_id}_{label}_{f.filename}")
                dest = os.path.join(_UPLOAD_DIR, safe)
                f.save(dest)
                if label == "avatar": avatar_path = dest
                else:                 banner_path = dest

        # Save metadata in DB (relative urls for the web preview)
        kw = {}
        if nick:           kw["nick"] = nick
        if bio:            kw["about_me"] = bio
        if avatar_path:    kw["avatar_url"] = "/uploads/bot_profile/" + os.path.basename(avatar_path)
        if banner_path:    kw["banner_url"] = "/uploads/bot_profile/" + os.path.basename(banner_path)
        guild_bot_profile_set(g_id, **kw)

        # Apply through the Discord API (sync via asyncio.run inside the helper)
        from services.bot_personalizer import apply_profile_sync
        token = os.getenv("DISCORD_TOKEN", "")
        if not token:
            return jsonify({"error": t("api.bot_profile.missing_discord_token")}), 500
        try:
            status_resp, body = apply_profile_sync(
                token, g_id,
                nick=nick or None,
                bio=bio or None,
                avatar_path=avatar_path,
                banner_path=banner_path,
            )
        except Exception as e:
            print(f"[bot_profile apply] err: {type(e).__name__}: {e}")
            return jsonify({"error": t("api.bot_profile.apply_failed")}), 500

        if status_resp in (200, 204):
            guild_bot_profile_mark_applied(g_id, applied_by=uid)
            return jsonify({"ok": True, "status": status_resp})
        return jsonify({"ok": False, "status": status_resp, "body": body}), 502

    @app.route("/api/bot-profile/reset", methods=["POST"])
    def api_bot_profile_reset():
        uid = (g.discord_user.get("user_id") or g.discord_user.get("id")) if g.discord_user else None
        if not uid or not _is_tookbot_plus(uid):
            return jsonify({"error": t("api.bot_profile.plus_required")}), 402
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        from services.bot_personalizer import apply_profile_sync
        token = os.getenv("DISCORD_TOKEN", "")
        try:
            status, body = apply_profile_sync(
                token, g_id,
                nick="",  # reset back to the bot's real name
                bio="",   # reset the per-guild bio (Discord accepts "" = clear)
                clear_avatar=True, clear_banner=True,
            )
        except Exception as e:
            print(f"[bot_profile reset] err: {type(e).__name__}: {e}")
            return jsonify({"error": t("api.bot_profile.reset_failed")}), 500
        guild_bot_profile_clear(g_id)
        return jsonify({"ok": status in (200, 204), "status": status, "body": body})

    @app.route("/uploads/bot_profile/<path:filename>")
    def uploads_bot_profile(filename):
        return send_from_directory(_UPLOAD_DIR, filename, max_age=300)
