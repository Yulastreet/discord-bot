import asyncio
import discord
from discord import app_commands

import yt_dlp

from .music_voice import connect_to_voice


def _is_playlist_url(query):
    """True si l'URL est une playlist YouTube (contient list=...)."""
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


# Mapping nom emoji custom -> fallback unicode si introuvable
_PLATFORM_EMOJI_FALLBACK = {
    "youtube":    "▶️",
    "spotify":    "🎧",
    "soundcloud": "🟧",
    "bandcamp":   "🎵",
}


def _platform_emoji(bot, name):
    """Cherche emoji custom 'name' dans toutes les guilds du bot.
    Retourne string Discord (<:name:id>) ou fallback unicode."""
    name_low = (name or "").lower()
    for em in bot.emojis:
        if (em.name or "").lower() == name_low:
            return str(em)
    return _PLATFORM_EMOJI_FALLBACK.get(name_low, "•")

# Message friendly pour tous les soucis musique. Les vraies erreurs sont
# loggees pour debug owner. L'utilisateur final voit juste un message
# rassurant + pas de stack trace cryptique.
MUSIC_TROUBLE_MESSAGE = (
    "⚠️ **Cette fonctionnalité a quelques soucis pour l'instant.**\n"
    "On s'en occupe ! Réessaie dans quelques minutes ou tente une autre vidéo."
)


