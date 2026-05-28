import asyncio
import discord
from discord import app_commands

def setup_rolereaction_commands(bot, deps):
    globals().update(deps)
    # ===== REACTION ROLES (commande slash) =====

    rolereaction_group = app_commands.Group(
        name="rolereaction",
        description="Gérer les rôles-réaction (admin/modo uniquement)",
        default_permissions=discord.Permissions(manage_roles=True),
    )


    def _parse_emoji_input(s: str, guild: discord.Guild) -> str | None:
        """Accepte un emoji unicode ou un custom emoji (forme '<:name:id>' ou
        juste l'emoji utilise dans le serveur). Renvoie la cle canonique."""
        import unicodedata as _ud
        if not s:
            return None
        s = s.strip()
        # Normalisation NFC : certains OS (notamment macOS) envoient l'emoji en
        # forme decomposee qui n'est pas reconnue par l'API Discord.
        s = _ud.normalize("NFC", s)
        # Strip zero-width chars parasites — ATTENTION : on GARDE U+200D (ZWJ)
        # car il est essentiel aux sequences emoji composees comme 🧗‍♂️ ou 👨‍👩‍👧‍👦.
        # ZWSP / ZWNJ / WJ / BOM seulement sont retires.
        for zw in ("​", "‌", "⁠", "﻿"):
            s = s.replace(zw, "")
        s = s.strip()
        if not s:
            return None
        # Custom emoji format Discord deja correct
        if s.startswith("<") and s.endswith(">"):
            return s
        # Custom emoji par nom (ex: ":foo:") -> resoudre via guild
        if s.startswith(":") and s.endswith(":") and len(s) > 2:
            name = s[1:-1]
            for e in guild.emojis:
                if e.name == name:
                    return f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
            return None
        # Sinon : emoji unicode (1+ caracteres)
        return s


    # ----- Builder interactif /rolereaction create -----

    class _RREmbedModal(discord.ui.Modal, title="Configurer l'embed"):
        titre = discord.ui.TextInput(label="Titre", max_length=256,
                                      placeholder="Ex: Choisis ton rôle de couleur")
        description = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, max_length=2000,
            placeholder="Texte affiché sous le titre (markdown OK)",
            required=False,
        )

        def __init__(self, parent_view):
            super().__init__()
            self.parent_view = parent_view
            # Pre-remplir si deja saisi
            self.titre.default       = parent_view.titre or ""
            self.description.default = parent_view.description or ""

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.titre       = self.titre.value
            self.parent_view.description = self.description.value
            await self.parent_view.refresh(interaction)


    class _RREmojiModal(discord.ui.Modal, title="Emoji du nouveau mapping"):
        emoji = discord.ui.TextInput(
            label="Emoji",
            placeholder="🟢  ou  :foo:  ou  <:custom:1234567890>",
            max_length=80,
        )

        def __init__(self, parent_view):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            ek = _parse_emoji_input(self.emoji.value, self.parent_view.guild)
            if not ek:
                await interaction.response.send_message("❌ Emoji invalide.", ephemeral=True)
                return
            # Anti-doublon emoji dans le draft
            if any(m["emoji_key"] == ek for m in self.parent_view.mappings):
                await interaction.response.send_message(
                    "❌ Cet emoji est déjà mappé. Retire-le d'abord ou utilise un autre.",
                    ephemeral=True,
                )
                return
            self.parent_view.pending_emoji_key     = ek
            self.parent_view.pending_emoji_display = self.emoji.value
            await self.parent_view.refresh(interaction)


    class _RoleReactionBuilder(discord.ui.View):
        """Builder interactif pour creer un message rôle-réaction multi-mapping."""

        def __init__(self, author_id: int, guild: discord.Guild):
            super().__init__(timeout=900)  # 15 min
            self.author_id   = author_id
            self.guild       = guild
            self.salon: discord.TextChannel | None = None
            self.mode        = "toggle"
            self.delivery    = "reaction"   # reaction | button
            self.style       = "embed"      # embed | text
            self.titre       = "Choisis ton rôle"
            self.description = "Réagis pour obtenir un rôle."
            self.mappings: list[dict] = []  # [{emoji_key, emoji_display, role_id, role_name}]
            self.pending_emoji_key: str | None     = None
            self.pending_emoji_display: str | None = None
            self._rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "❌ Ce builder n'est pas le tien. Lance `/rolereaction create` pour le tien.",
                    ephemeral=True,
                )
                return False
            return True

        def _summary_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="🎭 Builder Rôle-Réaction",
                color=0xC8F050,
            )
            embed.add_field(
                name="⚙️ Configuration",
                value=(
                    f"📍 **Salon :** {self.salon.mention if self.salon else '_non défini_'}\n"
                    f"⚡ **Mode :** `{self.mode}`\n"
                    f"🔘 **Type :** `{'boutons' if self.delivery == 'button' else 'réactions'}`\n"
                    f"🎨 **Affichage :** `{'message normal' if self.style == 'text' else 'embed'}`\n"
                    f"📝 **Titre :** {self.titre or '_—_'}\n"
                    f"📄 **Description :** {(self.description[:100] + '…') if self.description and len(self.description) > 100 else (self.description or '_—_')}"
                ),
                inline=False,
            )
            if self.mappings:
                lines = [f"{m['emoji_display']} → <@&{m['role_id']}>" for m in self.mappings]
                embed.add_field(
                    name=f"🎯 Mappings ({len(self.mappings)})",
                    value="\n".join(lines),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="🎯 Mappings",
                    value="_Aucun mapping. Clique sur **➕ Ajouter un mapping**._",
                    inline=False,
                )
            if self.pending_emoji_display:
                embed.add_field(
                    name="⏳ En attente",
                    value=f"Emoji {self.pending_emoji_display} en attente — choisis le rôle dans le menu ci-dessous.",
                    inline=False,
                )
            embed.set_footer(text="Le builder expire dans 15 minutes.")
            return embed

        def _rebuild(self):
            self.clear_items()
            # Row 0 : ChannelSelect
            chan_sel = discord.ui.ChannelSelect(
                placeholder="📍 Salon de destination…",
                channel_types=[discord.ChannelType.text],
                min_values=1, max_values=1, row=0,
            )
            async def _on_chan(interaction: discord.Interaction):
                self.salon = chan_sel.values[0].resolve() or self.guild.get_channel(chan_sel.values[0].id)
                await self.refresh(interaction)
            chan_sel.callback = _on_chan
            self.add_item(chan_sel)

            # Row 1 : Mode select
            mode_sel = discord.ui.Select(
                placeholder=f"⚡ Mode : {self.mode}",
                options=[
                    discord.SelectOption(label="toggle (ajout + retrait)",       value="toggle",   default=self.mode == "toggle"),
                    discord.SelectOption(label="add_only (ajout seulement)",     value="add_only", default=self.mode == "add_only"),
                    discord.SelectOption(label="unique (1 seul rôle du groupe)", value="unique",   default=self.mode == "unique"),
                ],
                row=1,
            )
            async def _on_mode(interaction: discord.Interaction):
                self.mode = mode_sel.values[0]
                await self.refresh(interaction)
            mode_sel.callback = _on_mode
            self.add_item(mode_sel)

            # Row 2 : RoleSelect (visible seulement si emoji en attente)
            if self.pending_emoji_key:
                role_sel = discord.ui.RoleSelect(
                    placeholder=f"🏷️ Rôle pour {self.pending_emoji_display}…",
                    min_values=1, max_values=1, row=2,
                )
                async def _on_role(interaction: discord.Interaction):
                    role: discord.Role = role_sel.values[0]
                    # Verif hierarchie
                    if role >= self.guild.me.top_role:
                        await interaction.response.send_message(
                            f"❌ Mon rôle est en dessous de **{role.name}**, je ne pourrai pas l'attribuer.",
                            ephemeral=True,
                        )
                        return
                    self.mappings.append({
                        "emoji_key":     self.pending_emoji_key,
                        "emoji_display": self.pending_emoji_display,
                        "role_id":       role.id,
                        "role_name":     role.name,
                    })
                    self.pending_emoji_key     = None
                    self.pending_emoji_display = None
                    await self.refresh(interaction)
                role_sel.callback = _on_role
                self.add_item(role_sel)

            # Row 3 : config (titre, type, style)
            btn_embed = discord.ui.Button(label="📝 Titre & description",
                                           style=discord.ButtonStyle.secondary, row=3)
            async def _on_embed(interaction: discord.Interaction):
                await interaction.response.send_modal(_RREmbedModal(self))
            btn_embed.callback = _on_embed
            self.add_item(btn_embed)

            btn_delivery = discord.ui.Button(
                label=f"🔘 Type : {'boutons' if self.delivery == 'button' else 'réactions'}",
                style=discord.ButtonStyle.secondary, row=3)
            async def _on_delivery(interaction: discord.Interaction):
                self.delivery = "button" if self.delivery == "reaction" else "reaction"
                await self.refresh(interaction)
            btn_delivery.callback = _on_delivery
            self.add_item(btn_delivery)

            btn_style = discord.ui.Button(
                label=f"🎨 Affichage : {'normal' if self.style == 'text' else 'embed'}",
                style=discord.ButtonStyle.secondary, row=3)
            async def _on_style(interaction: discord.Interaction):
                self.style = "text" if self.style == "embed" else "embed"
                await self.refresh(interaction)
            btn_style.callback = _on_style
            self.add_item(btn_style)

            # Row 4 : mapping + actions finales
            btn_add = discord.ui.Button(label="➕ Mapping",
                                         style=discord.ButtonStyle.primary, row=4,
                                         disabled=bool(self.pending_emoji_key))
            async def _on_add(interaction: discord.Interaction):
                await interaction.response.send_modal(_RREmojiModal(self))
            btn_add.callback = _on_add
            self.add_item(btn_add)

            btn_remove = discord.ui.Button(label="↩️ Retirer",
                                            style=discord.ButtonStyle.secondary, row=4,
                                            disabled=len(self.mappings) == 0)
            async def _on_remove(interaction: discord.Interaction):
                if self.mappings:
                    self.mappings.pop()
                await self.refresh(interaction)
            btn_remove.callback = _on_remove
            self.add_item(btn_remove)

            btn_send = discord.ui.Button(label="✅ Envoyer",
                                          style=discord.ButtonStyle.success, row=4,
                                          disabled=not (self.salon and self.mappings))
            btn_send.callback = self._on_send
            self.add_item(btn_send)

            btn_cancel = discord.ui.Button(label="❌ Annuler",
                                            style=discord.ButtonStyle.danger, row=4)
            async def _on_cancel(interaction: discord.Interaction):
                self.clear_items()
                await interaction.response.edit_message(
                    content="❌ Builder annulé.", embed=None, view=None,
                )
                self.stop()
            btn_cancel.callback = _on_cancel
            self.add_item(btn_cancel)

        async def refresh(self, interaction: discord.Interaction):
            self._rebuild()
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=self._summary_embed(), view=self)
            else:
                await interaction.response.edit_message(embed=self._summary_embed(), view=self)

        async def _on_send(self, interaction: discord.Interaction):
            if not self.salon or not self.mappings:
                return
            await interaction.response.defer(ephemeral=True)

            use_buttons = self.delivery == "button"
            mapping_lines = [f"{m['emoji_display']} → <@&{m['role_id']}>" for m in self.mappings]
            footer = ("Tu ne peux choisir qu'UN seul rôle parmi ceux-ci."
                      if self.mode == "unique"
                      else ("Clique un bouton pour recevoir le rôle correspondant."
                            if use_buttons
                            else "Réagis pour recevoir le rôle correspondant."))

            # ----- Construire content / embed selon le style -----
            content = None
            embed = None
            if self.style == "embed":
                embed = discord.Embed(
                    title=self.titre or "Choisis ton rôle",
                    description=self.description or "",
                    color=0xC8F050,
                )
                # En mode boutons, pas besoin de lister les emojis (les boutons parlent)
                if not use_buttons:
                    embed.add_field(name="Réactions disponibles",
                                    value="\n".join(mapping_lines), inline=False)
                embed.set_footer(text=footer)
            else:
                # Message normal (texte)
                parts = []
                if self.titre:
                    parts.append(f"**{self.titre}**")
                if self.description:
                    parts.append(self.description)
                if not use_buttons:
                    parts.append("\n".join(mapping_lines))
                parts.append(f"_{footer}_")
                content = "\n\n".join(p for p in parts if p)

            # ----- Construire la View boutons si besoin -----
            view = None
            if use_buttons:
                view = discord.ui.View(timeout=None)
                for m in self.mappings:
                    ek = m["emoji_key"]
                    try:
                        emoji_obj = (discord.PartialEmoji.from_str(ek)
                                     if ek.startswith("<") else ek)
                    except Exception:
                        emoji_obj = None
                    view.add_item(discord.ui.Button(
                        label=m["role_name"][:80],
                        emoji=emoji_obj,
                        style=discord.ButtonStyle.secondary,
                        custom_id=f"rr:{m['role_id']}",
                    ))

            try:
                msg = await self.salon.send(content=content, embed=embed, view=view)
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Permissions manquantes dans ce salon (envoyer messages).",
                    ephemeral=True,
                )
                return

            failed = []
            # ----- Mode réactions : ajoute les emojis -----
            if not use_buttons:
                async def _try_add(emoji_str):
                    if emoji_str.startswith("<"):
                        await msg.add_reaction(discord.PartialEmoji.from_str(emoji_str))
                        return None
                    base = emoji_str.replace("️", "")
                    variants = [emoji_str, base, base + "️"]
                    seen = set()
                    last_err = None
                    for v in variants:
                        if v in seen or not v:
                            continue
                        seen.add(v)
                        try:
                            await msg.add_reaction(v)
                            return None
                        except discord.HTTPException as e:
                            last_err = e
                            continue
                    return last_err

                for m in self.mappings:
                    ek = m["emoji_key"]
                    try:
                        err = await _try_add(ek)
                        if err:
                            raise err
                    except Exception as e:
                        print(f"[rolereaction] add_reaction {ek!r} err: {e!r}")
                        failed.append((m["emoji_display"], str(e)))
                    await asyncio.sleep(0.35)

            # Persiste les mappings
            group_key = f"msg_{msg.id}" if self.mode == "unique" else None
            for m in self.mappings:
                db_rr_add(
                    self.guild.id, msg.id, self.salon.id, m["emoji_key"], m["role_id"],
                    mode=self.mode, group_key=group_key, created_by=self.author_id,
                    delivery=self.delivery, style=self.style,
                )

            confirm = (
                f"✅ Message rôle-réaction posté dans {self.salon.mention}\n"
                f"`message_id = {msg.id}` · {len(self.mappings)} mapping(s) · "
                f"mode `{self.mode}` · type `{self.delivery}` · affichage `{self.style}`"
            )
            if failed:
                details = "\n".join(f"  · {d} — `{e}`" for d, e in failed)
                confirm += f"\n\n⚠️ **{len(failed)} réaction(s) non ajoutée(s) :**\n{details}"
            await interaction.edit_original_response(
                content=confirm, embed=None, view=None,
            )
            self.stop()


    @rolereaction_group.command(
        name="create",
        description="Créer un message rôle-réaction (builder interactif multi-emoji)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rr_create(interaction: discord.Interaction):
        view = _RoleReactionBuilder(interaction.user.id, interaction.guild)
        await interaction.response.send_message(
            embed=view._summary_embed(), view=view, ephemeral=True,
        )


    bot.tree.add_command(rolereaction_group)
