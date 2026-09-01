import asyncio
import discord
from discord import app_commands

from services.i18n import locale_of, t
from services.ui_v2 import Panel, row

def setup_rolereaction_commands(bot, deps):
    globals().update(deps)
    # ===== REACTION ROLES (slash command) =====

    rolereaction_group = app_commands.Group(
        name="rolereaction",
        description="Manage reaction roles (admin/mod only)",
        default_permissions=discord.Permissions(manage_roles=True),
    )


    def _parse_emoji_input(s: str, guild: discord.Guild) -> str | None:
        """Accepts a unicode emoji or a custom emoji (form '<:name:id>' or just
        the emoji used in the server). Returns the canonical key."""
        import unicodedata as _ud
        if not s:
            return None
        s = s.strip()
        # NFC normalisation: some systems (macOS notably) send the emoji in a
        # decomposed form the Discord API does not recognise.
        s = _ud.normalize("NFC", s)
        # Strip stray invisible characters, but KEEP U+200D (ZWJ) because it is
        # required by composed emojis (families, jobs, gendered emojis). Only
        # ZWSP / ZWNJ / WJ / BOM are removed.
        for zw in ("​", "‌", "⁠", "﻿"):
            s = s.replace(zw, "")
        s = s.strip()
        if not s:
            return None
        # Already a valid Discord custom emoji
        if s.startswith("<") and s.endswith(">"):
            return s
        # Custom emoji by name (e.g. ":foo:") -> resolve through the guild
        if s.startswith(":") and s.endswith(":") and len(s) > 2:
            name = s[1:-1]
            for e in guild.emojis:
                if e.name == name:
                    return f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
            return None
        # Otherwise: unicode emoji (1+ characters)
        return s


    # ----- Interactive builder /rolereaction create -----

    class _RREmbedModal(discord.ui.Modal):
        heading = discord.ui.TextInput(label="Title", max_length=256)
        description = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, max_length=2000,
            required=False,
        )

        def __init__(self, parent_view):
            super().__init__(title=t("server.rolereaction.embed_modal_title", parent_view.locale))
            self.parent_view = parent_view
            loc = parent_view.locale
            self.heading.label = t("server.rolereaction.field_title", loc)
            self.heading.placeholder = t("server.rolereaction.field_title_ph", loc)
            self.description.label = t("server.rolereaction.field_description", loc)
            self.description.placeholder = t("server.rolereaction.field_description_ph", loc)
            # Pre-fill when already set
            self.heading.default     = parent_view.embed_title or ""
            self.description.default = parent_view.description or ""

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.embed_title  = self.heading.value
            self.parent_view.description  = self.description.value
            await self.parent_view.refresh(interaction)


    class _RREmojiModal(discord.ui.Modal):
        emoji = discord.ui.TextInput(
            label="Emoji",
            placeholder="🟢  or  :foo:  or  <:custom:1234567890>",
            max_length=80,
        )

        def __init__(self, parent_view):
            super().__init__(title=t("server.rolereaction.emoji_modal_title", parent_view.locale))
            self.parent_view = parent_view
            self.emoji.label = t("server.rolereaction.field_emoji", parent_view.locale)

        async def on_submit(self, interaction: discord.Interaction):
            loc = self.parent_view.locale
            ek = _parse_emoji_input(self.emoji.value, self.parent_view.guild)
            if not ek:
                await interaction.response.send_message(
                    t("server.rolereaction.invalid_emoji", loc), ephemeral=True)
                return
            # Anti-duplicate emoji inside the draft
            if any(m["emoji_key"] == ek for m in self.parent_view.mappings):
                await interaction.response.send_message(
                    t("server.rolereaction.duplicate_emoji", loc),
                    ephemeral=True,
                )
                return
            self.parent_view.pending_emoji_key     = ek
            self.parent_view.pending_emoji_display = self.emoji.value
            await self.parent_view.refresh(interaction)


    class _RoleReactionBuilder(discord.ui.LayoutView):
        """Interactive builder to create a multi-mapping reaction-role message."""

        def __init__(self, author_id: int, guild: discord.Guild, locale: str = "en"):
            super().__init__(timeout=900)  # 15 min
            self.author_id   = author_id
            self.guild       = guild
            self.locale      = locale
            self.channel: discord.TextChannel | None = None
            self.mode        = "toggle"
            self.delivery    = "reaction"   # reaction | button
            self.style       = "embed"      # embed | text
            self.embed_title = t("server.rolereaction.default_title", locale)
            self.description = t("server.rolereaction.default_description", locale)
            self.mappings: list[dict] = []  # [{emoji_key, emoji_display, role_id, role_name}]
            self.pending_emoji_key: str | None     = None
            self.pending_emoji_display: str | None = None
            self._rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    t("server.rolereaction.not_your_builder", self.locale),
                    ephemeral=True,
                )
                return False
            return True

        def _summary_panel(self) -> Panel:
            loc = self.locale
            p = Panel(t("server.rolereaction.builder_title", loc))
            none_txt = t("server.rolereaction.not_set", loc)
            p.field(
                t("server.rolereaction.field_config", loc),
                t(
                    "server.rolereaction.config_value", loc,
                    channel=self.channel.mention if self.channel else none_txt,
                    mode=self.mode,
                    delivery=t("server.rolereaction.delivery_buttons", loc) if self.delivery == "button"
                             else t("server.rolereaction.delivery_reactions", loc),
                    style=t("server.rolereaction.style_text", loc) if self.style == "text"
                          else t("server.rolereaction.style_embed", loc),
                    title=self.embed_title or "_—_",
                    description=(self.description[:100] + "…") if self.description and len(self.description) > 100
                                else (self.description or "_—_"),
                ),
                inline=False,
            )
            if self.mappings:
                lines = [f"{m['emoji_display']} → <@&{m['role_id']}>" for m in self.mappings]
                p.field(
                    t("server.rolereaction.field_mappings_n", loc, count=len(self.mappings)),
                    "\n".join(lines),
                    inline=False,
                )
            else:
                p.field(
                    t("server.rolereaction.field_mappings", loc),
                    t("server.rolereaction.no_mapping", loc),
                    inline=False,
                )
            if self.pending_emoji_display:
                p.field(
                    t("server.rolereaction.field_pending", loc),
                    t("server.rolereaction.pending_value", loc,
                      emoji=self.pending_emoji_display),
                    inline=False,
                )
            p.footer(t("server.rolereaction.builder_footer", loc))
            return p

        def _rebuild(self):
            loc = self.locale
            self.clear_items()
            self.add_item(self._summary_panel().container())
            # Row 0: ChannelSelect
            chan_sel = discord.ui.ChannelSelect(
                placeholder=t("server.rolereaction.select_channel", loc),
                channel_types=[discord.ChannelType.text],
                min_values=1, max_values=1,
            )
            async def _on_chan(interaction: discord.Interaction):
                self.channel = chan_sel.values[0].resolve() or self.guild.get_channel(chan_sel.values[0].id)
                await self.refresh(interaction)
            chan_sel.callback = _on_chan
            self.add_item(row(chan_sel))

            # Row 1: mode select
            mode_sel = discord.ui.Select(
                placeholder=t("server.rolereaction.select_mode", loc, mode=self.mode),
                options=[
                    discord.SelectOption(label=t("server.rolereaction.mode_toggle", loc),   value="toggle",   default=self.mode == "toggle"),
                    discord.SelectOption(label=t("server.rolereaction.mode_add_only", loc), value="add_only", default=self.mode == "add_only"),
                    discord.SelectOption(label=t("server.rolereaction.mode_unique", loc),   value="unique",   default=self.mode == "unique"),
                ],
            )
            async def _on_mode(interaction: discord.Interaction):
                self.mode = mode_sel.values[0]
                await self.refresh(interaction)
            mode_sel.callback = _on_mode
            self.add_item(row(mode_sel))

            # Row 2: RoleSelect (only visible when an emoji is pending)
            if self.pending_emoji_key:
                role_sel = discord.ui.RoleSelect(
                    placeholder=t("server.rolereaction.select_role", loc,
                                  emoji=self.pending_emoji_display),
                    min_values=1, max_values=1,
                )
                async def _on_role(interaction: discord.Interaction):
                    role: discord.Role = role_sel.values[0]
                    # Hierarchy check
                    if role >= self.guild.me.top_role:
                        await interaction.response.send_message(
                            t("server.rolereaction.role_too_high", loc, role=role.name),
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
                self.add_item(row(role_sel))

            # Row 3: config (title, delivery type, style)
            btn_embed = discord.ui.Button(label=t("server.rolereaction.btn_texts", loc),
                                           style=discord.ButtonStyle.secondary)
            async def _on_embed(interaction: discord.Interaction):
                await interaction.response.send_modal(_RREmbedModal(self))
            btn_embed.callback = _on_embed

            btn_delivery = discord.ui.Button(
                label=t("server.rolereaction.btn_delivery", loc,
                        delivery=t("server.rolereaction.delivery_buttons", loc) if self.delivery == "button"
                                 else t("server.rolereaction.delivery_reactions", loc)),
                style=discord.ButtonStyle.secondary)
            async def _on_delivery(interaction: discord.Interaction):
                self.delivery = "button" if self.delivery == "reaction" else "reaction"
                await self.refresh(interaction)
            btn_delivery.callback = _on_delivery

            btn_style = discord.ui.Button(
                label=t("server.rolereaction.btn_style", loc,
                        style=t("server.rolereaction.style_text", loc) if self.style == "text"
                              else t("server.rolereaction.style_embed", loc)),
                style=discord.ButtonStyle.secondary)
            async def _on_style(interaction: discord.Interaction):
                self.style = "text" if self.style == "embed" else "embed"
                await self.refresh(interaction)
            btn_style.callback = _on_style

            self.add_item(row(btn_embed, btn_delivery, btn_style))

            # Row 4: mapping + final actions
            btn_add = discord.ui.Button(label=t("server.rolereaction.btn_add_mapping", loc),
                                         style=discord.ButtonStyle.primary,
                                         disabled=bool(self.pending_emoji_key))
            async def _on_add(interaction: discord.Interaction):
                await interaction.response.send_modal(_RREmojiModal(self))
            btn_add.callback = _on_add

            btn_remove = discord.ui.Button(label=t("server.rolereaction.btn_undo", loc),
                                            style=discord.ButtonStyle.secondary,
                                            disabled=len(self.mappings) == 0)
            async def _on_remove(interaction: discord.Interaction):
                if self.mappings:
                    self.mappings.pop()
                await self.refresh(interaction)
            btn_remove.callback = _on_remove

            btn_send = discord.ui.Button(label=t("server.rolereaction.btn_send", loc),
                                          style=discord.ButtonStyle.success,
                                          disabled=not (self.channel and self.mappings))
            btn_send.callback = self._on_send

            btn_cancel = discord.ui.Button(label=t("server.rolereaction.btn_cancel", loc),
                                            style=discord.ButtonStyle.danger)
            async def _on_cancel(interaction: discord.Interaction):
                self.clear_items()
                # A V2 message cannot fall back to plain content: swap the view.
                await interaction.response.edit_message(
                    view=Panel(description=t("server.rolereaction.cancelled", loc)).view(timeout=None),
                )
                self.stop()
            btn_cancel.callback = _on_cancel

            self.add_item(row(btn_add, btn_remove, btn_send, btn_cancel))

        async def refresh(self, interaction: discord.Interaction):
            self._rebuild()
            if interaction.response.is_done():
                await interaction.edit_original_response(view=self)
            else:
                await interaction.response.edit_message(view=self)

        async def _on_send(self, interaction: discord.Interaction):
            if not self.channel or not self.mappings:
                return
            await interaction.response.defer(ephemeral=True)
            loc = self.locale

            use_buttons = self.delivery == "button"
            mapping_lines = [f"{m['emoji_display']} → <@&{m['role_id']}>" for m in self.mappings]
            footer = (t("server.rolereaction.footer_unique", loc)
                      if self.mode == "unique"
                      else (t("server.rolereaction.footer_buttons", loc)
                            if use_buttons
                            else t("server.rolereaction.footer_reactions", loc)))

            # ----- Build content / panel depending on the style -----
            content = None
            panel = None
            if self.style == "embed":
                panel = Panel(
                    self.embed_title or t("server.rolereaction.default_title", loc),
                    self.description or "",
                )
                # In button mode there is no need to list the emojis (buttons speak)
                if not use_buttons:
                    panel.field(t("server.rolereaction.available_reactions", loc),
                                "\n".join(mapping_lines), inline=False)
                panel.footer(footer)
            else:
                # Plain text message
                parts = []
                if self.embed_title:
                    parts.append(f"**{self.embed_title}**")
                if self.description:
                    parts.append(self.description)
                if not use_buttons:
                    parts.append("\n".join(mapping_lines))
                parts.append(f"_{footer}_")
                content = "\n\n".join(p for p in parts if p)

            # ----- Build the role buttons when needed -----
            # custom_id "rr:<role_id>" is stable: tasks/runtime.py dispatches on it.
            buttons = []
            if use_buttons:
                for m in self.mappings:
                    ek = m["emoji_key"]
                    try:
                        emoji_obj = (discord.PartialEmoji.from_str(ek)
                                     if ek.startswith("<") else ek)
                    except Exception:
                        emoji_obj = None
                    buttons.append(discord.ui.Button(
                        label=m["role_name"][:80],
                        emoji=emoji_obj,
                        style=discord.ButtonStyle.secondary,
                        custom_id=f"rr:{m['role_id']}",
                    ))
            try:
                if panel is not None:
                    # Components V2 message: no content, no embed.
                    # 5 buttons max per ActionRow.
                    button_rows = [row(*buttons[i:i + 5])
                                   for i in range(0, len(buttons), 5)]
                    msg = await self.channel.send(
                        view=panel.view(*button_rows, timeout=None))
                else:
                    # Plain text style: no panel, so the message keeps its
                    # `content` and the buttons stay in a classic View (a
                    # LayoutView would turn the message into V2, which forbids
                    # `content`).
                    view = None
                    if buttons:
                        view = discord.ui.View(timeout=None)
                        for b in buttons:
                            view.add_item(b)
                    msg = await self.channel.send(content=content, view=view)
            except discord.Forbidden:
                await interaction.followup.send(
                    t("server.rolereaction.post_forbidden", loc),
                    ephemeral=True,
                )
                return

            failed = []
            # ----- Reaction mode: add the emojis -----
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

            # Persist the mappings
            group_key = f"msg_{msg.id}" if self.mode == "unique" else None
            for m in self.mappings:
                db_rr_add(
                    self.guild.id, msg.id, self.channel.id, m["emoji_key"], m["role_id"],
                    mode=self.mode, group_key=group_key, created_by=self.author_id,
                    delivery=self.delivery, style=self.style,
                )

            confirm = t("server.rolereaction.posted", loc,
                        channel=self.channel.mention, message_id=msg.id,
                        count=len(self.mappings), mode=self.mode,
                        delivery=self.delivery, style=self.style)
            if failed:
                details = "\n".join(f"  · {d} — `{e}`" for d, e in failed)
                confirm += "\n\n" + t("server.rolereaction.reactions_failed", loc,
                                      count=len(failed), details=details)
            await interaction.edit_original_response(
                view=Panel(description=confirm).view(timeout=None),
            )
            self.stop()


    @rolereaction_group.command(
        name="create",
        description="Create a reaction-role message (interactive multi-emoji builder)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rr_create(interaction: discord.Interaction):
        view = _RoleReactionBuilder(interaction.user.id, interaction.guild, locale_of(interaction))
        await interaction.response.send_message(view=view, ephemeral=True)


    bot.tree.add_command(rolereaction_group)
