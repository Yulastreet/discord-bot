# duel_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from database import get_historique
from duel_sabres import get_sabre, get_tous_les_sabres, RARETES
from duel_combat import calculer_stats, calculer_degats, barre_hp


# ─── ÉTAT PARTAGÉ DU TOUR ───────────────────────────
class CombatState:
    def __init__(self):
        self.choix = {}
        self._event = asyncio.Event()

    def enregistrer(self, uid, action):
        self.choix[uid] = action
        if len(self.choix) == 2:
            self._event.set()

    async def attendre(self, timeout=30):
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def reset(self):
        self.choix = {}
        self._event = asyncio.Event()


# ─── PANEL PERSO (ephemeral, un joueur) ───────────────────────────
class PersonalCombatView(discord.ui.View):
    ACTIONS = [
        ("⚔️ Attaque",  "attaque",  discord.ButtonStyle.danger),
        ("🛡️ Parade",   "parade",   discord.ButtonStyle.secondary),
        ("🔰 Défense",  "defense",  discord.ButtonStyle.primary),
        ("👊 Coup Bas", "coup_bas", discord.ButtonStyle.secondary),
        ("✨ Spéciale", "speciale", discord.ButtonStyle.success),
    ]

    def __init__(self, joueur, stats, state: CombatState, embed_actif: discord.Embed):
        super().__init__(timeout=35)
        self.joueur = joueur
        self.state = state
        self.embed_actif = embed_actif

        for label, action, style in self.ACTIONS:
            disabled = False
            display = label
            if action == "parade" and stats["parade_cooldown"] > 0:
                disabled = True
                display = f"🛡️ Cooldown ({stats['parade_cooldown']})"
            elif action == "speciale" and not stats["speciale_dispo"]:
                disabled = True
                display = "✨ Utilisée"

            btn = discord.ui.Button(
                label=display,
                style=style if not disabled else discord.ButtonStyle.secondary,
                disabled=disabled,
                custom_id=f"{joueur.id}:{action}",
                row=0,
            )

            async def callback(interaction: discord.Interaction, a=action):
                uid = interaction.user.id
                if uid != self.joueur.id or uid in self.state.choix:
                    await interaction.response.defer(ephemeral=True)
                    return
                self.state.enregistrer(uid, a)
                for child in self.children:
                    child.disabled = True
                embed_wait = discord.Embed(
                    title="✅ Action enregistrée — En attente de l'adversaire...",
                    color=0x555555
                )
                await interaction.response.edit_message(embed=embed_wait, view=self)

            btn.callback = callback
            self.add_item(btn)


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
            embed.set_footer(text=f"Page 1/{len(pages)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── VUE : ACCEPTER/REFUSER UN DUEL ───────────────────────────
class DuelInviteView(discord.ui.View):
    def __init__(self, challenger_id, challenged_id):
        super().__init__(timeout=30)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.accepted = None
        self.accept_interaction = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("❌ Cette invitation ne te concerne pas !", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accepter(self, interaction, button):
        self.accepted = True
        self.accept_interaction = interaction
        self.stop()
        await interaction.response.defer(ephemeral=True)

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def refuser(self, interaction, button):
        self.accepted = False
        self.stop()
        await interaction.response.defer()


# ─── RÉSOLUTION D'UNE ACTION ───────────────────────────
def resoudre_tour(attaquant, att_stats, att_sabre, defenseur, def_stats, def_sabre, action):
    desc = ""
    if action == "attaque":
        if def_stats["parade_active"]:
            rapport = calculer_degats(att_stats, def_stats)
            att_stats["hp"] = max(0, att_stats["hp"] - rapport["degats"])
            def_stats["parade_active"] = False
            desc += f"🔄 Parade de **{defenseur.display_name}** → **{rapport['degats']}** dégâts renvoyés à **{attaquant.display_name}** !\n"
        elif def_stats["defense_active"]:
            rapport = calculer_degats(att_stats, def_stats)
            dmg = max(1, int(rapport["degats"] * 0.4))
            def_stats["hp"] = max(0, def_stats["hp"] - dmg)
            def_stats["defense_active"] = False
            desc += f"🔰 **{defenseur.display_name}** réduit à **{dmg}** dégâts !\n"
        else:
            rapport = calculer_degats(att_stats, def_stats)
            def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])
            for m in rapport["messages"]:
                desc += f"{m}\n"
            if rapport["degats"] > 0:
                desc += f"⚔️ **{attaquant.display_name}** inflige **{rapport['degats']}** dégâts !\n"

    elif action == "coup_bas":
        if def_stats["parade_active"]:
            rapport = calculer_degats(att_stats, def_stats)
            dmg = rapport["degats"] * 2
            def_stats["hp"] = max(0, def_stats["hp"] - dmg)
            def_stats["parade_active"] = False
            desc += f"💥 **{attaquant.display_name}** brise la parade ! **{dmg}** dégâts critiques !\n"
        else:
            rapport = calculer_degats(att_stats, def_stats)
            dmg = max(1, rapport["degats"] // 2)
            def_stats["hp"] = max(0, def_stats["hp"] - dmg)
            desc += f"👊 **{attaquant.display_name}** coup bas : **{dmg}** dégâts.\n"

    elif action == "speciale" and att_stats["speciale_dispo"]:
        rapport = calculer_degats(att_stats, def_stats, utilise_speciale=True, sabre_data=att_sabre)
        def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])
        for m in rapport["messages"]:
            desc += f"{m}\n"
        if rapport["degats"] > 0:
            desc += f"💥 **{rapport['degats']}** dégâts !\n"

    return desc


