import asyncio
import discord
from discord import app_commands

from services.i18n import ti, t, locale_of
from services.ui_v2 import Panel

from .music_voice import connect_to_voice


def _is_playlist_url(query):
    """True if the URL is a YouTube playlist (contains list=...)."""
    if not isinstance(query, str) or not query.startswith("http"):
        return False
    q = query.lower()
    return ("youtube.com/playlist" in q or
            ("list=" in q and ("youtube.com" in q or "youtu.be" in q)))


def _is_soundcloud_url(query):
    if not isinstance(query, str):
        return False
    return "soundcloud.com/" in query.lower()


def _is_spotify_url(query):
    if not isinstance(query, str):
        return False
    return "open.spotify.com/" in query.lower()


# Custom emoji name -> unicode fallback when the emoji cannot be found
_PLATFORM_EMOJI_FALLBACK = {
    "youtube":    "▶️",
    "spotify":    "🎧",
    "soundcloud": "🟧",
    "bandcamp":   "🎵",
}


def _platform_emoji(bot, name):
    """Look for the custom emoji 'name' across every guild of the bot.
    Returns a Discord string (<:name:id>) or the unicode fallback."""
    name_low = (name or "").lower()
    for em in bot.emojis:
        if (em.name or "").lower() == name_low:
            return str(em)
    return _PLATFORM_EMOJI_FALLBACK.get(name_low, "•")

# Friendly message for every music hiccup ("games.music.trouble"). The real
# errors are logged so the owner can debug. The end user only sees a
# reassuring message instead of a cryptic stack trace.


