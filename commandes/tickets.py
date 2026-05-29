import asyncio
import discord
from discord import app_commands

def setup_ticket_commands(bot, deps):
    globals().update(deps)
    # ===== TICKETS =====

    _BUTTON_STYLES = {
        "primary":   discord.ButtonStyle.primary,
        "success":   discord.ButtonStyle.success,
        "danger":    discord.ButtonStyle.danger,
        "secondary": discord.ButtonStyle.secondary,
    }


    class TicketOpenView(discord.ui.View):
        """Persistent view du panneau d'ouverture de ticket. Custom_id stable."""

        def __init__(self, label: str = "Ouvrir un ticket", emoji: str = "🎫",
                     style: discord.ButtonStyle = discord.ButtonStyle.primary):
            super().__init__(timeout=None)
            btn = discord.ui.Button(
                label=label, emoji=emoji, style=style,
                custom_id="ticket:open",
            )
            btn.callback = self._on_open
            self.add_item(btn)

        async def _on_open(self, interaction: discord.Interaction):
            if not interaction.guild or not interaction.message:
                await interaction.response.send_message("❌ Erreur de contexte.", ephemeral=True)
                return
            panel = ticket_panel_get_by_message(interaction.guild.id, interaction.message.id)
            if not panel or not panel.get("enabled"):
                await interaction.response.send_message(
                    "❌ Ce panneau de tickets n'est plus actif.", ephemeral=True,
                )
                return

            # Anti-doublon : un ticket ouvert par user / panel
            existing = ticket_get_open_by_user(
                interaction.guild.id, interaction.user.id, panel_id=panel["id"],
            )
            if existing:
                ch = interaction.guild.get_channel(int(existing["channel_id"]))
                if ch:
                    await interaction.response.send_message(
                        f"📌 Tu as déjà un ticket ouvert : {ch.mention}",
                        ephemeral=True,
                    )
                    return
                # Le canal a disparu mais le ticket est marque open : on le ferme
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

            # Nom unique : ticket-username-N
            base_name = f"ticket-{interaction.user.name}".lower()[:90]
            # Discord channel names sanitization
            import re as _re
            base_name = _re.sub(r"[^a-z0-9-]", "-", base_name).strip("-") or "ticket"

            try:
                ticket_channel = await interaction.guild.create_text_channel(
                    name=base_name,
                    category=category,
                    overwrites=overwrites,
                    topic=f"Ticket de {interaction.user} (id {interaction.user.id})",
                    reason=f"Ticket ouvert par {interaction.user}",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Permissions insuffisantes (le bot doit pouvoir gérer les salons).",
                    ephemeral=True,
                )
                return
            except Exception as e:
                await interaction.followup.send(f"❌ Erreur : {e!r}", ephemeral=True)
                return

            ticket_id = ticket_create(
                interaction.guild.id, panel["id"], interaction.user.id, ticket_channel.id,
            )

            # Welcome message + ControlView
            embed = discord.Embed(
                title=f"🎫 Ticket #{ticket_id}",
                description=panel.get("welcome_message") or
                    "Bienvenue ! Décris ton problème ou ta demande, un membre du support va te répondre.",
                color=0xC8F050,
            )
            embed.add_field(name="Ouvert par", value=interaction.user.mention, inline=True)
            if support_role:
                embed.add_field(name="Support", value=support_role.mention, inline=True)

            mention = interaction.user.mention
            if support_role:
                mention += f" · {support_role.mention}"
            try:
                await ticket_channel.send(
                    content=mention, embed=embed, view=TicketControlView(),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
            except Exception as e:
                print(f"[ticket] welcome err: {e!r}")

            await interaction.followup.send(
                f"✅ Ticket créé : {ticket_channel.mention}", ephemeral=True,
            )


    class TicketControlView(discord.ui.View):
        """Persistent view dans chaque ticket : Claim / Close / Reopen / Delete."""

        def __init__(self):
            super().__init__(timeout=None)

        async def _resolve_ticket(self, interaction: discord.Interaction) -> dict | None:
            ticket = ticket_get_by_channel(interaction.channel.id)
            if not ticket:
                await interaction.response.send_message(
                    "❌ Ce salon n'est pas un ticket valide.", ephemeral=True,
                )
                return None
            return ticket

        @staticmethod
        def _is_support(member: discord.Member, ticket: dict) -> bool:
            # Owner du bot, perms manage_channels, ou role support du panel
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

        @discord.ui.button(label="Réclamer", emoji="🙋", style=discord.ButtonStyle.success,
                            custom_id="ticket:claim")
        async def claim(self, interaction: discord.Interaction, _button: discord.ui.Button):
            ticket = await self._resolve_ticket(interaction)
            if not ticket: return
            if not self._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    "❌ Réservé au support.", ephemeral=True,
                ); return
            ticket_set_claimed(ticket["id"], interaction.user.id)
            await interaction.response.send_message(
                f"🙋 **{interaction.user.mention}** prend en charge ce ticket.",
            )

        @discord.ui.button(label="Fermer", emoji="🔒", style=discord.ButtonStyle.secondary,
                            custom_id="ticket:close")
        async def close_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
            ticket = await self._resolve_ticket(interaction)
            if not ticket: return
            # Opener ou support
            if str(interaction.user.id) != ticket["opener_id"] and not self._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    "❌ Seul l'auteur du ticket ou le support peut fermer.", ephemeral=True,
                ); return
            ticket_set_status(ticket["id"], "closed", closed_by=interaction.user.id)
            # Retire perms d'ecriture pour l'opener
            try:
                opener = interaction.guild.get_member(int(ticket["opener_id"]))
                if opener:
                    ow = interaction.channel.overwrites_for(opener)
                    ow.send_messages = False
                    await interaction.channel.set_permissions(opener, overwrite=ow)
            except Exception:
                pass
            embed = discord.Embed(
                title="🔒 Ticket fermé",
                description=f"Fermé par {interaction.user.mention}.",
                color=0x808080,
            )
            await interaction.response.send_message(embed=embed)

        @discord.ui.button(label="Rouvrir", emoji="🔓", style=discord.ButtonStyle.secondary,
                            custom_id="ticket:reopen")
        async def reopen_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
            ticket = await self._resolve_ticket(interaction)
            if not ticket: return
            if not self._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    "❌ Réservé au support.", ephemeral=True,
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
            await interaction.response.send_message(f"🔓 Ticket rouvert par {interaction.user.mention}.")

        @discord.ui.button(label="Supprimer", emoji="🗑️", style=discord.ButtonStyle.danger,
                            custom_id="ticket:delete")
        async def delete_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
            ticket = await self._resolve_ticket(interaction)
            if not ticket: return
            if not self._is_support(interaction.user, ticket):
                await interaction.response.send_message(
                    "❌ Réservé au support.", ephemeral=True,
                ); return
            ticket_set_status(ticket["id"], "deleted", closed_by=interaction.user.id)
            await interaction.response.send_message(
                "🗑️ Salon supprimé dans 5s…",
            )
            await asyncio.sleep(5)
            try:
                await interaction.channel.delete(reason=f"Ticket supprimé par {interaction.user}")
            except Exception as e:
                print(f"[ticket] delete err: {e!r}")


    # ----- Slash command /ticket -----

    # ----- Builder interactif /ticket (panneau unique) -----

    class _TicketEmbedModal(discord.ui.Modal, title="Configurer le panneau"):
        titre       = discord.ui.TextInput(label="Titre", max_length=256,
                                            placeholder="🎫 Support")
        description = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, max_length=2000,
            placeholder="Texte affiché sur le panneau (markdown OK)",
            required=False,
        )
        welcome = discord.ui.TextInput(
            label="Message d'ouverture (envoyé dans le ticket)",
            style=discord.TextStyle.paragraph, max_length=1500,
            placeholder="Bienvenue ! Décris ton problème...",
            required=False,
        )
        button_label = discord.ui.TextInput(
            label="Texte du bouton", max_length=80,
            placeholder="Ouvrir un ticket", required=False,
        )
        button_emoji = discord.ui.TextInput(
            label="Emoji du bouton", max_length=10,
            placeholder="🎫", required=False,
        )

        def __init__(self, parent_view):
            super().__init__()
            self.parent_view = parent_view
            self.titre.default        = parent_view.titre or ""
            self.description.default  = parent_view.description or ""
            self.welcome.default      = parent_view.welcome or ""
            self.button_label.default = parent_view.button_label or "Ouvrir un ticket"
            self.button_emoji.default = parent_view.button_emoji or "🎫"

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.titre        = self.titre.value
            self.parent_view.description  = self.description.value
            self.parent_view.welcome      = self.welcome.value
            self.parent_view.button_label = self.button_label.value or "Ouvrir un ticket"
            self.parent_view.button_emoji = self.button_emoji.value or "🎫"
            await self.parent_view.refresh(interaction)


    class _TicketBuilderView(discord.ui.View):
        """Builder interactif d'un panneau de tickets."""

        def __init__(self, author_id: int, guild: discord.Guild):
            super().__init__(timeout=900)
            self.author_id   = author_id
            self.guild       = guild
            self.salon: discord.TextChannel | None = None
            self.support_role: discord.Role | None = None
            self.category: discord.CategoryChannel | None = None
            self.titre        = "🎫 Support"
            self.description  = "Clique sur le bouton ci-dessous pour ouvrir un ticket avec notre équipe."
            self.welcome      = ""
            self.button_label = "Ouvrir un ticket"
            self.button_emoji = "🎫"
            self._rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("❌ Pas ton menu.", ephemeral=True)
                return False
            return True

        def _summary_embed(self) -> discord.Embed:
            embed = discord.Embed(title="🎫 Builder Panneau Ticket", color=0xC8F050)
            embed.add_field(
                name="📍 Configuration",
                value=(
                    f"**Salon panneau :** {self.salon.mention if self.salon else '_non défini_'}\n"
                    f"**Rôle support :** {self.support_role.mention if self.support_role else '_aucun_'}\n"
                    f"**Catégorie tickets :** {self.category.mention if self.category else '_aucune (créés à la racine)_'}"
                ),
                inline=False,
            )
            embed.add_field(
                name="📝 Embed",
                value=(
                    f"**Titre :** {self.titre}\n"
                    f"**Description :** {(self.description[:100] + '…') if self.description and len(self.description) > 100 else (self.description or '_—_')}\n"
                    f"**Message ouverture :** {(self.welcome[:80] + '…') if self.welcome and len(self.welcome) > 80 else (self.welcome or '_par défaut_')}"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔘 Bouton",
                value=f"{self.button_emoji} **{self.button_label}**",
                inline=False,
            )
            embed.set_footer(text="Le builder expire dans 15 minutes.")
            return embed

        def _rebuild(self):
            self.clear_items()

            # Row 0 : Salon
            chan_sel = discord.ui.ChannelSelect(
                placeholder="📍 Salon où poster le panneau…",
                channel_types=[discord.ChannelType.text],
                min_values=1, max_values=1, row=0,
            )
            async def _on_chan(interaction: discord.Interaction):
                ch = self.guild.get_channel(chan_sel.values[0].id)
                if isinstance(ch, discord.TextChannel):
                    self.salon = ch
                await self.refresh(interaction)
            chan_sel.callback = _on_chan
            self.add_item(chan_sel)

            # Row 1 : Catégorie
            cat_sel = discord.ui.ChannelSelect(
                placeholder="📁 Catégorie où créer les tickets (optionnel)…",
                channel_types=[discord.ChannelType.category],
                min_values=0, max_values=1, row=1,
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
            self.add_item(cat_sel)

            # Row 2 : Rôle support
            role_sel = discord.ui.RoleSelect(
                placeholder="👥 Rôle support (optionnel)…",
                min_values=0, max_values=1, row=2,
            )
            async def _on_role(interaction: discord.Interaction):
                self.support_role = role_sel.values[0] if role_sel.values else None
                await self.refresh(interaction)
            role_sel.callback = _on_role
            self.add_item(role_sel)

            # Row 3 : Modal embed + actions
            btn_embed = discord.ui.Button(
                label="📝 Texte / bouton", style=discord.ButtonStyle.secondary, row=3,
            )
            async def _on_embed(interaction: discord.Interaction):
                await interaction.response.send_modal(_TicketEmbedModal(self))
            btn_embed.callback = _on_embed
            self.add_item(btn_embed)

            btn_send = discord.ui.Button(
                label="✅ Publier le panneau", style=discord.ButtonStyle.success, row=3,
                disabled=not self.salon,
            )
            btn_send.callback = self._on_send
            self.add_item(btn_send)

            btn_cancel = discord.ui.Button(
                label="❌ Annuler", style=discord.ButtonStyle.danger, row=3,
            )
            async def _on_cancel(interaction: discord.Interaction):
                self.clear_items()
                await interaction.response.edit_message(content="❌ Builder annulé.",
                                                         embed=None, view=None)
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
            if not self.salon:
                return
            await interaction.response.defer(ephemeral=True)

            pid = ticket_panel_create(
                guild_id=self.guild.id,
                channel_id=self.salon.id,
                panel_title=self.titre, panel_description=self.description,
                button_label=self.button_label, button_emoji=self.button_emoji,
                support_role_id=self.support_role.id if self.support_role else None,
                category_id=self.category.id if self.category else None,
                welcome_message=self.welcome or None,
                created_by=self.author_id,
            )
            embed = discord.Embed(title=self.titre, description=self.description, color=0xC8F050)
            if self.support_role:
                embed.add_field(name="Équipe", value=self.support_role.mention, inline=True)
            view = TicketOpenView(label=self.button_label, emoji=self.button_emoji)
            try:
                msg = await self.salon.send(embed=embed, view=view)
            except discord.Forbidden:
                ticket_panel_delete(pid, self.guild.id)
                await interaction.edit_original_response(
                    content=f"❌ Pas de permission pour poster dans {self.salon.mention}.",
                    embed=None, view=None,
                )
                self.stop()
                return
            ticket_panel_set_message(pid, msg.id)
            await interaction.edit_original_response(
                content=(
                    f"✅ Panneau ticket `#{pid}` posté dans {self.salon.mention}.\n"
                    f"`message_id = {msg.id}`"
                ),
                embed=None, view=None,
            )
            self.stop()


    @bot.tree.command(name="ticket", description="Créer un panneau de tickets (builder interactif)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def ticket_cmd(interaction: discord.Interaction):
        view = _TicketBuilderView(interaction.user.id, interaction.guild)
        await interaction.response.send_message(
            embed=view._summary_embed(), view=view, ephemeral=True,
        )

    try:
        bot.add_view(TicketOpenView())
        bot.add_view(TicketControlView())
    except Exception as e:
        print(f"[ticket] persistent views: {e!r}", flush=True)
