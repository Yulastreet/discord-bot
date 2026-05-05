# duel_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from duel_sabres import get_sabre, get_tous_les_sabres, RARETES
from duel_combat import calculer_stats, calculer_degats, barre_hp

# ─── VUE : CHOIX D'ACTION EN COMBAT ───────────────────────────
class CombatView(discord.ui.View):
    def __init__(self, joueur_id, sabre_data, timeout=30):
        super().__init__(timeout=timeout)
        self.joueur_id = joueur_id
        self.sabre_data = sabre_data
        self.choix = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.joueur_id:
            await interaction.response.send_message("❌ Ce n'est pas ton tour !", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚔️ Attaquer", style=discord.ButtonStyle.danger)
    async def attaquer(self, interaction, button):
        self.choix = "attaque"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="🛡️ Défendre", style=discord.ButtonStyle.primary)
    async def defendre(self, interaction, button):
        self.choix = "defense"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="✨ Spéciale", style=discord.ButtonStyle.success)
    async def speciale(self, interaction, button):
        self.choix = "speciale"
        self.stop()
        await interaction.response.defer()


# ─── VUE : ACCEPTER/REFUSER UN DUEL ───────────────────────────
class DuelInviteView(discord.ui.View):
    def __init__(self, challenger_id, challenged_id):
        super().__init__(timeout=30)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.accepted = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("❌ Cette invitation ne te concerne pas !", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accepter(self, interaction, button):
        self.accepted = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def refuser(self, interaction, button):
        self.accepted = False
        self.stop()
        await interaction.response.defer()


# ─── FONCTION PRINCIPALE DE COMBAT ───────────────────────────
async def lancer_combat(ctx_or_interaction, joueur1, joueur2, sabre1_id, sabre2_id, db):
    """Lance un combat entre deux joueurs."""
    
    channel = ctx_or_interaction.channel if hasattr(ctx_or_interaction, 'channel') else ctx_or_interaction.channel

    profil1 = db.get_profil(joueur1.id)
    profil2 = db.get_profil(joueur2.id)
    sabre1 = get_sabre(sabre1_id)
    sabre2 = get_sabre(sabre2_id)

    if not profil1 or not profil2:
        await channel.send("❌ Un des joueurs n'a pas de profil !")
        return

    stats1 = calculer_stats(profil1, sabre1)
    stats2 = calculer_stats(profil2, sabre2)

    tour = 1
    MAX_TOURS = 20

    embed_debut = discord.Embed(
        title="⚔️ DUEL DE SABRES LASER",
        description=f"{joueur1.mention} ({sabre1['emoji']} {sabre1['nom']}) VS {joueur2.mention} ({sabre2['emoji']} {sabre2['nom']})",
        color=0xFF0000
    )
    embed_debut.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_debut.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    await channel.send(embed=embed_debut)
    await asyncio.sleep(2)

    joueurs = [(joueur1, stats1, sabre1), (joueur2, stats2, sabre2)]

    while stats1["hp"] > 0 and stats2["hp"] > 0 and tour <= MAX_TOURS:
        idx_att = (tour - 1) % 2
        idx_def = 1 - idx_att

        attaquant, att_stats, att_sabre = joueurs[idx_att]
        defenseur, def_stats, def_sabre = joueurs[idx_def]

        # Vérif paralysie
        if "paralyze" in att_stats["effets"]:
            embed_para = discord.Embed(
                title=f"⛓️ Tour {tour}",
                description=f"{attaquant.mention} est paralysé et ne peut qu'esquiver !",
                color=0x888888
            )
            await channel.send(embed=embed_para)
            del att_stats["effets"]["paralyze"]
            tour += 1
            continue

        # Demander l'action
        speciale_label = f"✨ {att_sabre['speciale']['nom']}" if att_stats["speciale_dispo"] else "✨ (utilisé)"
        view = CombatView(attaquant.id, att_sabre)
        
        # Modifier le bouton spéciale si déjà utilisé
        if not att_stats["speciale_dispo"]:
            view.speciale.disabled = True

        embed_tour = discord.Embed(
            title=f"⚔️ Tour {tour} — {attaquant.display_name}, c'est ton tour !",
            color=0xFFD700
        )
        embed_tour.add_field(
            name=f"❤️ {joueur1.display_name}",
            value=barre_hp(stats1["hp"], stats1["hp_max"]),
            inline=False
        )
        embed_tour.add_field(
            name=f"❤️ {joueur2.display_name}",
            value=barre_hp(stats2["hp"], stats2["hp_max"]),
            inline=False
        )
        embed_tour.set_footer(text="Tu as 30 secondes pour choisir !")

        msg = await channel.send(embed=embed_tour, view=view)
        await view.wait()

        if view.choix is None:
            view.choix = "attaque"  # Auto-attaque si timeout

        # Traiter le choix
        rapport = {"degats": 0, "messages": []}

        if view.choix == "attaque":
            rapport = calculer_degats(att_stats, def_stats)
            def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])

        elif view.choix == "defense":
            att_stats["effets"]["defense_active"] = True
            rapport["messages"].append(f"🛡️ {attaquant.display_name} se met en posture défensive ! (+50% def)")
            att_stats["defense"] = int(att_stats["defense"] * 1.5)

        elif view.choix == "speciale" and att_stats["speciale_dispo"]:
            rapport = calculer_degats(att_stats, def_stats, utilise_speciale=True, sabre_data=att_sabre)
            def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])

        # Embed résultat du tour
        desc = f"**{attaquant.display_name}** choisit : **{view.choix.upper()}**\n"
        if rapport["degats"] > 0:
            desc += f"💥 Dégâts infligés : **{rapport['degats']}**\n"
        for msg_effet in rapport["messages"]:
            desc += f"{msg_effet}\n"

        embed_result = discord.Embed(
            title=f"Tour {tour} — Résultat",
            description=desc,
            color=0xFF4444
        )
        embed_result.add_field(
            name=f"❤️ {joueur1.display_name}",
            value=barre_hp(stats1["hp"], stats1["hp_max"]),
            inline=False
        )
        embed_result.add_field(
            name=f"❤️ {joueur2.display_name}",
            value=barre_hp(stats2["hp"], stats2["hp_max"]),
            inline=False
        )
        await channel.send(embed=embed_result)
        await asyncio.sleep(1.5)
        tour += 1

    # ─── FIN DU COMBAT ───────────────────────────
    if stats1["hp"] <= 0 and stats2["hp"] <= 0:
        gagnant, perdant = None, None
        desc_fin = "⚖️ **ÉGALITÉ !** Les deux combattants tombent simultanément !"
    elif stats1["hp"] <= 0:
        gagnant, perdant = joueur2, joueur1
        desc_fin = f"🏆 **{joueur2.display_name} GAGNE LE DUEL !**"
    elif stats2["hp"] <= 0:
        gagnant, perdant = joueur1, joueur2
        desc_fin = f"🏆 **{joueur1.display_name} GAGNE LE DUEL !**"
    else:
        # Timeout - celui avec le plus de HP gagne
        if stats1["hp"] >= stats2["hp"]:
            gagnant, perdant = joueur1, joueur2
        else:
            gagnant, perdant = joueur2, joueur1
        desc_fin = f"⏱️ Temps écoulé ! **{gagnant.display_name} gagne aux points !**"

    # Récompenses
    xp_gain = 50
    coins_gain = 100
    if gagnant:
        db.add_xp(gagnant.id, xp_gain)
        db.add_tookcoins(gagnant.id, coins_gain)
        desc_fin += f"\n\n🎖️ +{xp_gain} XP | 🪙 +{coins_gain} TookCoins"

    embed_fin = discord.Embed(
        title="⚔️ FIN DU DUEL",
        description=desc_fin,
        color=0xFFD700
    )
    await channel.send(embed=embed_fin)


