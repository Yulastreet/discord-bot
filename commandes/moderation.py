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
        options = discord.ui.TextInput(
            label="Options (une par ligne, 2 a 10)",
            style=discord.TextStyle.paragraph,
            placeholder="4 fromages\nReine\nPepperoni",
            max_length=600,
            required=True,
        )
        duration = discord.ui.TextInput(
            label="Duree (en heures, 1 a 168)",
            placeholder="24",
            default="24",
            max_length=3,
            required=False,
        )

        async def on_submit(self, interaction: discord.Interaction):
            import datetime as _dt
            opts = [ln.strip() for ln in str(self.options.value).splitlines() if ln.strip()]
            if len(opts) < 2:
                await interaction.response.send_message(
                    "Il faut au moins 2 options (une par ligne).", ephemeral=True,
                )
                return
            if len(opts) > 10:
                opts = opts[:10]
            # Parse duree, clamp [1, 168]h (= 7 jours max, limite Discord)
            try:
                dh = int(str(self.duration.value).strip() or "24")
            except ValueError:
                dh = 24
            dh = max(1, min(168, dh))
            try:
                poll = discord.Poll(
                    question=str(self.question.value).strip()[:300],
                    duration=_dt.timedelta(hours=dh),
                )
                for o in opts:
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