def setup_music_commands(bot, deps):
    globals().update(deps)
    # ===== MUSIC =====

    def _ensure_opus():
        """Ensure libopus is loaded. Re-tries load if not. Returns True on success."""
        if discord.opus.is_loaded():
            return True
        return _load_opus()

    @bot.tree.command(name="join", description="Join your voice channel")
    async def join(interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message(
                ti(interaction, "games.music.not_in_voice"), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if not _ensure_opus():
                await interaction.followup.send(ti(interaction, "games.music.trouble"))
                print("[music /join] libopus not loaded")
                return
            channel = interaction.user.voice.channel
            await connect_to_voice(bot, interaction.guild, channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(channel.id),
                            voice_channel_name=channel.name)
            await interaction.followup.send(
                ti(interaction, "games.music.join.connected", channel=channel.name))
        except Exception as e:
            import traceback
            print(f"[music /join] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            await interaction.followup.send(ti(interaction, "games.music.trouble"))

    @bot.tree.command(name="play", description="Play a track (link or title, YouTube playlists supported)")
    @app_commands.describe(query="Title, video link or YouTube playlist link")
    async def play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message(
                ti(interaction, "games.music.not_in_voice"), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if not _ensure_opus():
                await interaction.followup.send(ti(interaction, "games.music.trouble"))
                print("[music /play] libopus not loaded")
                return
            voice_channel = interaction.user.voice.channel
            vc = await connect_to_voice(bot, interaction.guild, voice_channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(voice_channel.id),
                            voice_channel_name=voice_channel.name)
            gid = str(interaction.guild.id)

            # --- Spotify (track / album / playlist): resolve via API then search YT ---
            if _is_spotify_url(query):
                await interaction.followup.send(ti(interaction, "games.music.play.spotify_resolving"))
                try:
                    from services.spotify_resolver import resolve_spotify_url
                    sp = await asyncio.to_thread(resolve_spotify_url, query, 100)
                except Exception as e:
                    print(f"[music spotify] error: {type(e).__name__}: {e}")
                    await interaction.followup.send(
                        ti(interaction, "games.music.play.spotify_unavailable"))
                    return
                tracks_meta = sp.get("tracks") or []
                if not tracks_meta:
                    await interaction.followup.send(
                        ti(interaction, "games.music.play.spotify_no_track"))
                    return

                # Strategy: 1st track gets a full extract (immediate playback with
                # proxy+POT), the rest resolve in the background IN PARALLEL with
                # get_audio_info_fast (direct YT search HTML scrape, ~200ms per
                # track, thread-safe since no yt-dlp plugin). play_next will
                # re-resolve the URL into a full stream when it actually plays.
                async def _resolve_full(tm):
                    q = tm.get("query")
                    if not q:
                        return None
                    try:
                        return await get_audio_info(q)
                    except Exception as e:
                        print(f"[music spotify] full search fail for {q!r}: {e}")
                        return None

                async def _resolve_fast(tm):
                    q = tm.get("query")
                    if not q:
                        return None
                    try:
                        from services.yt_fast_search import yt_search_fast
                        return await yt_search_fast(q)
                    except Exception as e:
                        print(f"[music spotify] fast search fail for {q!r}: {e}")
                        return None

                # 1st track: full extract so playback starts asap
                first_info = await _resolve_full(tracks_meta[0])
                if not first_info:
                    await interaction.followup.send(
                        ti(interaction, "games.music.play.spotify_first_missing"))
                    return
                music_queue_add(gid,
                                title=first_info["title"], url=first_info["url"],
                                source_url=first_info.get("source_url"),
                                duration=first_info.get("duration"),
                                thumbnail=first_info.get("thumbnail"),
                                requested_by=interaction.user.id)
                # Start playback right away
                if vc.is_connected() and not vc.is_playing():
                    await play_next(vc, interaction.channel, interaction.guild.id)

                # Resolve the rest IN PARALLEL via the fast HTML scrape
                async def _resolve_rest():
                    sem = asyncio.Semaphore(10)
                    rest = tracks_meta[1:]

                    async def _bound(tm):
                        async with sem:
                            return await _resolve_fast(tm)

                    infos = await asyncio.gather(*[_bound(tm) for tm in rest])
                    added = 1
                    for info in infos:
                        if not info:
                            continue
                        try:
                            music_queue_add(gid,
                                            title=info["title"], url=info["url"],
                                            source_url=info.get("source_url"),
                                            duration=info.get("duration"),
                                            thumbnail=info.get("thumbnail"),
                                            requested_by=interaction.user.id)
                            added += 1
                        except Exception as e:
                            print(f"[music spotify] queue_add err: {e}")
                    try:
                        cap_note = ""
                        if sp.get("spotify_cap") and added >= 100:
                            cap_note = ti(interaction, "games.music.play.spotify_cap_note")
                        await interaction.followup.send(
                            ti(interaction, "games.music.play.spotify_added",
                               count=added, title=sp.get("title", "?"))
                            + cap_note
                        )
                    except Exception:
                        pass
                    print(f"[music spotify] parallel-fast resolve done: {added} tracks", flush=True)

                if len(tracks_meta) > 1:
                    asyncio.create_task(_resolve_rest())
                else:
                    await interaction.followup.send(
                        ti(interaction, "games.music.play.spotify_added",
                           count=1, title=sp.get("title", "?"))
                    )
                return

            # --- YouTube playlist: add every track ---
            if _is_playlist_url(query):
                await interaction.followup.send(ti(interaction, "games.music.play.playlist_loading"))
                try:
                    pl = await get_playlist_info(query, max_items=200)
                except Exception as e:
                    print(f"[music] playlist error: {e}")
                    await interaction.followup.send(ti(interaction, "games.music.trouble"))
                    return
                entries = pl.get("entries") or []
                if not entries:
                    await interaction.followup.send(ti(interaction, "games.music.play.playlist_empty"))
                    return
                for entry in entries:
                    music_queue_add(gid,
                                    title=entry["title"],
                                    url=entry["url"],
                                    source_url=entry.get("source_url"),
                                    duration=entry.get("duration"),
                                    thumbnail=entry.get("thumbnail"),
                                    requested_by=interaction.user.id)
                await interaction.followup.send(
                    ti(interaction, "games.music.play.playlist_added",
                       count=len(entries), title=pl.get("playlist_title", "Playlist"))
                )
                if vc.is_connected() and not vc.is_playing():
                    await play_next(vc, interaction.channel, interaction.guild.id)
                return

            # --- Single track (title or video link) ---
            await interaction.followup.send(
                ti(interaction, "games.music.play.searching", query=query))
            try:
                info = await get_audio_info(query)
            except Exception as e:
                print(f"[music] yt-dlp error: {e}")
                await interaction.followup.send(ti(interaction, "games.music.trouble"))
                return
            music_queue_add(gid,
                            title=info["title"], url=info["url"],
                            source_url=info.get("source_url"),
                            duration=info.get("duration"),
                            thumbnail=info.get("thumbnail"),
                            requested_by=interaction.user.id)
            await interaction.followup.send(
                ti(interaction, "games.music.play.added", title=info["title"]))
            if vc.is_connected() and not vc.is_playing():
                await play_next(vc, interaction.channel, interaction.guild.id)
        except Exception as e:
            import traceback
            print(f"[music /play] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send(ti(interaction, "games.music.trouble"))
            except Exception:
                pass


    def _fmt_duration(seconds):
        if not seconds or seconds <= 0:
            return "—"
        s = int(seconds)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m{sec:02d}s"
        return f"{m}:{sec:02d}"


    @bot.tree.command(name="nowplaying", description="Show the track currently playing")
    async def nowplaying(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        st = music_state_get(gid) or {}
        title = st.get("current_title")
        if not title:
            await interaction.response.send_message(
                ti(interaction, "games.music.nowplaying.nothing"), ephemeral=True)
            return

        url = st.get("current_url") or ""
        duration = st.get("current_duration")
        thumb = st.get("current_thumbnail")
        started_at = st.get("started_at")
        is_paused = bool(st.get("is_paused"))
        voice_chan_name = st.get("voice_channel_name") or "?"

        # Compute the position when started_at is available
        position_str = "—"
        progress_bar = ""
        if started_at:
            try:
                import datetime as _dt
                if "T" in started_at:
                    start_dt = _dt.datetime.fromisoformat(started_at)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=_dt.timezone.utc)
                    now = _dt.datetime.now(_dt.timezone.utc)
                    elapsed = int((now - start_dt).total_seconds())
                    if elapsed >= 0 and duration:
                        elapsed = min(elapsed, int(duration))
                        position_str = f"{_fmt_duration(elapsed)} / {_fmt_duration(duration)}"
                        # Progress bar 20 chars
                        ratio = elapsed / max(1, int(duration))
                        filled = int(ratio * 20)
                        progress_bar = "▰" * filled + "▱" * (20 - filled)
                    elif duration:
                        position_str = f"— / {_fmt_duration(duration)}"
            except Exception:
                pass
        if position_str == "—" and duration:
            position_str = _fmt_duration(duration)

        head = ("⏸️ " if is_paused else "🎵 ") + title[:240]
        if url.startswith("http"):
            head = f"[{head}]({url})"
        p = Panel(head)
        if thumb:
            p.thumbnail(thumb)
        p.field(ti(interaction, "games.music.nowplaying.duration"),
                position_str, inline=True)
        p.field(ti(interaction, "games.music.nowplaying.voice_channel"),
                f"🔊 {voice_chan_name}", inline=True)
        if progress_bar:
            p.text(f"`{progress_bar}`")

        # Up next (first 3)
        try:
            q = music_queue_list(gid) or []
        except Exception:
            q = []
        if q:
            next_lines = []
            for i, t_item in enumerate(q[:3], 1):
                dur = _fmt_duration(t_item.get("duration")) if t_item.get("duration") else "—"
                next_lines.append(f"`{i}.` {t_item['title'][:60]} · `{dur}`")
            more = (ti(interaction, "games.music.nowplaying.more", count=len(q) - 3)
                    if len(q) > 3 else "")
            p.field(
                ti(interaction, "games.music.nowplaying.up_next", count=len(q)),
                "\n".join(next_lines) + more,
            )

        p.footer(ti(interaction, "games.music.nowplaying.footer"))
        await interaction.response.send_message(view=p.view())

    @bot.tree.command(name="skip", description="Skip the current track (or jump to a queue position)")
    @app_commands.describe(position="Queue number to jump to (1 = the next one). Empty = just skip the current track.")
    async def skip(interaction: discord.Interaction, position: int = None):
        vc = interaction.guild.voice_client
        gid = str(interaction.guild.id)

        # Plain skip
        if position is None:
            if vc and vc.is_playing():
                vc.stop()
                await interaction.response.send_message(ti(interaction, "games.music.skip.done"))
            else:
                await interaction.response.send_message(
                    ti(interaction, "games.music.skip.nothing_playing"), ephemeral=True)
            return

        # Skip to position N: silently pop (N-1) tracks, then stop the current one
        if position < 1:
            await interaction.response.send_message(
                ti(interaction, "games.music.position_min"), ephemeral=True)
            return

        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message(
                ti(interaction, "games.music.queue_empty"), ephemeral=True)
            return

        skipped_count = min(position - 1, len(q))
        # Silently pop tracks 1 to (position-1)
        for _ in range(skipped_count):
            music_queue_pop_next(gid)

        # Stop the current track to trigger play_next on whatever is left
        was_playing = bool(vc and vc.is_playing())
        if vc and vc.is_playing():
            vc.stop()

        total_skipped = skipped_count + (1 if was_playing else 0)
        target_msg = ""
        if position > len(q):
            target_msg = ti(interaction, "games.music.skip.overflow_note",
                            position=position, total=len(q))

        await interaction.response.send_message(
            ti(interaction, "games.music.skip.done_count", count=total_skipped, note=target_msg)
        )

    PER_PAGE = 50

    def _build_queue_panel(gid_str, page=1, locale=None):
        q = music_queue_list(gid_str) or []
        total = len(q)
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = max(1, min(int(page), total_pages))
        start = (page - 1) * PER_PAGE
        end = min(start + PER_PAGE, total)
        lines = []
        for i in range(start, end):
            track = q[i]
            lines.append(f"**{i+1}.** {track['title']}")
        p = Panel(
            t("games.music.queue.title", locale, total=total),
            "\n".join(lines) or t("games.music.queue.empty_line", locale),
        )
        p.footer(t("games.music.queue.footer", locale,
                   page=page, total_pages=total_pages))
        return p, page, total_pages, total

    class _QueueRow(discord.ui.ActionRow):
        @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
        async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            v = self.view
            v.page = max(1, v.page - 1)
            await v._update(interaction)

        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
        async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            v = self.view
            v.page = min(v.total_pages, v.page + 1)
            await v._update(interaction)

        @discord.ui.button(label="↻", style=discord.ButtonStyle.primary)
        async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Recompute the total (the queue may have changed)
            await self.view._update(interaction)

    class QueueView(discord.ui.LayoutView):
        def __init__(self, gid_str, page, total_pages, author_id, locale):
            super().__init__(timeout=180)
            self.gid_str = gid_str
            self.page = page
            self.total_pages = total_pages
            self.author_id = author_id
            self.locale = locale
            self.pager = _QueueRow()
            self.pager.prev_btn.label = t("games.music.queue.previous_button", locale)
            self.pager.next_btn.label = t("games.music.queue.next_button", locale)
            self.panel, self.page, self.total_pages, _ = _build_queue_panel(
                gid_str, self.page, locale)
            self._rebuild()

        def _refresh_buttons(self):
            self.pager.prev_btn.disabled = self.page <= 1
            self.pager.next_btn.disabled = self.page >= self.total_pages

        def _rebuild(self):
            self.clear_items()
            self.add_item(self.panel.container())
            self._refresh_buttons()
            self.add_item(self.pager)

        async def _update(self, interaction):
            self.panel, self.page, self.total_pages, _ = _build_queue_panel(
                self.gid_str, self.page, self.locale)
            self._rebuild()
            await interaction.response.edit_message(view=self)

    @bot.tree.command(name="queue", description="Show the music queue")
    async def queue_cmd(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        q = music_queue_list(gid)
        if not q:
            await interaction.response.send_message(ti(interaction, "games.music.queue.empty"))
            return
        locale = locale_of(interaction)
        _p, page, total_pages, _ = _build_queue_panel(gid, page=1, locale=locale)
        view = QueueView(gid, page, total_pages, interaction.user.id, locale)
        await interaction.response.send_message(view=view)


    @bot.tree.command(name="jump", description="Play a specific queue track without losing the others")
    @app_commands.describe(position="Position in the queue (1 = the next one already on top)")
    async def jump(interaction: discord.Interaction, position: int):
        if position < 1:
            await interaction.response.send_message(
                ti(interaction, "games.music.position_min"), ephemeral=True)
            return
        gid = str(interaction.guild.id)
        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message(
                ti(interaction, "games.music.queue_empty"), ephemeral=True)
            return
        if position > len(q):
            await interaction.response.send_message(
                ti(interaction, "games.music.position_out_of_range",
                   position=position, total=len(q)),
                ephemeral=True,
            )
            return
        target = q[position - 1]
        try:
            from database import music_queue_move_to_front
            ok = music_queue_move_to_front(gid, target["id"])
        except Exception as e:
            print(f"[music /jump] move err: {e}")
            await interaction.response.send_message(
                ti(interaction, "games.music.jump.reorder_error"), ephemeral=True)
            return
        if not ok:
            await interaction.response.send_message(
                ti(interaction, "games.music.jump.track_not_found"), ephemeral=True)
            return
        vc = interaction.guild.voice_client
        was_playing = bool(vc and vc.is_playing())
        if was_playing:
            vc.stop()  # triggers play_next on the track now at the top
        await interaction.response.send_message(
            ti(interaction, "games.music.jump.done", title=target["title"])
        )

    @bot.tree.command(name="volume", description="Set the music volume (0-200, default 100)")
    @app_commands.describe(level="Volume in percent (0 = muted, 100 = normal, 200 = max)")
    async def volume(interaction: discord.Interaction, level: int):
        if level < 0 or level > 200:
            await interaction.response.send_message(
                ti(interaction, "games.music.volume.out_of_range"), ephemeral=True)
            return
        gid = str(interaction.guild.id)
        vol_f = level / 100.0
        try:
            from database import guild_setting_set
            guild_setting_set(gid, "music_volume", str(vol_f))
        except Exception as e:
            print(f"[music /volume] persist error: {e}")
        # Apply to the current playback when possible
        vc = interaction.guild.voice_client
        applied_live = False
        if vc and vc.source and hasattr(vc.source, "volume"):
            try:
                vc.source.volume = vol_f
                applied_live = True
            except Exception as e:
                print(f"[music /volume] live apply error: {e}")
        suffix = (ti(interaction, "games.music.volume.applied_live") if applied_live
                  else ti(interaction, "games.music.volume.next_track"))
        await interaction.response.send_message(
            ti(interaction, "games.music.volume.set", level=level, suffix=suffix))

    @bot.tree.command(name="remove", description="Remove a track from the queue by its position")
    @app_commands.describe(position="Position in the queue (1 = the next one)")
    async def remove(interaction: discord.Interaction, position: int):
        if position < 1:
            await interaction.response.send_message(
                ti(interaction, "games.music.position_min"), ephemeral=True)
            return
        gid = str(interaction.guild.id)
        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message(
                ti(interaction, "games.music.queue_empty"), ephemeral=True)
            return
        if position > len(q):
            await interaction.response.send_message(
                ti(interaction, "games.music.position_out_of_range",
                   position=position, total=len(q)),
                ephemeral=True,
            )
            return
        track = q[position - 1]
        try:
            from database import music_queue_remove
            music_queue_remove(gid, track["id"])
        except Exception as e:
            print(f"[music /remove] error: {e}")
            await interaction.response.send_message(
                ti(interaction, "games.music.trouble"), ephemeral=True)
            return
        await interaction.response.send_message(
            ti(interaction, "games.music.remove.done",
               title=track["title"][:120], position=position)
        )

    # ----- Platform button view for /search -----
    class _PlatformPickerView(discord.ui.LayoutView):
        def __init__(self, bot, query, owner_id, voice_channel, guild, text_channel,
                     _ensure_opus, panel):
            super().__init__(timeout=60)
            self.bot = bot
            self.query = query
            self.owner_id = owner_id
            self.voice_channel = voice_channel
            self.guild = guild
            self.text_channel = text_channel
            self._ensure_opus = _ensure_opus
            self.chosen = False
            self._origin_interaction = None
            self.panel = panel
            self.buttons_row = discord.ui.ActionRow()
            self._build_buttons()
            self._rebuild()

        def _rebuild(self):
            self.clear_items()
            self.add_item(self.panel.container())
            self.add_item(self.buttons_row)

        def _disable_buttons(self):
            for child in self.buttons_row.children:
                child.disabled = True

        def _build_buttons(self):
            specs = [
                ("youtube",    "YouTube",    discord.ButtonStyle.danger),
                ("spotify",    "Spotify",    discord.ButtonStyle.success),
                ("soundcloud", "SoundCloud", discord.ButtonStyle.primary),  # blue (orange not available)
                ("bandcamp",   "Bandcamp",   discord.ButtonStyle.secondary),
            ]
            for plat, label, style in specs:
                em_str = _platform_emoji(self.bot, plat)
                # Discord button emoji: must be a PartialEmoji or a unicode str.
                # For a custom <:name:id>, the id is extracted via PartialEmoji.from_str.
                try:
                    emoji = discord.PartialEmoji.from_str(em_str) if em_str else None
                except Exception:
                    emoji = None
                btn = discord.ui.Button(label=f" {label}", style=style, custom_id=f"search:{plat}", emoji=emoji)
                btn.callback = self._make_cb(plat)
                self.buttons_row.add_item(btn)

        def _make_cb(self, platform):
            async def cb(inter: discord.Interaction):
                if inter.user.id != self.owner_id:
                    await inter.response.send_message(
                        ti(inter, "games.music.search.not_author"), ephemeral=True)
                    return
                self.chosen = True
                # Disable the other buttons
                self._disable_buttons()
                try:
                    await inter.message.edit(view=self)
                except Exception:
                    pass
                await inter.response.defer()
                await self._run_search(inter, platform)
            return cb

        async def _run_search(self, inter, platform):
            results = []
            error_msg = None
            try:
                if platform == "youtube":
                    results = await search_youtube(self.query, max_results=5)
                elif platform == "soundcloud":
                    results = await search_soundcloud(self.query, max_results=5)
                elif platform == "spotify":
                    from services.spotify_resolver import search_spotify
                    sp_results = await asyncio.to_thread(search_spotify, self.query, 5)
                    # Format: [{title, artists, url(spotify), duration_ms, thumbnail, query}]
                    for r in sp_results:
                        results.append({
                            "title":    f"{r['title']} - {r['artists']}",
                            "url":      r["url"] or r.get("query"),
                            "duration": int((r.get("duration_ms") or 0) / 1000) or None,
                            "uploader": r.get("artists") or "",
                            "thumbnail": r.get("thumbnail"),
                            "spotify_query": r.get("query"),  # marker: will be resolved through YT
                        })
                elif platform == "bandcamp":
                    # No native bandcamp search in yt-dlp. Fallback: ytsearch on "bandcamp <query>"
                    # then filter the bandcamp.com URLs. Not great but works for known album names.
                    yt = await search_youtube(f"bandcamp {self.query}", max_results=10)
                    results = [r for r in yt if "bandcamp.com" in (r.get("url") or "")][:5]
                    if not results:
                        error_msg = ti(inter, "games.music.search.bandcamp_no_native")
            except Exception as e:
                print(f"[music /search {platform}] {type(e).__name__}: {e}")
                error_msg = ti(inter, "games.music.search.failed", platform=platform)

            if error_msg:
                await inter.followup.send(f"⚠️ {error_msg}", ephemeral=True)
                return
            if not results:
                await inter.followup.send(
                    ti(inter, "games.music.search.no_result",
                       platform=platform, query=self.query[:80]),
                    ephemeral=True)
                return
            await self._send_results_picker(inter, platform, results)

        async def _send_results_picker(self, inter, platform, results):
            loc = locale_of(inter)
            untitled = t("games.music.search.untitled", loc)
            unknown_uploader = t("games.music.search.unknown_uploader", loc)
            options = []
            for i, r in enumerate(results):
                label = (r.get("title") or untitled)[:95]
                dur = _fmt_duration(r.get("duration")) if r.get("duration") else "?"
                up = (r.get("uploader") or "")[:50]
                desc = (f"{up} · {dur}"[:95] if up
                        else t("games.music.search.duration_only", loc, duration=dur))
                options.append(discord.SelectOption(label=label, value=str(i), description=desc))

            outer = self

            class _ResultsRow(discord.ui.ActionRow):
                @discord.ui.select(
                    placeholder=t("games.music.search.select_placeholder", loc, platform=platform),
                    options=options,
                )
                async def pick(self, sel_inter: discord.Interaction, sel: discord.ui.Select):
                    if sel_inter.user.id != outer.owner_id:
                        await sel_inter.response.send_message(
                            ti(sel_inter, "games.music.search.menu_not_yours"), ephemeral=True)
                        return
                    self.view.picked = True
                    idx = int(sel.values[0])
                    chosen = results[idx]
                    await sel_inter.response.defer()
                    try:
                        if not outer._ensure_opus():
                            await sel_inter.followup.send(
                                ti(sel_inter, "games.music.trouble"), ephemeral=True)
                            return
                        if not outer.voice_channel:
                            await sel_inter.followup.send(
                                ti(sel_inter, "games.music.search.left_voice"), ephemeral=True)
                            return
                        vc = await connect_to_voice(outer.bot, outer.guild, outer.voice_channel)
                        gid = str(outer.guild.id)
                        music_state_set(gid,
                                        voice_channel_id=str(outer.voice_channel.id),
                                        voice_channel_name=outer.voice_channel.name)
                        # Spotify: must be resolved through a YouTube search
                        target_url = chosen.get("spotify_query") or chosen["url"]
                        info = await get_audio_info(target_url)
                        music_queue_add(gid,
                                        title=info["title"], url=info["url"],
                                        source_url=info.get("source_url"),
                                        duration=info.get("duration"),
                                        thumbnail=info.get("thumbnail") or chosen.get("thumbnail"),
                                        requested_by=outer.owner_id)
                        await sel_inter.followup.send(
                            ti(sel_inter, "games.music.search.added", title=info["title"]))
                        if vc.is_connected() and not vc.is_playing():
                            await play_next(vc, outer.text_channel, outer.guild.id)
                    except Exception as e:
                        print(f"[music /search pick {platform}] {type(e).__name__}: {e}")
                        await sel_inter.followup.send(
                            ti(sel_inter, "games.music.trouble"), ephemeral=True)
                    self.view.stop()

            class ResultsView(discord.ui.LayoutView):
                def __init__(self, panel):
                    super().__init__(timeout=60)
                    self.picked = False
                    self.add_item(panel.container())
                    self.add_item(_ResultsRow())

            p = Panel(t("games.music.search.results_title", loc,
                        platform=platform, query=outer.query[:80]))
            for i, r in enumerate(results, 1):
                dur = _fmt_duration(r.get("duration")) if r.get("duration") else "?"
                p.field(
                    f"`{i}.` {(r.get('title') or untitled)[:80]}",
                    f"{(r.get('uploader') or unknown_uploader)[:40]} · `{dur}`",
                )
            await inter.followup.send(view=ResultsView(p))

        async def on_timeout(self):
            if not self.chosen and self._origin_interaction:
                try:
                    self._disable_buttons()
                    # A V2 message has no `content`: the notice goes into the panel.
                    self.panel.text(
                        ti(self._origin_interaction, "games.music.search.timeout"))
                    self._rebuild()
                    await self._origin_interaction.edit_original_response(view=self)
                except Exception:
                    pass

    @bot.tree.command(name="search", description="Search on YouTube / SoundCloud / Spotify / Bandcamp")
    @app_commands.describe(query="Title, artist, or keywords to search for")
    async def search(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message(
                ti(interaction, "games.music.not_in_voice"), ephemeral=True)
            return
        await interaction.response.defer()

        # Quick preview: top YouTube hit, for the title/artist/thumbnail
        preview = None
        try:
            yt_top = await search_youtube(query, max_results=1)
            if yt_top:
                preview = yt_top[0]
        except Exception as e:
            print(f"[music /search preview] {e}")

        p = Panel(
            ti(interaction, "games.music.search.title", query=query[:120]),
            ti(interaction, "games.music.search.description"),
        )
        if preview:
            unknown_uploader = ti(interaction, "games.music.search.unknown_uploader")
            p.field(
                ti(interaction, "games.music.search.preview"),
                f"**{(preview.get('title') or '?')[:120]}**\n"
                f"{(preview.get('uploader') or unknown_uploader)[:60]} · "
                f"`{_fmt_duration(preview.get('duration')) if preview.get('duration') else '?'}`",
            )
            if preview.get("thumbnail"):
                p.thumbnail(preview["thumbnail"])
        p.footer(ti(interaction, "games.music.search.footer"))

        view = _PlatformPickerView(
            bot=bot, query=query, owner_id=interaction.user.id,
            voice_channel=interaction.user.voice.channel,
            guild=interaction.guild, text_channel=interaction.channel,
            _ensure_opus=_ensure_opus, panel=p,
        )
        await interaction.followup.send(view=view)
        view._origin_interaction = interaction

    @bot.tree.command(name="pause", description="Pause the music")
    async def pause(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message(
                ti(interaction, "games.music.pause.nothing_playing"), ephemeral=True)
            return
        vc.pause()
        music_state_set(str(interaction.guild.id), is_paused=1, is_playing=0)
        await interaction.response.send_message(ti(interaction, "games.music.pause.done"))

    @bot.tree.command(name="resume", description="Resume the paused music")
    async def resume(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            await interaction.response.send_message(
                ti(interaction, "games.music.resume.nothing_paused"), ephemeral=True)
            return
        vc.resume()
        music_state_set(str(interaction.guild.id), is_paused=0, is_playing=1)
        await interaction.response.send_message(ti(interaction, "games.music.resume.done"))

    @bot.tree.command(name="musicstats", description="Top tracks / top listeners of this server")
    @app_commands.describe(days="Period in days (1-365, default 30)")
    async def musicstats(interaction: discord.Interaction, days: int = 30):
        if days < 1 or days > 365:
            await interaction.response.send_message(
                ti(interaction, "games.music.stats.bad_period"), ephemeral=True)
            return
        gid = str(interaction.guild.id)
        try:
            from database import (music_stats_summary, music_stats_top_tracks,
                                  music_stats_top_requesters)
            summary = music_stats_summary(gid, days)
            tops    = music_stats_top_tracks(gid, days, 10)
            users   = music_stats_top_requesters(gid, days, 5)
        except Exception as e:
            print(f"[music /musicstats] error: {e}")
            await interaction.response.send_message(
                ti(interaction, "games.music.trouble"), ephemeral=True)
            return
        if not summary or not summary.get("total_plays"):
            await interaction.response.send_message(
                ti(interaction, "games.music.stats.no_data", days=days)
            )
            return
        total_s = int(summary.get("total_seconds") or 0)
        h = total_s // 3600
        m = (total_s % 3600) // 60
        p = Panel(ti(interaction, "games.music.stats.title", days=days))
        p.field(ti(interaction, "games.music.stats.plays"),
                str(summary["total_plays"]), inline=True)
        p.field(ti(interaction, "games.music.stats.unique_tracks"),
                str(summary["unique_tracks"]), inline=True)
        p.field(ti(interaction, "games.music.stats.listeners"),
                str(summary["unique_users"]), inline=True)
        p.field(ti(interaction, "games.music.stats.total_time"),
                f"{h}h{m:02d}m", inline=True)
        by_src = summary.get("by_source") or []
        if by_src:
            p.field(
                ti(interaction, "games.music.stats.by_source"),
                "\n".join(f"`{s['source']}`: {s['plays']}" for s in by_src),
            )
        if tops:
            top_lines = []
            for i, tr in enumerate(tops, 1):
                title = (tr["track_title"] or "?")[:60]
                top_lines.append(f"`{i}.` {title} · **{tr['plays']}**")
            p.field(ti(interaction, "games.music.stats.top_tracks"),
                    "\n".join(top_lines))
        if users:
            user_lines = []
            for i, u in enumerate(users, 1):
                user_lines.append(f"`{i}.` <@{u['user_id']}> · **{u['plays']}**")
            p.field(ti(interaction, "games.music.stats.top_listeners"),
                    "\n".join(user_lines))
        await interaction.response.send_message(view=p.view())

    @bot.tree.command(name="stop", description="Stop the music and clear the queue")
    async def stop(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        music_queue_clear(gid)
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
        music_state_clear_current(gid)
        await interaction.response.send_message(ti(interaction, "games.music.stop.done"))

    @bot.tree.command(name="leave", description="Leave the voice channel")
    async def leave(interaction: discord.Interaction):
        if interaction.guild.voice_client:
            gid = str(interaction.guild.id)
            music_queue_clear(gid)
            await interaction.guild.voice_client.disconnect()
            music_state_disconnect(gid)
            await interaction.response.send_message(ti(interaction, "games.music.leave.done"))
        else:
            await interaction.response.send_message(
                ti(interaction, "games.music.leave.not_connected"), ephemeral=True)


    async def _resume_music():
        """On boot, rejoin the known voice channels and restart the queue when it is not empty.
        Reads music_state for every guild the bot is in."""
        from database import music_state_get, music_state_disconnect, music_queue_list
        for guild in bot.guilds:
            gid = str(guild.id)
            st = music_state_get(gid)
            if not st:
                continue
            ch_id = st.get("voice_channel_id")
            if not ch_id:
                continue
            # Does the voice channel still exist?
            channel = guild.get_channel(int(ch_id))
            if not channel or not isinstance(channel, discord.VoiceChannel):
                music_state_disconnect(gid)

                continue
            try:
                vc = await connect_to_voice(bot, guild, channel)
                print(f"[resume] {guild.name}: reconnected to {channel.name}")
                # Restart the queue when it is not empty
                if music_queue_list(gid):
                    await play_next(vc, None, guild.id)
            except Exception as e:
                print(f"[resume] {guild.name}: reconnect to {channel.name} failed: {e}")
                music_state_disconnect(gid)

    return _resume_music
