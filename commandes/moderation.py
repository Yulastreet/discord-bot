import discord
from discord import app_commands


def setup_moderation_commands(bot):
    @bot.tree.command(name="kick", description="Expulser un membre")
    @app_commands.describe(membre="Le membre a expulser", raison="La raison")
    @app_commands.default_permissions(kick_members=True)
    async def kick(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison fournie"):
        await membre.kick(reason=raison)
        await interaction.response.send_message(f"**{membre.name}** a ete expulse. Raison : {raison}")

    @bot.tree.command(name="ban", description="Bannir un membre")
    @app_commands.describe(membre="Le membre a bannir", raison="La raison")
    @app_commands.default_permissions(ban_members=True)
    async def ban(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison fournie"):
        await membre.ban(reason=raison)
        await interaction.response.send_message(f"**{membre.name}** a ete banni. Raison : {raison}")

    @bot.tree.command(name="clear", description="Supprimer des messages")
    @app_commands.describe(nombre="Nombre de messages a supprimer")
    @app_commands.default_permissions(manage_messages=True)
    async def clear(interaction: discord.Interaction, nombre: int):
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.purge(limit=nombre)
        await interaction.followup.send(f"**{nombre}** messages supprimes.", ephemeral=True)

    class PollBuilderModal(discord.ui.Modal, title="Creer un sondage"):
        question = discord.ui.TextInput(
            label="Question",
            placeholder="Ex : Quelle pizza ce soir ?",
            max_length=300,
            required=True,
        )
        option_1 = discord.ui.TextInput(
            label="Option 1",
            placeholder="Ex : 4 fromages",
            max_length=55,
            required=True,
        )
        option_2 = discord.ui.TextInput(
            label="Option 2",
            placeholder="Ex : Reine",
            max_length=55,
            required=True,
        )
        option_3 = discord.ui.TextInput(
            label="Option 3 (optionnel)",
            max_length=55,
            required=False,
        )
        option_4 = discord.ui.TextInput(
            label="Option 4 (optionnel)",
            max_length=55,
            required=False,
        )

        async def on_submit(self, interaction: discord.Interaction):
            import datetime as _dt
            opts = [
                str(self.option_1.value).strip(),
                str(self.option_2.value).strip(),
                str(self.option_3.value).strip(),
                str(self.option_4.value).strip(),
            ]
            opts = [o for o in opts if o]
            if len(opts) < 2:
                await interaction.response.send_message(
                    "Il faut au moins 2 options remplies.", ephemeral=True,
                )
                return
            try:
                poll = discord.Poll(
                    question=str(self.question.value).strip()[:300],
                    duration=_dt.timedelta(hours=24),
                )
                for o in opts[:10]:
                    poll.add_answer(text=o[:55])
                await interaction.response.send_message(poll=poll)
            except Exception as e:
                await interaction.response.send_message(
                    f"Erreur creation du sondage : {type(e).__name__}: {e}",
                    ephemeral=True,
                )

    @bot.tree.command(name="poll", description="Creer un sondage (ouvre un builder)")
    async def poll(interaction: discord.Interaction):
        # Ouvre le modal de creation. Le sondage utilise le composant
        # natif Discord (vote live + UI integree). Pour 5+ options ou
        # plus de controle, utiliser le builder du dashboard.
        await interaction.response.send_modal(PollBuilderModal())