def setup_music_commands(bot, deps):
    globals().update(deps)
    # ===== MUSIQUE =====

    def _ensure_opus():
        """Ensure libopus is loaded. Re-tries load if not. Returns True on success."""
        if discord.opus.is_loaded():
            return True
        return _load_opus()

    @bot.tree.command(name="join", description="Rejoindre ton salon vocal")
    async def join(interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if not _ensure_opus():
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                print("[music /join] libopus non charge")
                return
            channel = interaction.user.voice.channel
            await connect_to_voice(bot, interaction.guild, channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(channel.id),
                            voice_channel_name=channel.name)
            await interaction.followup.send(f"✅ Connecté à **{channel.name}** !")
        except Exception as e:
            import traceback
            print(f"[music /join] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)

    @bot.tree.command(name="play", description="Jouer une musique (lien ou titre, playlist YouTube supportee)")
    @app_commands.describe(query="Titre, lien vidéo ou lien playlist YouTube")
    async def play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if not _ensure_opus():
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                print("[music /play] libopus non charge")
                return
            voice_channel = interaction.user.voice.channel
            vc = await connect_to_voice(bot, interaction.guild, voice_channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(voice_channel.id),
                            voice_channel_name=voice_channel.name)
            gid = str(interaction.guild.id)

            # --- Spotify (track / album / playlist) : resolve via API puis search YT ---
            if _is_spotify_url(query):
                await interaction.followup.send("🎧 Resolution Spotify en cours...")
                try:
                    from services.spotify_resolver import resolve_spotify_url
                    sp = await asyncio.to_thread(resolve_spotify_url, query, 100)
                except Exception as e:
                    print(f"[music spotify] error: {type(e).__name__}: {e}")
                    await interaction.followup.send(
                        "⚠️ Spotify n'est pas configure (clefs API manquantes) ou URL invalide.\n"
                        "Tu peux deja jouer SoundCloud, YouTube, Bandcamp via leur lien direct."
                    )
                    return
                tracks_meta = sp.get("tracks") or []
                if not tracks_meta:
                    await interaction.followup.send("❌ Aucune piste trouvee dans cette ressource Spotify.")
                    return

                # Strategie : 1er track full extract (lecture immediate avec
                # proxy+POT), le reste en background PARALLEL avec
                # get_audio_info_fast (scrape YT search HTML direct, ~200ms
                # par track, thread-safe car pas de plugin yt-dlp). play_next
                # re-resoudra l'URL en stream complet au moment de jouer.
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

                # 1er track : full extract pour demarrer lecture asap
                first_info = await _resolve_full(tracks_meta[0])
                if not first_info:
                    await interaction.followup.send("❌ Premier track Spotify pas trouvable sur YouTube.")
                    return
                music_queue_add(gid,
                                title=first_info["title"], url=first_info["url"],
                                source_url=first_info.get("source_url"),
                                duration=first_info.get("duration"),
                                thumbnail=first_info.get("thumbnail"),
                                requested_by=interaction.user.id)
                # Lance lecture immediate
                if vc.is_connected() and not vc.is_playing():
                    await play_next(vc, interaction.channel, interaction.guild.id)

                # Resolve le reste en PARALLEL via fast HTML scrape
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
                            cap_note = (
                                "\n⚠️ Limite Spotify : seules les **100 premieres** pistes "
                                "ont ete ajoutees (l'API publique cap a 100 par playlist sans OAuth)."
                            )
                        await interaction.followup.send(
                            f"🎧 **{added}** piste(s) ajoutee(s) depuis Spotify : **{sp.get('title','?')}**"
                            + cap_note
                        )
                    except Exception:
                        pass
                    print(f"[music spotify] parallel-fast resolve done : {added} tracks", flush=True)

                if len(tracks_meta) > 1:
                    asyncio.create_task(_resolve_rest())
                else:
                    await interaction.followup.send(
                        f"🎧 **1** piste ajoutee depuis Spotify : **{sp.get('title','?')}**"
                    )
                return

            # --- Playlist YouTube : on ajoute toutes les pistes ---
            if _is_playlist_url(query):
                await interaction.followup.send(f"🔍 Chargement de la playlist...")
                try:
                    pl = await get_playlist_info(query, max_items=200)
                except Exception as e:
                    print(f"[music] playlist error: {e}")
                    await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                    return
                entries = pl.get("entries") or []
                if not entries:
                    await interaction.followup.send("❌ Playlist vide ou inaccessible.")
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
                    f"📋 **{len(entries)}** pistes ajoutées depuis **{pl.get('playlist_title','Playlist')}**"
                )
                if vc.is_connected() and not vc.is_playing():
                    await play_next(vc, interaction.channel, interaction.guild.id)
                return

            # --- Track unique (titre ou lien vidéo) ---
            await interaction.followup.send(f"🔍 Recherche de **{query}**...")
            try:
                info = await get_audio_info(query)
            except Exception as e:
                print(f"[music] yt-dlp error: {e}")
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                return
            music_queue_add(gid,
                            title=info["title"], url=info["url"],
                            source_url=info.get("source_url"),
                            duration=info.get("duration"),
                            thumbnail=info.get("thumbnail"),
                            requested_by=interaction.user.id)
            await interaction.followup.send(f"✅ Ajouté à la file : **{info['title']}**")
            if vc.is_connected() and not vc.is_playing():
                await play_next(vc, interaction.channel, interaction.guild.id)
        except Exception as e:
            import traceback
            print(f"[music /play] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
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


    @bot.tree.command(name="nowplaying", description="Voir la musique en cours")
    async def nowplaying(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        st = music_state_get(gid) or {}
        title = st.get("current_title")
        if not title:
            await interaction.response.send_message("🔇 Aucune musique en cours.", ephemeral=True)
            return

        url = st.get("current_url") or ""
        duration = st.get("current_duration")
        thumb = st.get("current_thumbnail")
        started_at = st.get("started_at")
        is_paused = bool(st.get("is_paused"))
        voice_chan_name = st.get("voice_channel_name") or "?"

        # Calcule position si started_at present
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

        embed = discord.Embed(
            title=("⏸️ " if is_paused else "🎵 ") + title[:240],
            url=url if url.startswith("http") else None,
            color=discord.Color.blurple() if not is_paused else discord.Color.orange(),
        )
        if thumb:
            embed.set_thumbnail(url=thumb)
        embed.add_field(name="Durée", value=position_str, inline=True)
        embed.add_field(name="Salon vocal", value=f"🔊 {voice_chan_name}", inline=True)
        if progress_bar:
            embed.add_field(name="​", value=f"`{progress_bar}`", inline=False)

        # File suivante (3 premieres)
        try:
            q = music_queue_list(gid) or []
        except Exception:
            q = []
        if q:
            next_lines = []
            for i, t in enumerate(q[:3], 1):
                dur = _fmt_duration(t.get("duration")) if t.get("duration") else "—"
                next_lines.append(f"`{i}.` {t['title'][:60]} · `{dur}`")
            embed.add_field(
                name=f"🎶 À suivre ({len(q)} en file)",
                value="\n".join(next_lines) + (f"\n_… +{len(q)-3} autres_" if len(q) > 3 else ""),
                inline=False,
            )

        embed.set_footer(text="TookBot · /queue pour la file complète · /skip pour passer")
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="skip", description="Passer la musique en cours (ou jusqu'a une position de la file)")
    @app_commands.describe(position="Numero dans la file vers lequel sauter (1 = la prochaine). Vide = juste skip la courante.")
    async def skip(interaction: discord.Interaction, position: int = None):
        vc = interaction.guild.voice_client
        gid = str(interaction.guild.id)

        # Skip simple
        if position is None:
            if vc and vc.is_playing():
                vc.stop()
                await interaction.response.send_message("⏭️ Musique passée !")
            else:
                await interaction.response.send_message("❌ Aucune musique en cours !", ephemeral=True)
            return

        # Skip vers position N : pop (N-1) tracks silencieusement, puis stop la courante
        if position < 1:
            await interaction.response.send_message("❌ La position doit être >= 1.", ephemeral=True)
            return

        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message("❌ La file est vide.", ephemeral=True)
            return

        skipped_count = min(position - 1, len(q))
        # Pop silencieusement les tracks 1 a (position-1)
        for _ in range(skipped_count):
            music_queue_pop_next(gid)

        # Stop la courante pour declencher play_next sur ce qu'il reste
        was_playing = bool(vc and vc.is_playing())
        if vc and vc.is_playing():
            vc.stop()

        total_skipped = skipped_count + (1 if was_playing else 0)
        target_msg = ""
        if position > len(q):
            target_msg = f" (position {position} dépassait la file de {len(q)}, file vidée)"

        await interaction.response.send_message(
            f"⏭️ **{total_skipped}** musique(s) passée(s){target_msg}"
        )

    PER_PAGE = 50

    def _build_queue_embed(gid_str, page=1):
        q = music_queue_list(gid_str) or []
        total = len(q)
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = max(1, min(int(page), total_pages))
        start = (page - 1) * PER_PAGE
        end = min(start + PER_PAGE, total)
        embed = discord.Embed(
            title=f"🎵 File d'attente ({total} piste(s))",
            color=discord.Color.blurple(),
        )
        lines = []
        for i in range(start, end):
            t = q[i]
            lines.append(f"**{i+1}.** {t['title']}")
        embed.description = "\n".join(lines) or "*vide*"
        embed.set_footer(text=f"Page {page}/{total_pages} • /jump position:N pour jouer une piste")
        return embed, page, total_pages, total

    class QueueView(discord.ui.View):
        def __init__(self, gid_str, page, total_pages, author_id):
            super().__init__(timeout=180)
            self.gid_str = gid_str
            self.page = page
            self.total_pages = total_pages
            self.author_id = author_id
            self._refresh_buttons()

        def _refresh_buttons(self):
            self.prev_btn.disabled = self.page <= 1
            self.next_btn.disabled = self.page >= self.total_pages

        async def _update(self, interaction):
            embed, self.page, self.total_pages, _ = _build_queue_embed(self.gid_str, self.page)
            self._refresh_buttons()
            await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="◀ Precedent", style=discord.ButtonStyle.secondary)
        async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page = max(1, self.page - 1)
            await self._update(interaction)

        @discord.ui.button(label="Suivant ▶", style=discord.ButtonStyle.secondary)
        async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page = min(self.total_pages, self.page + 1)
            await self._update(interaction)

        @discord.ui.button(label="↻", style=discord.ButtonStyle.primary)
        async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Recalcule total (au cas ou queue a change)
            await self._update(interaction)

    @bot.tree.command(name="queue", description="Voir la file d'attente musicale")
    async def queue_cmd(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        q = music_queue_list(gid)
        if not q:
            await interaction.response.send_message("📭 La file d'attente est vide !")
            return
        embed, page, total_pages, _ = _build_queue_embed(gid, page=1)
        view = QueueView(gid, page, total_pages, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)


    @bot.tree.command(name="jump", description="Jouer une piste specifique de la file sans perdre les autres")
    @app_commands.describe(position="Position dans la file (1 = la prochaine deja en tete)")
    async def jump(interaction: discord.Interaction, position: int):
        if position < 1:
            await interaction.response.send_message("❌ La position doit être >= 1.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message("❌ La file est vide.", ephemeral=True)
            return
        if position > len(q):
            await interaction.response.send_message(
                f"❌ Position {position} hors limites (file = {len(q)} piste(s)).",
                ephemeral=True,
            )
            return
        target = q[position - 1]
        try:
            from database import music_queue_move_to_front
            ok = music_queue_move_to_front(gid, target["id"])
        except Exception as e:
            print(f"[music /jump] move err: {e}")
            await interaction.response.send_message("❌ Erreur reordonnancement.", ephemeral=True)
            return
        if not ok:
            await interaction.response.send_message("❌ Track introuvable.", ephemeral=True)
            return
        vc = interaction.guild.voice_client
        was_playing = bool(vc and vc.is_playing())
        if was_playing:
            vc.stop()  # declenche play_next sur la track maintenant en tete
        await interaction.response.send_message(
            f"⏯️ Saut vers **{target['title']}** (les autres pistes sont conservees)."
        )

    @bot.tree.command(name="volume", description="Regler le volume musique (0-200, default 100)")
    @app_commands.describe(niveau="Volume en pourcentage (0 = muet, 100 = normal, 200 = max)")
    async def volume(interaction: discord.Interaction, niveau: int):
        if niveau < 0 or niveau > 200:
            await interaction.response.send_message("❌ Volume entre 0 et 200.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        vol_f = niveau / 100.0
        try:
            from database import guild_setting_set
            guild_setting_set(gid, "music_volume", str(vol_f))
        except Exception as e:
            print(f"[music /volume] persist error: {e}")
        # Applique a la lecture en cours si possible
        vc = interaction.guild.voice_client
        applied_live = False
        if vc and vc.source and hasattr(vc.source, "volume"):
            try:
                vc.source.volume = vol_f
                applied_live = True
            except Exception as e:
                print(f"[music /volume] live apply error: {e}")
        suffix = " (applique en direct)" if applied_live else " (prochaine piste)"
        await interaction.response.send_message(f"🔊 Volume : **{niveau}%**{suffix}")

    @bot.tree.command(name="remove", description="Retirer une piste de la file par sa position")
    @app_commands.describe(position="Position dans la file (1 = la prochaine)")
    async def remove(interaction: discord.Interaction, position: int):
        if position < 1:
            await interaction.response.send_message("❌ La position doit être >= 1.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message("❌ La file est vide.", ephemeral=True)
            return
        if position > len(q):
            await interaction.response.send_message(
                f"❌ Position {position} hors limites (file = {len(q)} piste(s)).",
                ephemeral=True,
            )
            return
        track = q[position - 1]
        try:
            from database import music_queue_remove
            music_queue_remove(gid, track["id"])
        except Exception as e:
            print(f"[music /remove] error: {e}")
            await interaction.response.send_message(MUSIC_TROUBLE_MESSAGE, ephemeral=True)
            return
        await interaction.response.send_message(
            f"🗑️ Retire de la file : **{track['title'][:120]}** (position {position})"
        )

    # ----- View boutons plateformes pour /search -----
    class _PlatformPickerView(discord.ui.View):
        def __init__(self, bot, query, owner_id, voice_channel, guild, text_channel, _ensure_opus):
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
            self._build_buttons()

        def _build_buttons(self):
            specs = [
                ("youtube",    "YouTube",    discord.ButtonStyle.danger),
                ("spotify",    "Spotify",    discord.ButtonStyle.success),
                ("soundcloud", "SoundCloud", discord.ButtonStyle.primary),  # blue (orange pas dispo)
                ("bandcamp",   "Bandcamp",   discord.ButtonStyle.secondary),
            ]
            for plat, label, style in specs:
                em_str = _platform_emoji(self.bot, plat)
                # Discord button emoji : doit etre PartialEmoji ou str unicode.
                # Si custom <:name:id>, on extrait id via PartialEmoji.from_str.
                try:
                    emoji = discord.PartialEmoji.from_str(em_str) if em_str else None
                except Exception:
                    emoji = None
                btn = discord.ui.Button(label=f" {label}", style=style, custom_id=f"search:{plat}", emoji=emoji)
                btn.callback = self._make_cb(plat)
                self.add_item(btn)

        def _make_cb(self, platform):
            async def cb(inter: discord.Interaction):
                if inter.user.id != self.owner_id:
                    await inter.response.send_message("❌ Seul l'auteur peut choisir.", ephemeral=True)
                    return
                self.chosen = True
                # Disable les autres boutons
                for child in self.children:
                    child.disabled = True
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
                    # Format : [{title, artists, url(spotify), duration_ms, thumbnail, query}]
                    for r in sp_results:
                        results.append({
                            "title":    f"{r['title']} - {r['artists']}",
                            "url":      r["url"] or r.get("query"),
                            "duration": int((r.get("duration_ms") or 0) / 1000) or None,
                            "uploader": r.get("artists") or "",
                            "thumbnail": r.get("thumbnail"),
                            "spotify_query": r.get("query"),  # marqueur : on resoudra via YT
                        })
                elif platform == "bandcamp":
                    # Pas de native search bandcamp dans yt-dlp. Fallback : ytsearch sur "bandcamp <query>"
                    # puis on filtre les URLs bandcamp.com. Pas top mais marche pour les noms d'albums connus.
                    yt = await search_youtube(f"bandcamp {self.query}", max_results=10)
                    results = [r for r in yt if "bandcamp.com" in (r.get("url") or "")][:5]
                    if not results:
                        error_msg = ("Bandcamp n'a pas de recherche native dans yt-dlp. "
                                     "Colle directement une URL bandcamp.com dans `/play`.")
            except Exception as e:
                print(f"[music /search {platform}] {type(e).__name__}: {e}")
                error_msg = f"La recherche sur {platform} a échoué, réessaie."

            if error_msg:
                await inter.followup.send(f"⚠️ {error_msg}", ephemeral=True)
                return
            if not results:
                await inter.followup.send(f"❌ Aucun resultat {platform} pour `{self.query[:80]}`.", ephemeral=True)
                return
            await self._send_results_picker(inter, platform, results)

        async def _send_results_picker(self, inter, platform, results):
            options = []
            for i, r in enumerate(results):
                label = (r.get("title") or "(sans titre)")[:95]
                dur = _fmt_duration(r.get("duration")) if r.get("duration") else "?"
                up = (r.get("uploader") or "")[:50]
                desc = f"{up} · {dur}"[:95] if up else f"durée : {dur}"
                options.append(discord.SelectOption(label=label, value=str(i), description=desc))

            outer = self
            class ResultsView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)
                    self.picked = False

                @discord.ui.select(placeholder=f"Choisis une piste {platform}...", options=options)
                async def pick(self, sel_inter: discord.Interaction, sel: discord.ui.Select):
                    if sel_inter.user.id != outer.owner_id:
                        await sel_inter.response.send_message("❌ Ce menu n'est pas le tien.", ephemeral=True)
                        return
                    self.picked = True
                    idx = int(sel.values[0])
                    chosen = results[idx]
                    await sel_inter.response.defer()
                    try:
                        if not outer._ensure_opus():
                            await sel_inter.followup.send(MUSIC_TROUBLE_MESSAGE, ephemeral=True)
                            return
                        if not outer.voice_channel:
                            await sel_inter.followup.send("❌ Tu n'es plus dans un vocal.", ephemeral=True)
                            return
                        vc = await connect_to_voice(outer.bot, outer.guild, outer.voice_channel)
                        gid = str(outer.guild.id)
                        music_state_set(gid,
                                        voice_channel_id=str(outer.voice_channel.id),
                                        voice_channel_name=outer.voice_channel.name)
                        # Spotify : on doit resoudre via YouTube search
                        target_url = chosen.get("spotify_query") or chosen["url"]
                        info = await get_audio_info(target_url)
                        music_queue_add(gid,
                                        title=info["title"], url=info["url"],
                                        source_url=info.get("source_url"),
                                        duration=info.get("duration"),
                                        thumbnail=info.get("thumbnail") or chosen.get("thumbnail"),
                                        requested_by=outer.owner_id)
                        await sel_inter.followup.send(f"✅ Ajoute : **{info['title']}**")
                        if vc.is_connected() and not vc.is_playing():
                            await play_next(vc, outer.text_channel, outer.guild.id)
                    except Exception as e:
                        print(f"[music /search pick {platform}] {type(e).__name__}: {e}")
                        await sel_inter.followup.send(MUSIC_TROUBLE_MESSAGE, ephemeral=True)
                    self.stop()

            embed = discord.Embed(
                title=f"Résultats {platform} pour : {outer.query[:80]}",
                color=discord.Color.blurple(),
            )
            for i, r in enumerate(results, 1):
                dur = _fmt_duration(r.get("duration")) if r.get("duration") else "?"
                embed.add_field(
                    name=f"`{i}.` {(r.get('title') or '(sans titre)')[:80]}",
                    value=f"{(r.get('uploader') or 'Inconnu')[:40]} · `{dur}`",
                    inline=False,
                )
            await inter.followup.send(embed=embed, view=ResultsView())

        async def on_timeout(self):
            if not self.chosen and self._origin_interaction:
                try:
                    for child in self.children:
                        child.disabled = True
                    await self._origin_interaction.edit_original_response(
                        content="⌛ Choix de plateforme expiré (60s). Relance `/search`.",
                        view=self,
                    )
                except Exception:
                    pass

    @bot.tree.command(name="search", description="Chercher sur YouTube / SoundCloud / Spotify / Bandcamp")
    @app_commands.describe(query="Titre, artiste, ou mots-cles a chercher")
    async def search(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()

        # Quick preview : 1er hit YouTube pour avoir titre/artiste/thumbnail
        preview = None
        try:
            yt_top = await search_youtube(query, max_results=1)
            if yt_top:
                preview = yt_top[0]
        except Exception as e:
            print(f"[music /search preview] {e}")

        embed = discord.Embed(
            title=f"Recherche : {query[:120]}",
            description=(
                "Choisis la plateforme sur laquelle chercher.\n"
                "Le bot proposera 5 resultats au clic."
            ),
            color=discord.Color.blurple(),
        )
        if preview:
            embed.add_field(
                name="Aperçu (top YouTube)",
                value=f"**{(preview.get('title') or '?')[:120]}**\n"
                      f"{(preview.get('uploader') or 'Inconnu')[:60]} · "
                      f"`{_fmt_duration(preview.get('duration')) if preview.get('duration') else '?'}`",
                inline=False,
            )
            if preview.get("thumbnail"):
                embed.set_thumbnail(url=preview["thumbnail"])
        embed.set_footer(text="60s pour choisir une plateforme")

        view = _PlatformPickerView(
            bot=bot, query=query, owner_id=interaction.user.id,
            voice_channel=interaction.user.voice.channel,
            guild=interaction.guild, text_channel=interaction.channel,
            _ensure_opus=_ensure_opus,
        )
        await interaction.followup.send(embed=embed, view=view)
        view._origin_interaction = interaction

    @bot.tree.command(name="pause", description="Mettre la musique en pause")
    async def pause(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message("❌ Aucune musique en cours.", ephemeral=True)
            return
        vc.pause()
        music_state_set(str(interaction.guild.id), is_paused=1, is_playing=0)
        await interaction.response.send_message("⏸️ Musique en pause.")

    @bot.tree.command(name="resume", description="Reprendre la musique en pause")
    async def resume(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            await interaction.response.send_message("❌ Aucune musique en pause.", ephemeral=True)
            return
        vc.resume()
        music_state_set(str(interaction.guild.id), is_paused=0, is_playing=1)
        await interaction.response.send_message("▶️ Lecture reprise.")

    @bot.tree.command(name="musicstats", description="Top tracks / top auditeurs de ce serveur")
    @app_commands.describe(periode="Periode en jours (1-365, default 30)")
    async def musicstats(interaction: discord.Interaction, periode: int = 30):
        if periode < 1 or periode > 365:
            await interaction.response.send_message("❌ Periode entre 1 et 365 jours.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        try:
            from database import (music_stats_summary, music_stats_top_tracks,
                                  music_stats_top_requesters)
            summary = music_stats_summary(gid, periode)
            tops    = music_stats_top_tracks(gid, periode, 10)
            users   = music_stats_top_requesters(gid, periode, 5)
        except Exception as e:
            print(f"[music /musicstats] error: {e}")
            await interaction.response.send_message(MUSIC_TROUBLE_MESSAGE, ephemeral=True)
            return
        if not summary or not summary.get("total_plays"):
            await interaction.response.send_message(
                f"📊 Aucune lecture musicale sur les {periode} derniers jours."
            )
            return
        total_s = int(summary.get("total_seconds") or 0)
        h = total_s // 3600
        m = (total_s % 3600) // 60
        embed = discord.Embed(
            title=f"📊 Stats musique ({periode}j)",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Lectures", value=str(summary["total_plays"]), inline=True)
        embed.add_field(name="Pistes uniques", value=str(summary["unique_tracks"]), inline=True)
        embed.add_field(name="Auditeurs", value=str(summary["unique_users"]), inline=True)
        embed.add_field(name="Temps total", value=f"{h}h{m:02d}m", inline=True)
        by_src = summary.get("by_source") or []
        if by_src:
            embed.add_field(
                name="Par source",
                value="\n".join(f"`{s['source']}` : {s['plays']}" for s in by_src),
                inline=True,
            )
        if tops:
            top_lines = []
            for i, t in enumerate(tops, 1):
                title = (t["track_title"] or "?")[:60]
                top_lines.append(f"`{i}.` {title} · **{t['plays']}**")
            embed.add_field(name="🎵 Top pistes", value="\n".join(top_lines), inline=False)
        if users:
            user_lines = []
            for i, u in enumerate(users, 1):
                user_lines.append(f"`{i}.` <@{u['user_id']}> · **{u['plays']}**")
            embed.add_field(name="👤 Top auditeurs", value="\n".join(user_lines), inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="stop", description="Stopper la musique et vider la file")
    async def stop(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        music_queue_clear(gid)
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
        music_state_clear_current(gid)
        await interaction.response.send_message("⏹️ Musique stoppée et file vidée !")

    @bot.tree.command(name="leave", description="Quitter le salon vocal")
    async def leave(interaction: discord.Interaction):
        if interaction.guild.voice_client:
            gid = str(interaction.guild.id)
            music_queue_clear(gid)
            await interaction.guild.voice_client.disconnect()
            music_state_disconnect(gid)
            await interaction.response.send_message("👋 Déconnecté du salon vocal !")
        else:
            await interaction.response.send_message("❌ Je ne suis pas dans un salon vocal !", ephemeral=True)


    async def _resume_music():
        """Au boot, rejoindre les vocals connus + relancer la queue si elle n'est pas vide.
        Lit music_state pour chaque guild où le bot est présent."""
        from database import music_state_get, music_state_disconnect, music_queue_list
        for guild in bot.guilds:
            gid = str(guild.id)
            st = music_state_get(gid)
            if not st:
                continue
            ch_id = st.get("voice_channel_id")
            if not ch_id:
                continue
            # Le voice channel existe-t-il toujours ?
            channel = guild.get_channel(int(ch_id))
            if not channel or not isinstance(channel, discord.VoiceChannel):
                music_state_disconnect(gid)

                continue
            try:
                vc = await connect_to_voice(bot, guild, channel)
                print(f"[resume] {guild.name} : reconnecté à {channel.name}")
                # Relancer la queue si non vide
                if music_queue_list(gid):
                    await play_next(vc, None, guild.id)
            except Exception as e:
                print(f"[resume] {guild.name} : échec reconnexion {channel.name}: {e}")
                music_state_disconnect(gid)

    return _resume_music
