# duel_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from database import get_historique
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


# ─── VUE : HISTORIQUE DU COMBAT ───────────────────────────
class HistoriqueView(discord.ui.View):
    def __init__(self, historique: list):
        super().__init__(timeout=120)
        self.historique = historique

    @discord.ui.button(label="📜 Historique du combat", style=discord.ButtonStyle.secondary)
    async def voir_historique(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.historique:
            await interaction.response.send_message("Aucune action enregistrée.", ephemeral=True)
            return

        pages = []
        chunk = ""
        for ligne in self.historique:
            if len(chunk) + len(ligne) + 1 > 4000:
                pages.append(chunk)
                chunk = ligne + "\n"
            else:
                chunk += ligne + "\n"
        if chunk:
            pages.append(chunk)

        embed = discord.Embed(title="📜 Historique du combat", description=pages[0], color=0x888888)
        if len(pages) > 1:
            embed.set_footer(text=f"Page 1/{len(pages)} — trop long pour tout afficher d'un coup")
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
    channel = ctx_or_interaction.channel

    profil1 = db.ensure_profil(joueur1.id, joueur1.name)
    profil2 = db.ensure_profil(joueur2.id, joueur2.name)
    sabre1 = get_sabre(sabre1_id)
    sabre2 = get_sabre(sabre2_id)

    stats1 = calculer_stats(profil1, sabre1)
    stats2 = calculer_stats(profil2, sabre2)

    tour = 1
    MAX_TOURS = 20
    historique = []

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
                description=f"{attaquant.mention} est paralysé et passe son tour !",
                color=0x888888
            )
            await channel.send(embed=embed_para)
            historique.append(f"**Tour {tour}** — {attaquant.display_name} est paralysé !")
            del att_stats["effets"]["paralyze"]
            tour += 1
            continue

        view = CombatView(attaquant.id, att_sabre)
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

        await channel.send(embed=embed_tour, view=view)
        await view.wait()

        if view.choix is None:
            view.choix = "attaque"

        rapport = {"degats": 0, "soin": 0, "messages": []}

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

        # Enregistrement dans l'historique
        entree = f"**Tour {tour}** — {attaquant.display_name} › {view.choix.upper()}"
        if rapport["degats"] > 0:
            entree += f" | 💥 {rapport['degats']} dégâts"
        for msg_effet in rapport["messages"]:
            entree += f" | {msg_effet}"
        entree += f"\n↳ HP : {joueur1.display_name} {stats1['hp']}/{stats1['hp_max']} · {joueur2.display_name} {stats2['hp']}/{stats2['hp_max']}"
        historique.append(entree)

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
        if stats1["hp"] >= stats2["hp"]:
            gagnant, perdant = joueur1, joueur2
        else:
            gagnant, perdant = joueur2, joueur1
        desc_fin = f"⏱️ Temps écoulé ! **{gagnant.display_name} gagne aux points !**"

    xp_gain = 50
    coins_gain = 100
    if gagnant:
        db.add_xp(gagnant.id, xp_gain)
        db.add_tookcoins(gagnant.id, coins_gain)
        db.add_victoire(gagnant.id)
        db.add_defaite(perdant.id)
        db.sauvegarder(joueur1.id, joueur2.id, gagnant.id, coins_gain, 0)
        desc_fin += f"\n\n🎖️ +{xp_gain} XP | 🪙 +{coins_gain} TookCoins"

    embed_fin = discord.Embed(
        title="⚔️ FIN DU DUEL",
        description=desc_fin,
        color=0xFFD700
    )
    await channel.send(embed=embed_fin, view=HistoriqueView(historique))


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

        profil1 = db.ensure_profil(interaction.user.id, interaction.user.name)
        profil2 = db.ensure_profil(adversaire.id, adversaire.name)

        sabre1_id = profil1.get("sabre_equipe", "bleu")
        sabre2_id = profil2.get("sabre_equipe", "bleu")
        sabre1 = get_sabre(sabre1_id)
        sabre2 = get_sabre(sabre2_id)

        view = DuelInviteView(interaction.user.id, adversaire.id)
        embed = discord.Embed(
            title="⚔️ DÉFI EN DUEL !",
            description=(
                f"{interaction.user.mention} défie {adversaire.mention} en duel de sabres laser !\n\n"
                f"{interaction.user.display_name} : {sabre1['emoji']} **{sabre1['nom']}** "
                f"({RARETES[sabre1['rarete']]['emoji']} {RARETES[sabre1['rarete']]['label']})\n"
                f"{adversaire.display_name} : {sabre2['emoji']} **{sabre2['nom']}** "
                f"({RARETES[sabre2['rarete']]['emoji']} {RARETES[sabre2['rarete']]['label']})"
            ),
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

    @bot.tree.command(name="profil", description="Voir ton profil de duel")
    @app_commands.describe(membre="Le membre dont tu veux voir le profil")
    async def profil(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        profil_data = db.ensure_profil(membre.id, membre.name)
        sabre_id = profil_data.get("sabre_equipe", "bleu")
        sabre = get_sabre(sabre_id)
        rarete = RARETES[sabre["rarete"]]
        total = profil_data["victoires"] + profil_data["defaites"]
        ratio = f"{profil_data['victoires']}/{total}" if total > 0 else "0/0"

        embed = discord.Embed(title=f"⚔️ Profil de {membre.display_name}", color=discord.Color.red())
        embed.set_thumbnail(url=membre.display_avatar.url)
        embed.add_field(name="💰 TookCoins", value=f"**{profil_data['tookcoins']}** 🪙", inline=True)
        embed.add_field(name="🏆 Victoires", value=f"**{profil_data['victoires']}**", inline=True)
        embed.add_field(name="💀 Défaites", value=f"**{profil_data['defaites']}**", inline=True)
        embed.add_field(name="📊 Ratio", value=ratio, inline=True)
        embed.add_field(name="⚔️ Sabre équipé", value=f"{sabre['emoji']} {sabre['nom']} ({rarete['emoji']} {rarete['label']})", inline=True)
        embed.add_field(name="🗂️ Collection", value=f"**{len(profil_data['sabres'])}** sabre(s)", inline=True)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="collection", description="Voir ta collection de sabres")
    @app_commands.describe(membre="Le membre dont tu veux voir la collection")
    async def collection(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        profil_data = db.ensure_profil(membre.id, membre.name)
        sabres = profil_data.get("sabres", ["bleu"])
        sabre_equipe = profil_data.get("sabre_equipe", "bleu")

        embed = discord.Embed(title=f"🗡️ Collection de {membre.display_name}", color=discord.Color.blue())
        description = ""
        for sabre_id in sabres:
            sabre = get_sabre(sabre_id)
            if not sabre:
                continue
            rarete = RARETES[sabre["rarete"]]
            equipe = " ← **équipé**" if sabre_id == sabre_equipe else ""
            description += f"{sabre['emoji']} **{sabre['nom']}** {rarete['emoji']} {rarete['label']}{equipe}\n"
        embed.description = description or "Aucun sabre."
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="historique", description="Voir tes derniers duels")
    @app_commands.describe(membre="Le membre dont tu veux voir l'historique")
    async def historique_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        duels = get_historique(membre.id, limit=5)

        if not duels:
            await interaction.response.send_message(
                f"❌ Aucun duel dans l'historique pour {membre.display_name} !", ephemeral=True
            )
            return

        embed = discord.Embed(title=f"📜 Historique de {membre.display_name}", color=discord.Color.blurple())
        description = ""
        for duel in duels:
            gagne = str(duel["gagnant_id"]) == str(membre.id)
            result = "✅ Victoire" if gagne else "❌ Défaite"
            adversaire_id = duel["user_id_2"] if str(duel["user_id_1"]) == str(membre.id) else duel["user_id_1"]
            try:
                adversaire = await interaction.client.fetch_user(int(adversaire_id))
                adversaire_nom = adversaire.name
            except Exception:
                adversaire_nom = f"Inconnu ({adversaire_id})"
            coins = duel["tookcoins_gagnant"] if gagne else duel["tookcoins_perdant"]
            description += f"{result} vs **{adversaire_nom}** — {coins} 🪙 | {duel['date'][:10]}\n"
        embed.description = description
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="boutique_sabres", description="Voir la boutique des sabres laser")
    async def boutique_sabres(interaction: discord.Interaction):
        sabres = get_tous_les_sabres()
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        inventaire = profil_data.get("sabres", ["bleu"])

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
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)

        sabre = get_sabre(sabre_id)
        if not sabre:
            await interaction.response.send_message("❌ Sabre introuvable ! Vérifie l'ID avec `/boutique_sabres`", ephemeral=True)
            return

        inventaire = profil_data.get("sabres", ["bleu"])
        if sabre_id in inventaire:
            await interaction.response.send_message(f"❌ Tu possèdes déjà le {sabre['nom']} !", ephemeral=True)
            return

        coins = profil_data.get("tookcoins", 0)
        if coins < sabre["prix"]:
            await interaction.response.send_message(
                f"❌ Pas assez de TookCoins ! Il te faut 🪙 {sabre['prix']} (tu as 🪙 {coins})",
                ephemeral=True
            )
            return

        db.add_tookcoins(interaction.user.id, -sabre["prix"])
        db.update_profil(interaction.user.id, {"sabres": inventaire + [sabre_id]})

        embed = discord.Embed(
            title="✅ ACHAT RÉUSSI !",
            description=(
                f"Tu as acheté le {sabre['emoji']} **{sabre['nom']}** !\n\n"
                f"💫 Capacité spéciale : **{sabre['speciale']['nom']}**\n"
                f"_{sabre['speciale']['description']}_"
            ),
            color=0x00FF00
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="equiper_sabre", description="Équiper un sabre laser")
    @app_commands.describe(sabre_id="L'ID du sabre à équiper")
    async def equiper_sabre(interaction: discord.Interaction, sabre_id: str):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        inventaire = profil_data.get("sabres", ["bleu"])

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
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        sabre_id = profil_data.get("sabre_equipe", "bleu")
        sabre = get_sabre(sabre_id)
        rarete = RARETES[sabre["rarete"]]
        inventaire = profil_data.get("sabres", ["bleu"])

        embed = discord.Embed(
            title=f"{sabre['emoji']} {sabre['nom']}",
            description=sabre["description"],
            color=0x00BFFF
        )
        embed.add_field(name="Rareté", value=f"{rarete['emoji']} {rarete['label']}", inline=True)
        embed.add_field(
            name="Capacité Spéciale",
            value=f"✨ **{sabre['speciale']['nom']}**\n_{sabre['speciale']['description']}_",
            inline=False
        )
        embed.add_field(name="Sabres possédés", value=f"{len(inventaire)} sabre(s)", inline=True)
        embed.set_footer(text="Utilise /equiper_sabre pour changer de sabre")

        await interaction.response.send_message(embed=embed)