# ─── COMMANDES ───────────────────────────
def setup_duel_commands(bot, db):

    @bot.tree.command(name="duel", description="Défie un joueur en duel de sabres laser")
    @app_commands.describe(adversaire="Le joueur que tu veux défier")
    async def duel(interaction: discord.Interaction, adversaire: discord.Member):
        if adversaire.id == interaction.user.id:
            await interaction.response.send_message("❌ Tu ne peux pas te défier toi-même !", ephemeral=True)
            return
        if adversaire.bot:
            await interaction.response.send_message("❌ Tu ne peux pas défier un bot !", ephemeral=True)
            return

        profil1 = db.get_profil(interaction.user.id)
        profil2 = db.get_profil(adversaire.id)

        if not profil1:
            await interaction.response.send_message("❌ Tu n'as pas de profil ! Utilise `/start`", ephemeral=True)
            return
        if not profil2:
            await interaction.response.send_message(f"❌ {adversaire.display_name} n'a pas de profil !", ephemeral=True)
            return

        sabre1_id = profil1.get("sabre_equipe", "bleu")
        sabre2_id = profil2.get("sabre_equipe", "bleu")
        sabre1 = get_sabre(sabre1_id)
        sabre2 = get_sabre(sabre2_id)

        # Invitation
        view = DuelInviteView(interaction.user.id, adversaire.id)
        embed = discord.Embed(
            title="⚔️ DÉFI EN DUEL !",
            description=f"{interaction.user.mention} défie {adversaire.mention} en duel de sabres laser !\n\n"
                       f"{interaction.user.display_name} : {sabre1['emoji']} **{sabre1['nom']}** ({RARETES[sabre1['rarete']]['emoji']} {RARETES[sabre1['rarete']]['label']})\n"
                       f"{adversaire.display_name} : {sabre2['emoji']} **{sabre2['nom']}** ({RARETES[sabre2['rarete']]['emoji']} {RARETES[sabre2['rarete']]['label']})",
            color=0xFF0000
        )
        embed.set_footer(text=f"{adversaire.display_name} a 30 secondes pour accepter ou refuser !")
        
        await interaction.response.send_message(embed=embed, view=view)
        await view.wait()

        if view.accepted is None:
            await interaction.followup.send(f"⏱️ {adversaire.display_name} n'a pas répondu. Duel annulé.")
        elif not view.accepted:
            await interaction.followup.send(f"❌ {adversaire.display_name} a refusé le duel.")
        else:
            await interaction.followup.send(f"✅ {adversaire.display_name} accepte le duel ! Que le meilleur gagne !")
            await lancer_combat(interaction, interaction.user, adversaire, sabre1_id, sabre2_id, db)

    @bot.tree.command(name="boutique_sabres", description="Voir la boutique des sabres laser")
    async def boutique_sabres(interaction: discord.Interaction):
        sabres = get_tous_les_sabres()
        profil = db.get_profil(interaction.user.id)
        inventaire = profil.get("sabres", ["bleu"]) if profil else []

        embed = discord.Embed(title="🗡️ BOUTIQUE DES SABRES LASER", color=0x00BFFF)

        for rarete_id, rarete_info in RARETES.items():
            sabres_rarete = [s for s in sabres.values() if s["rarete"] == rarete_id]
            if not sabres_rarete:
                continue
            
            texte = ""
            for s in sabres_rarete:
                possede = "✅" if s["id"] in inventaire else ""
                prix_txt = "**GRATUIT**" if s["prix"] == 0 else f"🪙 {s['prix']}"
                texte += f"{s['emoji']} **{s['nom']}** {possede}\n{prix_txt} | ✨ {s['speciale']['nom']}\n\n"
            
            embed.add_field(
                name=f"{rarete_info['emoji']} {rarete_info['label']}",
                value=texte,
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="acheter_sabre", description="Acheter un sabre laser")
    @app_commands.describe(sabre_id="L'ID du sabre à acheter (ex: rouge, violet, arc_en_ciel...)")
    async def acheter_sabre(interaction: discord.Interaction, sabre_id: str):
        profil = db.get_profil(interaction.user.id)
        if not profil:
            await interaction.response.send_message("❌ Utilise `/start` d'abord !", ephemeral=True)
            return

        sabre = get_sabre(sabre_id)
        if not sabre:
            await interaction.response.send_message("❌ Sabre introuvable ! Vérifie l'ID avec `/boutique_sabres`", ephemeral=True)
            return

        inventaire = profil.get("sabres", ["bleu"])
        if sabre_id in inventaire:
            await interaction.response.send_message(f"❌ Tu possèdes déjà le {sabre['nom']} !", ephemeral=True)
            return

        coins = profil.get("tookcoins", 0)
        if coins < sabre["prix"]:
            await interaction.response.send_message(
                f"❌ Pas assez de TookCoins ! Il te faut 🪙 {sabre['prix']} (tu as 🪙 {coins})",
                ephemeral=True
            )
            return

        # Achat
        db.add_tookcoins(interaction.user.id, -sabre["prix"])
        inventaire.append(sabre_id)
        db.update_profil(interaction.user.id, {"sabres": inventaire})

        embed = discord.Embed(
            title="✅ ACHAT RÉUSSI !",
            description=f"Tu as acheté le {sabre['emoji']} **{sabre['nom']}** !\n\n"
                       f"💫 Capacité spéciale : **{sabre['speciale']['nom']}**\n"
                       f"_{sabre['speciale']['description']}_",
            color=0x00FF00
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="equiper_sabre", description="Équiper un sabre laser")
    @app_commands.describe(sabre_id="L'ID du sabre à équiper")
    async def equiper_sabre(interaction: discord.Interaction, sabre_id: str):
        profil = db.get_profil(interaction.user.id)
        if not profil:
            await interaction.response.send_message("❌ Utilise `/start` d'abord !", ephemeral=True)
            return

        inventaire = profil.get("sabres", ["bleu"])
        if sabre_id not in inventaire:
            await interaction.response.send_message("❌ Tu ne possèdes pas ce sabre !", ephemeral=True)
            return

        sabre = get_sabre(sabre_id)
        db.update_profil(interaction.user.id, {"sabre_equipe": sabre_id})

        await interaction.response.send_message(
            f"✅ {sabre['emoji']} **{sabre['nom']}** équipé ! Bonne chance au prochain duel !"
        )

    @bot.tree.command(name="mon_sabre", description="Voir ton sabre équipé")
    async def mon_sabre(interaction: discord.Interaction):
        profil = db.get_profil(interaction.user.id)
        if not profil:
            await interaction.response.send_message("❌ Utilise `/start` d'abord !", ephemeral=True)
            return

        sabre_id = profil.get("sabre_equipe", "bleu")
        sabre = get_sabre(sabre_id)
        rarete = RARETES[sabre["rarete"]]
        inventaire = profil.get("sabres", ["bleu"])

        embed = discord.Embed(
            title=f"{sabre['emoji']} {sabre['nom']}",
            description=sabre["description"],
            color=0x00BFFF
        )
        embed.add_field(name="Rareté", value=f"{rarete['emoji']} {rarete['label']}", inline=True)
        embed.add_field(name="Capacité Spéciale", value=f"✨ **{sabre['speciale']['nom']}**\n_{sabre['speciale']['description']}_", inline=False)
        embed.add_field(name="Sabres possédés", value=f"{len(inventaire)} sabre(s)", inline=True)
        embed.set_footer(text=f"Utilise /equiper_sabre pour changer de sabre")

        await interaction.response.send_message(embed=embed)