# ─── FONCTION PRINCIPALE DE COMBAT ───────────────────────────
async def lancer_combat(challenger_interaction, accept_interaction, joueur1, joueur2, sabre1_id, sabre2_id, db):
    channel = challenger_interaction.channel

    profil1 = db.ensure_profil(joueur1.id, joueur1.name)
    profil2 = db.ensure_profil(joueur2.id, joueur2.name)
    sabre1 = get_sabre(sabre1_id)
    sabre2 = get_sabre(sabre2_id)
    stats1 = calculer_stats(profil1, sabre1)
    stats2 = calculer_stats(profil2, sabre2)

    # Jet de dé initial
    while True:
        de1, de2 = random.randint(1, 6), random.randint(1, 6)
        if de1 != de2:
            break
    if de1 > de2:
        ordre = [(joueur1, stats1, sabre1), (joueur2, stats2, sabre2)]
        premier = joueur1.display_name
    else:
        ordre = [(joueur2, stats2, sabre2), (joueur1, stats1, sabre1)]
        premier = joueur2.display_name

    tour = 1
    MAX_TOURS = 20
    historique = []

    labels_action = {
        "attaque": "⚔️ Attaque", "parade": "🛡️ Parade",
        "defense": "🔰 Défense", "coup_bas": "👊 Coup Bas", "speciale": "✨ Spéciale",
    }

    # ─── Message principal dans le canal ───────────────────────────
    embed_debut = discord.Embed(
        title="⚔️ DUEL DE SABRES LASER",
        description=(
            f"🎲 **{joueur1.display_name}** : {de1}  ·  **{joueur2.display_name}** : {de2}\n"
            f"➡️ **{premier} commence !**\n\n"
            f"{joueur1.mention} ({sabre1['emoji']} {sabre1['nom']}) "
            f"VS {joueur2.mention} ({sabre2['emoji']} {sabre2['nom']})"
        ),
        color=0xFF0000
    )
    embed_debut.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_debut.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    msg = await channel.send(embed=embed_debut)

    # ─── Panels ephemeral personnels ───────────────────────────
    embed_init_panel = discord.Embed(
        title="⚔️ Ton panneau de combat",
        description="Tes boutons d'action apparaissent ici à chaque tour. Seul toi vois ce message.",
        color=0x2b2d31
    )
    state = CombatState()
    try:
        panel1 = await challenger_interaction.followup.send(embed=embed_init_panel, ephemeral=True)
        panel2 = await accept_interaction.followup.send(embed=embed_init_panel, ephemeral=True)
    except Exception:
        panel1 = None
        panel2 = None

    await asyncio.sleep(2)

    while stats1["hp"] > 0 and stats2["hp"] > 0 and tour <= MAX_TOURS:

        # ─── Mise à jour message principal ───────────────────────────
        embed_choix = discord.Embed(
            title=f"⚔️ Tour {tour} — Phase de choix",
            description=f"⏳ Les deux joueurs choisissent leur action en privé...",
            color=0xFFD700
        )
        embed_choix.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
        embed_choix.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
        embed_choix.set_footer(text="Chaque joueur voit ses boutons dans son panel privé ↙️")
        await msg.edit(embed=embed_choix, view=None)

        # ─── Panels perso avec boutons ───────────────────────────
        state.reset()

        def make_panel_embed(joueur, stats, adversaire, stats_adv, tour):
            e = discord.Embed(
                title=f"⚔️ Tour {tour} — Choisis ton action !",
                color=0xFFD700
            )
            e.add_field(name=f"❤️ {joueur.display_name} (toi)", value=barre_hp(stats["hp"], stats["hp_max"]), inline=False)
            e.add_field(name=f"❤️ {adversaire.display_name}", value=barre_hp(stats_adv["hp"], stats_adv["hp_max"]), inline=False)
            e.set_footer(text="30 secondes pour choisir !")
            return e

        embed_p1 = make_panel_embed(joueur1, stats1, joueur2, stats2, tour)
        embed_p2 = make_panel_embed(joueur2, stats2, joueur1, stats1, tour)

        view1 = PersonalCombatView(joueur1, stats1, state, embed_p1)
        view2 = PersonalCombatView(joueur2, stats2, state, embed_p2)

        try:
            if panel1:
                await panel1.edit(embed=embed_p1, view=view1)
            if panel2:
                await panel2.edit(embed=embed_p2, view=view2)
        except Exception:
            pass

        await state.attendre(timeout=30)

        # Auto-attaque si timeout
        if joueur1.id not in state.choix:
            state.choix[joueur1.id] = "attaque"
        if joueur2.id not in state.choix:
            state.choix[joueur2.id] = "attaque"

        # Désactiver panels pendant résolution
        embed_wait = discord.Embed(title="⏳ Résolution en cours...", color=0x555555)
        try:
            if panel1:
                await panel1.edit(embed=embed_wait, view=None)
            if panel2:
                await panel2.edit(embed=embed_wait, view=None)
        except Exception:
            pass

        choix1 = state.choix[joueur1.id]
        choix2 = state.choix[joueur2.id]

        # ─── Application des états réactifs ───────────────────────────
        desc_result = (
            f"**{joueur1.display_name}** → {labels_action[choix1]}\n"
            f"**{joueur2.display_name}** → {labels_action[choix2]}\n\n"
        )

        for j, stats, choix in [(joueur1, stats1, choix1), (joueur2, stats2, choix2)]:
            if choix == "parade" and stats["parade_cooldown"] == 0:
                stats["parade_active"] = True
                stats["parade_cooldown"] = 5
                desc_result += f"🛡️ **{j.display_name}** se met en parade !\n"
            elif choix == "defense":
                stats["defense_active"] = True
                desc_result += f"🔰 **{j.display_name}** posture défensive (-60% dégâts) !\n"

        desc_result += "\n"

        # ─── Résolution dans l'ordre d'initiative ───────────────────────────
        for (att, att_stats, att_sabre), (def_, def_stats, def_sabre) in [
            (ordre[0], ordre[1]),
            (ordre[1], ordre[0]),
        ]:
            if stats1["hp"] <= 0 or stats2["hp"] <= 0:
                break
            att_choix = state.choix[att.id]
            if att_choix in ("attaque", "coup_bas", "speciale"):
                desc_result += resoudre_tour(att, att_stats, att_sabre, def_, def_stats, def_sabre, att_choix)

        # Mise à jour cooldowns fin de tour
        for j, stats, choix in [(joueur1, stats1, choix1), (joueur2, stats2, choix2)]:
            if choix != "parade" and stats["parade_cooldown"] > 0:
                stats["parade_cooldown"] -= 1
            if choix != "defense":
                stats["defense_active"] = False

        # ─── Résultat dans le canal ───────────────────────────
        embed_result = discord.Embed(
            title=f"⚔️ Tour {tour} — Résultat",
            description=desc_result,
            color=0xFF4444
        )
        embed_result.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
        embed_result.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)

        historique.append(
            f"**Tour {tour}**\n{desc_result}"
            f"HP : {joueur1.display_name} {stats1['hp']}/{stats1['hp_max']} · "
            f"{joueur2.display_name} {stats2['hp']}/{stats2['hp_max']}"
        )

        await msg.edit(embed=embed_result, view=None)
        await asyncio.sleep(3)
        tour += 1

    # ─── Fin du combat ───────────────────────────
    if stats1["hp"] <= 0 and stats2["hp"] <= 0:
        gagnant, perdant = None, None
        desc_fin = "⚖️ **ÉGALITÉ !**"
    elif stats1["hp"] <= 0:
        gagnant, perdant = joueur2, joueur1
        desc_fin = f"🏆 **{joueur2.display_name} GAGNE !**"
    elif stats2["hp"] <= 0:
        gagnant, perdant = joueur1, joueur2
        desc_fin = f"🏆 **{joueur1.display_name} GAGNE !**"
    else:
        gagnant, perdant = (joueur1, joueur2) if stats1["hp"] >= stats2["hp"] else (joueur2, joueur1)
        desc_fin = f"⏱️ Temps écoulé ! **{gagnant.display_name} gagne aux points !**"

    xp_gain, coins_gain = 50, 100
    if gagnant:
        db.add_xp(gagnant.id, xp_gain)
        db.add_tookcoins(gagnant.id, coins_gain)
        db.add_victoire(gagnant.id)
        db.add_defaite(perdant.id)
        db.sauvegarder(joueur1.id, joueur2.id, gagnant.id, coins_gain, 0)
        desc_fin += f"\n\n🎖️ +{xp_gain} XP | 🪙 +{coins_gain} TookCoins"

    embed_fin = discord.Embed(title="⚔️ FIN DU DUEL", description=desc_fin, color=0xFFD700)
    embed_fin.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_fin.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    await msg.edit(embed=embed_fin, view=HistoriqueView(historique))

    embed_termine = discord.Embed(title="⚔️ Combat terminé !", description=desc_fin, color=0x2b2d31)
    try:
        if panel1:
            await panel1.edit(embed=embed_termine, view=None)
        if panel2:
            await panel2.edit(embed=embed_termine, view=None)
    except Exception:
        pass


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
                f"{interaction.user.mention} défie {adversaire.mention} en duel !\n\n"
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
            await interaction.followup.send(
                f"✅ {adversaire.display_name} accepte ! **Vérifie tes messages privés du bot** pour tes boutons d'action."
            )
            await lancer_combat(
                interaction, view.accept_interaction,
                interaction.user, adversaire,
                sabre1_id, sabre2_id, db
            )

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
            await interaction.response.send_message(f"❌ Aucun duel pour {membre.display_name} !", ephemeral=True)
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
            embed.add_field(name=f"{rarete_info['emoji']} {rarete_info['label']}", value=texte, inline=False)
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
                f"❌ Pas assez de TookCoins ! Il te faut 🪙 {sabre['prix']} (tu as 🪙 {coins})", ephemeral=True
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
        await interaction.response.send_message(f"✅ {sabre['emoji']} **{sabre['nom']}** équipé !")

    @bot.tree.command(name="mon_sabre", description="Voir ton sabre équipé")
    async def mon_sabre(interaction: discord.Interaction):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        sabre_id = profil_data.get("sabre_equipe", "bleu")
        sabre = get_sabre(sabre_id)
        rarete = RARETES[sabre["rarete"]]
        inventaire = profil_data.get("sabres", ["bleu"])
        embed = discord.Embed(title=f"{sabre['emoji']} {sabre['nom']}", description=sabre["description"], color=0x00BFFF)
        embed.add_field(name="Rareté", value=f"{rarete['emoji']} {rarete['label']}", inline=True)
        embed.add_field(
            name="Capacité Spéciale",
            value=f"✨ **{sabre['speciale']['nom']}**\n_{sabre['speciale']['description']}_",
            inline=False
        )
        embed.add_field(name="Sabres possédés", value=f"{len(inventaire)} sabre(s)", inline=True)
        embed.set_footer(text="Utilise /equiper_sabre pour changer de sabre")
        await interaction.response.send_message(embed=embed)