import asyncio
import discord
from discord import app_commands

from services.i18n import DEFAULT_LOCALE, guild_locale, locale_of, t, ti
from services.ui_v2 import Panel, row

def setup_ticket_commands(bot, deps):
    globals().update(deps)
    # ===== TICKETS =====

    _BUTTON_STYLES = {
        "primary":   discord.ButtonStyle.primary,
        "success":   discord.ButtonStyle.success,
        "danger":    discord.ButtonStyle.danger,
        "secondary": discord.ButtonStyle.secondary,
    }

    # custom_id -> i18n key of the button label. The custom_ids are stable and
    # MUST NOT change: they identify the buttons of already posted messages.
    _CONTROL_LABEL_KEYS = {
        "ticket:claim":  "server.ticket.btn_claim",
        "ticket:close":  "server.ticket.btn_close",
        "ticket:reopen": "server.ticket.btn_reopen",
        "ticket:delete": "server.ticket.btn_delete",
    }


    class TicketOpenView(discord.ui.LayoutView):
        """Persistent view of the ticket panel. Stable custom_id.

        ``panel`` is the Components V2 panel shown above the button. It is
        omitted when the view is only (re)registered for dispatch.
        """

        def __init__(self, label: str = "Open a ticket", emoji: str = "🎫",
                     style: discord.ButtonStyle = discord.ButtonStyle.primary,
                     panel: Panel | None = None):
            super().__init__(timeout=None)
            btn = discord.ui.Button(
                label=label, emoji=emoji, style=style,
                custom_id="ticket:open",
            )
            btn.callback = self._on_open
            if panel is not None:
                self.add_item(panel.container())
            self.add_item(row(btn))

        async def _on_open(self, interaction: discord.Interaction):
            if not interaction.guild or not interaction.message:
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.guild_only"), ephemeral=True)
                return
            panel = ticket_panel_get_by_message(interaction.guild.id, interaction.message.id)
            if not panel or not panel.get("enabled"):
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.panel_missing"),
                    ephemeral=True,
                )
                return

            # Anti-duplicate: one open ticket per user / panel
            existing = ticket_get_open_by_user(
                interaction.guild.id, interaction.user.id, panel_id=panel["id"],
            )
            if existing:
                ch = interaction.guild.get_channel(int(existing["channel_id"]))
                if ch:
                    await interaction.response.send_message(
                        ti(interaction, "server.ticket.already_open", channel=ch.mention),
                        ephemeral=True,
                    )
                    return
                # The channel is gone but the ticket is flagged open: close it
                ticket_set_status(existing["id"], "deleted")

            await interaction.response.defer(ephemeral=True)

            # Build overwrites
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, attach_files=True, embed_links=True,
                    read_message_history=True,
                ),
                interaction.guild.me: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, manage_channels=True,
                    manage_messages=True, embed_links=True, attach_files=True,
                ),
            }
            support_role = None
            if panel.get("support_role_id"):
                support_role = interaction.guild.get_role(int(panel["support_role_id"]))
                if support_role:
                    overwrites[support_role] = discord.PermissionOverwrite(
                        read_messages=True, send_messages=True, manage_messages=True,
                        embed_links=True, attach_files=True, read_message_history=True,
                    )

            category = None
            if panel.get("category_id"):
                category = interaction.guild.get_channel(int(panel["category_id"]))
                if category and not isinstance(category, discord.CategoryChannel):
                    category = None

            # Unique name: ticket-username-N
            base_name = f"ticket-{interaction.user.name}".lower()[:90]
            # Discord channel names sanitization
            import re as _re
            base_name = _re.sub(r"[^a-z0-9-]", "-", base_name).strip("-") or "ticket"

            try:
                ticket_channel = await interaction.guild.create_text_channel(
                    name=base_name,
                    category=category,
                    overwrites=overwrites,
                    topic=f"Ticket from {interaction.user} (id {interaction.user.id})",
                    reason=f"Ticket opened by {interaction.user}",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    ti(interaction, "server.ticket.create_forbidden"),
                    ephemeral=True,
                )
                return
            except Exception as e:
                await interaction.followup.send(
                    ti(interaction, "server.ticket.create_error", error=type(e).__name__),
                    ephemeral=True,
                )
                print(f"[ticket] create err: {e!r}")
                return

            ticket_id = ticket_create(
                interaction.guild.id, panel["id"], interaction.user.id, ticket_channel.id,
            )

            # Welcome message + ControlView
            glocale = guild_locale(interaction.guild.id) or DEFAULT_LOCALE
            p = Panel(
                t("server.ticket.channel_title", glocale, ticket_id=ticket_id),
                panel.get("welcome_message") or
                    t("server.ticket.default_welcome", glocale),
            )
            p.field(t("server.ticket.opened_by", glocale),
                    interaction.user.mention, inline=True)
            if support_role:
                p.field(t("server.ticket.support", glocale),
                        support_role.mention, inline=True)

            mention = interaction.user.mention
            if support_role:
                mention += f" · {support_role.mention}"
            # A V2 message cannot carry `content`: the ping becomes a text block
            # of the panel and the send call keeps its allowed_mentions so the
            # mentions really ping.
            p.text(mention, first=True)
            try:
                await ticket_channel.send(
                    view=TicketControlView(glocale, panel=p),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
            except Exception as e:
                print(f"[ticket] welcome err: {e!r}")

            await interaction.followup.send(
                ti(interaction, "server.ticket.created", channel=ticket_channel.mention),
                ephemeral=True,
            )


    class _TicketControlRow(discord.ui.ActionRow):
        """Claim / Close / Reopen / Delete. custom_ids kept identical."""

        @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success,
                            custom_id="ticket:claim")
        async def claim(self, interaction: discord.Interaction, _button: discord.ui.Button):
            v = self.view
            ticket = await v._resolve_ticket(interaction)
            if not ticket: return
            if not v._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.support_only"), ephemeral=True,
                ); return
            ticket_set_claimed(ticket["id"], interaction.user.id)
            await interaction.response.send_message(
                t("server.ticket.claimed", guild_locale(interaction.guild.id) or DEFAULT_LOCALE,
                  member=interaction.user.mention),
            )

        @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.secondary,
                            custom_id="ticket:close")
        async def close_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
            v = self.view
            ticket = await v._resolve_ticket(interaction)
            if not ticket: return
            # Opener or support
            if str(interaction.user.id) != ticket["opener_id"] and not v._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.close_denied"), ephemeral=True,
                ); return
            ticket_set_status(ticket["id"], "closed", closed_by=interaction.user.id)
            # Remove write perms for the opener
            try:
                opener = interaction.guild.get_member(int(ticket["opener_id"]))
                if opener:
                    ow = interaction.channel.overwrites_for(opener)
                    ow.send_messages = False
                    await interaction.channel.set_permissions(opener, overwrite=ow)
            except Exception:
                pass
            glocale = guild_locale(interaction.guild.id) or DEFAULT_LOCALE
            p = Panel(
                t("server.ticket.closed_title", glocale),
                t("server.ticket.closed_body", glocale, member=interaction.user.mention),
            )
            await interaction.response.send_message(view=p.view())

        @discord.ui.button(label="Reopen", emoji="🔓", style=discord.ButtonStyle.secondary,
                            custom_id="ticket:reopen")
        async def reopen_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
            v = self.view
            ticket = await v._resolve_ticket(interaction)
            if not ticket: return
            if not v._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.support_only"), ephemeral=True,
                ); return
            ticket_set_status(ticket["id"], "open")
            try:
                opener = interaction.guild.get_member(int(ticket["opener_id"]))
                if opener:
                    ow = interaction.channel.overwrites_for(opener)
                    ow.send_messages = True
                    await interaction.channel.set_permissions(opener, overwrite=ow)
            except Exception:
                pass
            await interaction.response.send_message(
                t("server.ticket.reopened", guild_locale(interaction.guild.id) or DEFAULT_LOCALE,
                  member=interaction.user.mention))

        @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger,
                            custom_id="ticket:delete")
        async def delete_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
            v = self.view
            ticket = await v._resolve_ticket(interaction)
            if not ticket: return
            if not v._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.support_only"), ephemeral=True,
                ); return
            ticket_set_status(ticket["id"], "deleted", closed_by=interaction.user.id)
            await interaction.response.send_message(
                t("server.ticket.deleting", guild_locale(interaction.guild.id) or DEFAULT_LOCALE),
            )
            await asyncio.sleep(5)
            try:
                await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
            except Exception as e:
                print(f"[ticket] delete err: {e!r}")


    class TicketControlView(discord.ui.LayoutView):
        """Persistent view inside each ticket: Claim / Close / Reopen / Delete.

        ``panel`` is the welcome panel; it is omitted when the view is only
        (re)registered for dispatch.
        """

        def __init__(self, locale: str = DEFAULT_LOCALE, panel: Panel | None = None):
            super().__init__(timeout=None)
            self.controls = _TicketControlRow()
            # Labels are localised per guild; the custom_ids stay untouched so
            # the buttons of already posted messages keep working.
            for child in self.controls.children:
                key = _CONTROL_LABEL_KEYS.get(getattr(child, "custom_id", None))
                if key:
                    child.label = t(key, locale)
            if panel is not None:
                self.add_item(panel.container())
            self.add_item(self.controls)

        async def _resolve_ticket(self, interaction: discord.Interaction) -> dict | None:
            ticket = ticket_get_by_channel(interaction.channel.id)
            if not ticket:
                await interaction.response.send_message(
                    ti(interaction, "server.ticket.not_a_ticket"), ephemeral=True,
                )
                return None
            return ticket

        @staticmethod
        def _is_support(member: discord.Member, ticket: dict) -> bool:
            # Bot owner, manage_channels perms, or the panel's support role
            if member.guild_permissions.manage_channels or member.guild_permissions.administrator:
                return True
            panel_id = ticket.get("panel_id")
            if panel_id:
                panel = ticket_panel_get(panel_id)
                if panel and panel.get("support_role_id"):
                    role = member.guild.get_role(int(panel["support_role_id"]))
                    if role and role in member.roles:
                        return True
            return False



    # ----- Slash command /ticket -----

    # ----- Interactive builder /ticket (single panel) -----

    class _TicketEmbedModal(discord.ui.Modal):
        heading      = discord.ui.TextInput(label="Title", max_length=256)
        description  = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, max_length=2000,
            required=False,
        )
        welcome = discord.ui.TextInput(
            label="Opening message", style=discord.TextStyle.paragraph, max_length=1500,
            required=False,
        )
        button_label = discord.ui.TextInput(label="Button text", max_length=80, required=False)
        button_emoji = discord.ui.TextInput(label="Button emoji", max_length=10,
                                            placeholder="🎫", required=False)

        def __init__(self, parent_view):
            loc = parent_view.locale
            super().__init__(title=t("server.ticket.modal_title", loc))
            self.parent_view = parent_view
            self.heading.label            = t("server.ticket.field_title", loc)
            self.heading.placeholder      = t("server.ticket.field_title_ph", loc)
            self.description.label        = t("server.ticket.field_description", loc)
            self.description.placeholder  = t("server.ticket.field_description_ph", loc)
            self.welcome.label            = t("server.ticket.field_welcome", loc)
            self.welcome.placeholder      = t("server.ticket.field_welcome_ph", loc)
            self.button_label.label       = t("server.ticket.field_button_label", loc)
            self.button_label.placeholder = t("server.ticket.default_button_label", loc)
            self.button_emoji.label       = t("server.ticket.field_button_emoji", loc)
            self.heading.default        = parent_view.panel_title or ""
            self.description.default    = parent_view.description or ""
            self.welcome.default        = parent_view.welcome or ""
            self.button_label.default   = parent_view.button_label or t("server.ticket.default_button_label", loc)
            self.button_emoji.default   = parent_view.button_emoji or "🎫"

        async def on_submit(self, interaction: discord.Interaction):
            loc = self.parent_view.locale
            self.parent_view.panel_title  = self.heading.value
            self.parent_view.description  = self.description.value
            self.parent_view.welcome      = self.welcome.value
            self.parent_view.button_label = self.button_label.value or t("server.ticket.default_button_label", loc)
            self.parent_view.button_emoji = self.button_emoji.value or "🎫"
            await self.parent_view.refresh(interaction)


    class _TicketBuilderView(discord.ui.LayoutView):
        """Interactive builder of a ticket panel."""

        def __init__(self, author_id: int, guild: discord.Guild, locale: str = DEFAULT_LOCALE):
            super().__init__(timeout=900)
            self.author_id   = author_id
            self.guild       = guild
            self.locale      = locale
            self.channel: discord.TextChannel | None = None
            self.support_role: discord.Role | None = None
            self.category: discord.CategoryChannel | None = None
            self.panel_title  = t("server.ticket.default_panel_title", locale)
            self.description  = t("server.ticket.default_panel_description", locale)
            self.welcome      = ""
            self.button_label = t("server.ticket.default_button_label", locale)
            self.button_emoji = "🎫"
            self._rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    t("server.ticket.not_your_menu", self.locale), ephemeral=True)
                return False
            return True

        def _summary_panel(self) -> Panel:
            loc = self.locale
            p = Panel(t("server.ticket.builder_title", loc))
            p.field(
                t("server.ticket.field_config", loc),
                t("server.ticket.config_value", loc,
                  channel=self.channel.mention if self.channel else t("server.ticket.not_set", loc),
                  role=self.support_role.mention if self.support_role else t("server.ticket.no_role", loc),
                  category=self.category.mention if self.category else t("server.ticket.no_category", loc)),
                inline=False,
            )
            p.field(
                t("server.ticket.field_embed", loc),
                t("server.ticket.embed_value", loc,
                  title=self.panel_title,
                  description=(self.description[:100] + "…") if self.description and len(self.description) > 100
                              else (self.description or "_—_"),
                  welcome=(self.welcome[:80] + "…") if self.welcome and len(self.welcome) > 80
                          else (self.welcome or t("server.ticket.default_value", loc))),
                inline=False,
            )
            p.field(
                t("server.ticket.field_button", loc),
                f"{self.button_emoji} **{self.button_label}**",
                inline=False,
            )
            p.footer(t("server.ticket.builder_footer", loc))
            return p

        def _rebuild(self):
            loc = self.locale
            self.clear_items()
            self.add_item(self._summary_panel().container())

            # Row 0: channel
            chan_sel = discord.ui.ChannelSelect(
                placeholder=t("server.ticket.select_channel", loc),
                channel_types=[discord.ChannelType.text],
                min_values=1, max_values=1,
            )
            async def _on_chan(interaction: discord.Interaction):
                ch = self.guild.get_channel(chan_sel.values[0].id)
                if isinstance(ch, discord.TextChannel):
                    self.channel = ch
                await self.refresh(interaction)
            chan_sel.callback = _on_chan
            self.add_item(row(chan_sel))

            # Row 1: category
            cat_sel = discord.ui.ChannelSelect(
                placeholder=t("server.ticket.select_category", loc),
                channel_types=[discord.ChannelType.category],
                min_values=0, max_values=1,
            )
            async def _on_cat(interaction: discord.Interaction):
                if cat_sel.values:
                    ch = self.guild.get_channel(cat_sel.values[0].id)
                    if isinstance(ch, discord.CategoryChannel):
                        self.category = ch
                else:
                    self.category = None
                await self.refresh(interaction)
            cat_sel.callback = _on_cat
            self.add_item(row(cat_sel))

            # Row 2: support role
            role_sel = discord.ui.RoleSelect(
                placeholder=t("server.ticket.select_role", loc),
                min_values=0, max_values=1,
            )
            async def _on_role(interaction: discord.Interaction):
                self.support_role = role_sel.values[0] if role_sel.values else None
                await self.refresh(interaction)
            role_sel.callback = _on_role
            self.add_item(row(role_sel))

            # Row 3: text modal + actions
            btn_embed = discord.ui.Button(
                label=t("server.ticket.btn_texts", loc), style=discord.ButtonStyle.secondary,
            )
            async def _on_embed(interaction: discord.Interaction):
                await interaction.response.send_modal(_TicketEmbedModal(self))
            btn_embed.callback = _on_embed

            btn_send = discord.ui.Button(
                label=t("server.ticket.btn_publish", loc), style=discord.ButtonStyle.success,
                disabled=not self.channel,
            )
            btn_send.callback = self._on_send

            btn_cancel = discord.ui.Button(
                label=t("server.ticket.btn_cancel", loc), style=discord.ButtonStyle.danger,
            )
            async def _on_cancel(interaction: discord.Interaction):
                self.clear_items()
                # A V2 message cannot fall back to plain content: swap the view.
                await interaction.response.edit_message(
                    view=Panel(description=t("server.ticket.cancelled", loc)).view(timeout=None))
                self.stop()
            btn_cancel.callback = _on_cancel

            self.add_item(row(btn_embed, btn_send, btn_cancel))

        async def refresh(self, interaction: discord.Interaction):
            self._rebuild()
            if interaction.response.is_done():
                await interaction.edit_original_response(view=self)
            else:
                await interaction.response.edit_message(view=self)

        async def _on_send(self, interaction: discord.Interaction):
            if not self.channel:
                return
            await interaction.response.defer(ephemeral=True)
            loc = self.locale

            # Pre-check of the bot's Discord permissions in the target channel,
            # every missing permission named explicitly (exact Discord name).
            perms = self.channel.permissions_for(self.guild.me)
            missing = []
            if not perms.view_channel:  missing.append(t("server.ticket.perm_view_channel", loc))
            if not perms.send_messages: missing.append(t("server.ticket.perm_send_messages", loc))
            if not perms.embed_links:   missing.append(t("server.ticket.perm_embed_links", loc))
            if missing:
                await interaction.edit_original_response(
                    view=Panel(description=t("server.ticket.missing_perms", loc,
                                             channel=self.channel.mention,
                                             perms=", ".join(missing))).view(timeout=None),
                )
                self.stop()
                return

            pid = ticket_panel_create(
                guild_id=self.guild.id,
                channel_id=self.channel.id,
                panel_title=self.panel_title, panel_description=self.description,
                button_label=self.button_label, button_emoji=self.button_emoji,
                support_role_id=self.support_role.id if self.support_role else None,
                category_id=self.category.id if self.category else None,
                welcome_message=self.welcome or None,
                created_by=self.author_id,
            )
            p = Panel(self.panel_title, self.description)
            if self.support_role:
                p.field(t("server.ticket.team", loc), self.support_role.mention, inline=True)
            view = TicketOpenView(label=self.button_label, emoji=self.button_emoji, panel=p)
            try:
                msg = await self.channel.send(view=view)
            except discord.Forbidden:
                ticket_panel_delete(pid, self.guild.id)
                await interaction.edit_original_response(
                    view=Panel(description=t("server.ticket.post_forbidden", loc,
                                             channel=self.channel.mention)).view(timeout=None),
                )
                self.stop()
                return
            ticket_panel_set_message(pid, msg.id)
            await interaction.edit_original_response(
                view=Panel(description=t("server.ticket.panel_posted", loc,
                                         panel_id=pid, channel=self.channel.mention,
                                         message_id=msg.id)).view(timeout=None),
            )
            self.stop()


    @bot.tree.command(name="ticket", description="Create a ticket panel (interactive builder)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def ticket_cmd(interaction: discord.Interaction):
        view = _TicketBuilderView(interaction.user.id, interaction.guild, locale_of(interaction))
        await interaction.response.send_message(view=view, ephemeral=True)

    def _register_ticket_views():
        """(Re)registers the persistent views. Called at import time AND in
        on_ready (reliable timing after the gateway connection, otherwise old
        messages are not picked up)."""
        try:
            bot.add_view(TicketOpenView())
            bot.add_view(TicketControlView())
        except Exception as e:
            print(f"[ticket] persistent views: {e!r}", flush=True)

    # Exposed for re-registration in on_ready
    bot._register_ticket_views = _register_ticket_views
    _register_ticket_views()
