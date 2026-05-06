# duel_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from database import get_historique, get_combat_xp_progress, get_xp_pour_prochain_niveau
from duel_sabres import get_sabre, get_tous_les_sabres, RARETES
from duel_combat import calculer_stats, calculer_degats, barre_hp
from duel_minigames import run_minigame


# ─── EMBED HELPER : PHASE DE CHOIX ───────────────────────────
def make_tour_embed(tour, j1, s1, sb1, j2, s2, sb2, phase=1):
    if phase == 1:
        desc   = "Les deux joueurs choisissent chacun leur tour sans voir le choix adverse."
        footer = f"⌛ {j1.display_name} choisit... · ⏳ {j2.display_name} en attente — 30 secondes !"
    elif phase == 2:
        desc   = "Les deux joueurs choisissent chacun leur tour sans voir le choix adverse."
        footer = f"✅ {j1.display_name} a choisi ! · ⌛ {j2.display_name} choisit... — 30 secondes !"
    else:
        desc   = "⚙️ Résolution en cours..."
        footer = None

    embed = discord.Embed(
        title=f"⚔️ Tour {tour} — Choisissez votre action !",
        description=desc,
        color=0xFFD700,
    )

    # Joueur 1
    parade1 = "🛡️ Parade dispo" if s1["parade_cooldown"] == 0 else f"🛡️ Cooldown ({s1['parade_cooldown']})"
    spec1   = (f"✨ **{sb1['speciale']['nom']}** — _{sb1['speciale']['description']}_"
               if s1["speciale_dispo"] else "✨ ~~Spéciale utilisée~~")
    embed.add_field(
        name=f"❤️ {j1.display_name}",
        value=f"{barre_hp(s1['hp'], s1['hp_max'])}\n{parade1}\n{spec1}",
        inline=False,
    )

    # Joueur 2
    parade2 = "🛡️ Parade dispo" if s2["parade_cooldown"] == 0 else f"🛡️ Cooldown ({s2['parade_cooldown']})"
    spec2   = (f"✨ **{sb2['speciale']['nom']}** — _{sb2['speciale']['description']}_"
               if s2["speciale_dispo"] else "✨ ~~Spéciale utilisée~~")
    embed.add_field(
        name=f"❤️ {j2.display_name}",
        value=f"{barre_hp(s2['hp'], s2['hp_max'])}\n{parade2}\n{spec2}",
        inline=False,
    )

    if footer:
        embed.set_footer(text=footer)
    return embed


# ─── VUE : BOUTONS DE COMBAT (public, séquentiel) ───────────────────────────
class TourView(discord.ui.View):
    ACTIONS = [
        ("⚔️ Attaquer",  "attaque",  discord.ButtonStyle.danger,    0),
        ("🛡️ Parade",    "parade",   discord.ButtonStyle.secondary,  0),
        ("🔰 Défense",   "defense",  discord.ButtonStyle.primary,    0),
        ("👊 Coup Bas",  "coup_bas", discord.ButtonStyle.secondary,  1),
        ("✨ Spéciale",  "speciale", discord.ButtonStyle.success,    1),
    ]

    def __init__(self, joueur_actif, stats_actif, event_choix, choix_state, tour):
        super().__init__(timeout=35)
        self.joueur_actif = joueur_actif
        self.event_choix  = event_choix
        self.choix_state  = choix_state

        for label, action, style, row in self.ACTIONS:
            disabled = False
            display  = label
            if action == "parade" and stats_actif["parade_cooldown"] > 0:
                disabled = True
                display  = f"🛡️ Cooldown ({stats_actif['parade_cooldown']})"
            elif action == "speciale" and not stats_actif["speciale_dispo"]:
                disabled = True
                display  = "✨ Utilisée"

            btn = discord.ui.Button(
                label=display,
                style=style if not disabled else discord.ButtonStyle.secondary,
                disabled=disabled,
                custom_id=f"act_{joueur_actif.id}_{tour}_{action}",
                row=row,
            )

            async def callback(interaction: discord.Interaction, a=action):
                if interaction.user.id != self.joueur_actif.id:
                    await interaction.response.defer()
                    return
                if self.joueur_actif.id in self.choix_state:
                    await interaction.response.defer()
                    return
                self.choix_state[self.joueur_actif.id] = a
                for child in self.children:
                    child.disabled = True
                await interaction.response.defer()
                self.event_choix.set()

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
        self.challenger_id     = challenger_id
        self.challenged_id     = challenged_id
        self.accepted          = None
        self.accept_interaction = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("❌ Cette invitation ne te concerne pas !", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accepter(self, interaction, button):
        self.accepted           = True
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
            dmg     = max(1, int(rapport["degats"] * 0.4))
            def_stats["hp"]            = max(0, def_stats["hp"] - dmg)
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
            dmg     = rapport["degats"] * 2
            def_stats["hp"]            = max(0, def_stats["hp"] - dmg)
            def_stats["parade_active"] = False
            desc += f"💥 **{attaquant.display_name}** brise la parade ! **{dmg}** dégâts critiques !\n"
        else:
            rapport = calculer_degats(att_stats, def_stats)
            dmg     = max(1, rapport["degats"] // 2)
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
    sabre1  = get_sabre(sabre1_id)
    sabre2  = get_sabre(sabre2_id)
    stats1  = calculer_stats(profil1, sabre1)
    stats2  = calculer_stats(profil2, sabre2)

    # Jet de dé initial
    while True:
        de1, de2 = random.randint(1, 6), random.randint(1, 6)
        if de1 != de2:
            break
    if de1 > de2:
        ordre  = [(joueur1, stats1, sabre1), (joueur2, stats2, sabre2)]
        premier = joueur1.display_name
    else:
        ordre  = [(joueur2, stats2, sabre2), (joueur1, stats1, sabre1)]
        premier = joueur2.display_name

    tour         = 1
    MAX_TOURS    = 20
    historique   = []
    next_minigame = random.randint(5, 8)   # premier mini-jeu entre le tour 5 et 8

    labels_action = {
        "attaque":  "⚔️ Attaque",
        "parade":   "🛡️ Parade",
        "defense":  "🔰 Défense",
        "coup_bas": "👊 Coup Bas",
        "speciale": "✨ Spéciale",
    }

    embed_debut = discord.Embed(
        title="⚔️ DUEL DE SABRES LASER",
        description=(
            f"🎲 **{joueur1.display_name}** : {de1}  ·  **{joueur2.display_name}** : {de2}\n"
            f"➡️ **{premier} commence !**\n\n"
            f"{joueur1.mention} ({sabre1['emoji']} {sabre1['nom']}) "
            f"VS {joueur2.mention} ({sabre2['emoji']} {sabre2['nom']})"
        ),
        color=0xFF0000,
    )
    embed_debut.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_debut.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    msg = await channel.send(embed=embed_debut)

    await asyncio.sleep(2)

    while stats1["hp"] > 0 and stats2["hp"] > 0 and tour <= MAX_TOURS:

        # ─── Mini-jeu ────────────────────────────────────────────────
        if tour == next_minigame:
            mini_desc     = await run_minigame(msg, joueur1, stats1, joueur2, stats2, tour)
            next_minigame = tour + random.randint(5, 8)

            # Reprendre si combat pas terminé
            if stats1["hp"] <= 0 or stats2["hp"] <= 0:
                break

            resume = discord.Embed(
                title="⚔️ Le combat reprend !",
                description=mini_desc,
                color=0xFF4444,
            )
            resume.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
            resume.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
            await msg.edit(embed=resume, view=None)
            await asyncio.sleep(2)
            tour += 1
            continue

        # ─── Phase 1 : Joueur 1 choisit ──────────────────────────────
        choix_state = {}
        event_p1    = asyncio.Event()
        view_p1     = TourView(joueur1, stats1, event_p1, choix_state, tour)
        await msg.edit(
            embed=make_tour_embed(tour, joueur1, stats1, sabre1, joueur2, stats2, sabre2, phase=1),
            view=view_p1,
        )
        try:
            await asyncio.wait_for(event_p1.wait(), timeout=30)
        except asyncio.TimeoutError:
            choix_state[joueur1.id] = "attaque"

        # ─── Phase 2 : Joueur 2 choisit ──────────────────────────────
        event_p2 = asyncio.Event()
        view_p2  = TourView(joueur2, stats2, event_p2, choix_state, tour)
        await msg.edit(
            embed=make_tour_embed(tour, joueur1, stats1, sabre1, joueur2, stats2, sabre2, phase=2),
            view=view_p2,
        )
        try:
            await asyncio.wait_for(event_p2.wait(), timeout=30)
        except asyncio.TimeoutError:
            choix_state[joueur2.id] = "attaque"

        choix1 = choix_state.get(joueur1.id, "attaque")
        choix2 = choix_state.get(joueur2.id, "attaque")

        # ─── Application des états réactifs ──────────────────────────
        desc_result = (
            f"**{joueur1.display_name}** → {labels_action[choix1]}\n"
            f"**{joueur2.display_name}** → {labels_action[choix2]}\n\n"
        )

        for j, stats, choix in [(joueur1, stats1, choix1), (joueur2, stats2, choix2)]:
            if choix == "parade" and stats["parade_cooldown"] == 0:
                stats["parade_active"]   = True
                stats["parade_cooldown"] = 5
                desc_result += f"🛡️ **{j.display_name}** se met en parade !\n"
            elif choix == "defense":
                stats["defense_active"] = True
                desc_result += f"🔰 **{j.display_name}** posture défensive (-60% dégâts) !\n"

        desc_result += "\n"

        # ─── Résolution dans l'ordre d'initiative ────────────────────
        for (att, att_stats, att_sabre), (def_, def_stats, def_sabre) in [
            (ordre[0], ordre[1]),
            (ordre[1], ordre[0]),
        ]:
            if stats1["hp"] <= 0 or stats2["hp"] <= 0:
                break
            att_choix = choix_state[att.id]
            if att_choix in ("attaque", "coup_bas", "speciale"):
                desc_result += resoudre_tour(att, att_stats, att_sabre, def_, def_stats, def_sabre, att_choix)

        # Mise à jour cooldowns fin de tour
        for j, stats, choix in [(joueur1, stats1, choix1), (joueur2, stats2, choix2)]:
            if choix != "parade" and stats["parade_cooldown"] > 0:
                stats["parade_cooldown"] -= 1
            if choix != "defense":
                stats["defense_active"] = False

        # ─── Résultat ────────────────────────────────────────────────
        embed_result = discord.Embed(
            title=f"⚔️ Tour {tour} — Résultat",
            description=desc_result,
            color=0xFF4444,
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

    # ─── Fin du combat ───────────────────────────────────────────────
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

    tookcoins_gain = 100
    xp_combat_win  = 150
    xp_combat_lose = 50

    if gagnant:
        db.add_tookcoins(gagnant.id, tookcoins_gain)
        db.add_victoire(gagnant.id)
        db.add_defaite(perdant.id)
        db.sauvegarder(joueur1.id, joueur2.id, gagnant.id, tookcoins_gain, 0)
        desc_fin += f"\n\n🪙 +{tookcoins_gain} TookCoins"

        # XP de combat
        new_lvl_g, lvl_up_g = db.add_combat_xp(gagnant.id, xp_combat_win)
        new_lvl_p, lvl_up_p = db.add_combat_xp(perdant.id, xp_combat_lose)
        desc_fin += f"\n⚔️ +{xp_combat_win} XP de combat ({gagnant.display_name}) | +{xp_combat_lose} XP ({perdant.display_name})"

        if lvl_up_g:
            desc_fin += f"\n⬆️ **{gagnant.display_name}** passe au **Niveau de combat {new_lvl_g}** ! (+1 point de stat à attribuer)"
        if lvl_up_p:
            desc_fin += f"\n⬆️ **{perdant.display_name}** passe au **Niveau de combat {new_lvl_p}** ! (+1 point de stat à attribuer)"

    embed_fin = discord.Embed(title="⚔️ FIN DU DUEL", description=desc_fin, color=0xFFD700)
    embed_fin.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_fin.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    await msg.edit(embed=embed_fin, view=HistoriqueView(historique))


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

        profil1   = db.ensure_profil(interaction.user.id, interaction.user.name)
        profil2   = db.ensure_profil(adversaire.id, adversaire.name)
        sabre1_id = profil1.get("sabre_equipe", "bleu")
        sabre2_id = profil2.get("sabre_equipe", "bleu")
        sabre1    = get_sabre(sabre1_id)
        sabre2    = get_sabre(sabre2_id)

        view  = DuelInviteView(interaction.user.id, adversaire.id)
        embed = discord.Embed(
            title="⚔️ DÉFI EN DUEL !",
            description=(
                f"{interaction.user.mention} défie {adversaire.mention} en duel !\n\n"
                f"{interaction.user.display_name} : {sabre1['emoji']} **{sabre1['nom']}** "
                f"({RARETES[sabre1['rarete']]['emoji']} {RARETES[sabre1['rarete']]['label']})\n"
                f"{adversaire.display_name} : {sabre2['emoji']} **{sabre2['nom']}** "
                f"({RARETES[sabre2['rarete']]['emoji']} {RARETES[sabre2['rarete']]['label']})"
            ),
            color=0xFF0000,
        )
        embed.set_footer(text=f"{adversaire.display_name} a 30 secondes pour accepter ou refuser !")
        await interaction.response.send_message(embed=embed, view=view)
        await view.wait()

        if view.accepted is None:
            await interaction.followup.send(f"⏱️ {adversaire.display_name} n'a pas répondu. Duel annulé.")
        elif not view.accepted:
            await interaction.followup.send(f"❌ {adversaire.display_name} a refusé le duel.")
        else:
            await interaction.followup.send(f"✅ {adversaire.display_name} accepte ! Le combat commence !")
            await lancer_combat(
                interaction, view.accept_interaction,
                interaction.user, adversaire,
                sabre1_id, sabre2_id, db,
            )

    @bot.tree.command(name="profil", description="Voir ton profil de duel")
    @app_commands.describe(membre="Le membre dont tu veux voir le profil")
    async def profil(interaction: discord.Interaction, membre: discord.Member = None):
        membre      = membre or interaction.user
        profil_data = db.ensure_profil(membre.id, membre.name)
        sabre_id    = profil_data.get("sabre_equipe", "bleu")
        sabre       = get_sabre(sabre_id)
        rarete      = RARETES[sabre["rarete"]]
        total       = profil_data["victoires"] + profil_data["defaites"]
        ratio       = f"{profil_data['victoires']}/{total}" if total > 0 else "0/0"

        # Niveau de combat + progression
        combat_xp   = profil_data.get("combat_xp", 0)
        clvl, xp_in, xp_needed = get_combat_xp_progress(combat_xp)
        stat_points = profil_data.get("stat_points", 0)

        embed = discord.Embed(title=f"⚔️ Profil de {membre.display_name}", color=discord.Color.red())
        embed.set_thumbnail(url=membre.display_avatar.url)

        # Stats de base
        embed.add_field(name="💰 TookCoins",  value=f"**{profil_data['tookcoins']}** 🪙", inline=True)
        embed.add_field(name="🏆 Victoires",  value=f"**{profil_data['victoires']}**",     inline=True)
        embed.add_field(name="💀 Défaites",   value=f"**{profil_data['defaites']}**",      inline=True)
        embed.add_field(name="📊 Ratio",      value=ratio,                                  inline=True)
        embed.add_field(
            name="⚔️ Sabre équipé",
            value=f"{sabre['emoji']} {sabre['nom']} ({rarete['emoji']} {rarete['label']})",
            inline=True,
        )
        embed.add_field(name="🗂️ Collection", value=f"**{len(profil_data['sabres'])}** sabre(s)", inline=True)

        # Niveau de combat
        if xp_needed > 0:
            xp_bar_pct = int((xp_in / xp_needed) * 10)
            xp_bar     = "🟦" * xp_bar_pct + "⬛" * (10 - xp_bar_pct)
            xp_txt     = f"{xp_bar} {xp_in}/{xp_needed} XP"
        else:
            xp_txt = "Niveau maximum atteint !"

        pts_txt = f" *(⚠️ {stat_points} point(s) à attribuer !)*" if stat_points > 0 else ""
        embed.add_field(
            name=f"⚔️ Niveau de Combat : {clvl}{pts_txt}",
            value=xp_txt,
            inline=False,
        )

        # Stats attribuées
        sf = profil_data.get('stat_force',     0)
        sa = profil_data.get('stat_agilite',   0)
        sd = profil_data.get('stat_defense',   0)
        se = profil_data.get('stat_endurance', 0)
        sc = profil_data.get('stat_chance',    0)
        stats_txt = (
            f"⚔️ **Force {sf}** → +{sf * 5} attaque\n"
            f"💨 **Agilité {sa}** → +{sa * 4}% esquive\n"
            f"🛡️ **Défense {sd}** → +{sd * 3} défense\n"
            f"❤️ **Endurance {se}** → +{se * 25} HP max\n"
            f"🍀 **Chance {sc}** → +{sc * 5}% critique"
        )
        embed.add_field(name="📈 Statistiques de combat", value=stats_txt, inline=False)

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="attribuer_stat", description="Attribuer un point de stat gagné en montant de niveau de combat")
    @app_commands.describe(stat="La statistique à améliorer")
    @app_commands.choices(stat=[
        app_commands.Choice(name="⚔️ Force       (+5 attaque par point)",    value="force"),
        app_commands.Choice(name="💨 Agilité     (+4% esquive par point)",   value="agilite"),
        app_commands.Choice(name="🛡️ Défense     (+3 défense par point)",    value="defense"),
        app_commands.Choice(name="❤️ Endurance   (+25 HP max par point)",    value="endurance"),
        app_commands.Choice(name="🍀 Chance      (+5% critique par point)",  value="chance"),
    ])
    async def attribuer_stat(interaction: discord.Interaction, stat: str):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        points      = profil_data.get("stat_points", 0)
        if points <= 0:
            await interaction.response.send_message(
                "❌ Tu n'as aucun point de stat disponible ! Gagne des duels pour monter de niveau de combat.",
                ephemeral=True,
            )
            return
        success = db.attribuer_stat(interaction.user.id, stat)
        if not success:
            await interaction.response.send_message("❌ Erreur lors de l'attribution.", ephemeral=True)
            return

        stat_labels = {
            "force":     "⚔️ Force",
            "agilite":   "💨 Agilité",
            "defense":   "🛡️ Défense",
            "endurance": "❤️ Endurance",
            "chance":    "🍀 Chance",
        }
        stat_effects = {
            "force":     "+5 attaque",
            "agilite":   "+4% chance d'esquive",
            "defense":   "+3 défense",
            "endurance": "+25 HP maximum",
            "chance":    "+5% chance de critique",
        }
        embed = discord.Embed(
            title="📈 Stat améliorée !",
            description=(
                f"**{stat_labels[stat]}** augmentée d'un point !\n"
                f"Effet : _{stat_effects[stat]}_\n\n"
                f"Il te reste **{points - 1}** point(s) à attribuer."
            ),
            color=0x00FF88,
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="statpoint", description="Attribuer un point de stat gagné en montant de niveau de combat")
    @app_commands.describe(stat="La statistique à améliorer")
    @app_commands.choices(stat=[
        app_commands.Choice(name="⚔️ Force       (+5 attaque par point)",    value="force"),
        app_commands.Choice(name="💨 Agilité     (+4% esquive par point)",   value="agilite"),
        app_commands.Choice(name="🛡️ Défense     (+3 défense par point)",    value="defense"),
        app_commands.Choice(name="❤️ Endurance   (+25 HP max par point)",    value="endurance"),
        app_commands.Choice(name="🍀 Chance      (+5% critique par point)",  value="chance"),
    ])
    async def statpoint(interaction: discord.Interaction, stat: str):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        points      = profil_data.get("stat_points", 0)
        if points <= 0:
            await interaction.response.send_message(
                "❌ Tu n'as aucun point de stat disponible ! Gagne des duels pour monter de niveau de combat.",
                ephemeral=True,
            )
            return
        success = db.attribuer_stat(interaction.user.id, stat)
        if not success:
            await interaction.response.send_message("❌ Erreur lors de l'attribution.", ephemeral=True)
            return

        stat_labels = {
            "force":     "⚔️ Force",
            "agilite":   "💨 Agilité",
            "defense":   "🛡️ Défense",
            "endurance": "❤️ Endurance",
            "chance":    "🍀 Chance",
        }
        stat_effects = {
            "force":     "+5 attaque",
            "agilite":   "+4% chance d'esquive",
            "defense":   "+3 défense",
            "endurance": "+25 HP maximum",
            "chance":    "+5% chance de critique",
        }
        embed = discord.Embed(
            title="📈 Stat améliorée !",
            description=(
                f"**{stat_labels[stat]}** augmentée d'un point !\n"
                f"Effet : _{stat_effects[stat]}_\n\n"
                f"Il te reste **{points - 1}** point(s) à attribuer."
            ),
            color=0x00FF88,
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="collection", description="Voir ta collection de sabres")
    @app_commands.describe(membre="Le membre dont tu veux voir la collection")
    async def collection(interaction: discord.Interaction, membre: discord.Member = None):
        membre      = membre or interaction.user
        profil_data = db.ensure_profil(membre.id, membre.name)
        sabres      = profil_data.get("sabres", ["bleu"])
        sabre_equipe = profil_data.get("sabre_equipe", "bleu")

        embed = discord.Embed(title=f"🗡️ Collection de {membre.display_name}", color=discord.Color.blue())
        description = ""
        for sabre_id in sabres:
            sabre = get_sabre(sabre_id)
            if not sabre:
                continue
            rarete  = RARETES[sabre["rarete"]]
            equipe  = " ← **équipé**" if sabre_id == sabre_equipe else ""
            description += f"{sabre['emoji']} **{sabre['nom']}** {rarete['emoji']} {rarete['label']}{equipe}\n"
        embed.description = description or "Aucun sabre."
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="historique", description="Voir tes derniers duels")
    @app_commands.describe(membre="Le membre dont tu veux voir l'historique")
    async def historique_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        duels  = get_historique(membre.id, limit=5)
        if not duels:
            await interaction.response.send_message(f"❌ Aucun duel pour {membre.display_name} !", ephemeral=True)
            return
        embed = discord.Embed(title=f"📜 Historique de {membre.display_name}", color=discord.Color.blurple())
        description = ""
        for duel in duels:
            gagne       = str(duel["gagnant_id"]) == str(membre.id)
            result      = "✅ Victoire" if gagne else "❌ Défaite"
            adversaire_id = duel["user_id_2"] if str(duel["user_id_1"]) == str(membre.id) else duel["user_id_1"]
            try:
                adversaire     = await interaction.client.fetch_user(int(adversaire_id))
                adversaire_nom = adversaire.name
            except Exception:
                adversaire_nom = f"Inconnu ({adversaire_id})"
            coins       = duel["tookcoins_gagnant"] if gagne else duel["tookcoins_perdant"]
            description += f"{result} vs **{adversaire_nom}** — {coins} 🪙 | {duel['date'][:10]}\n"
        embed.description = description
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="boutique_sabres", description="Voir la boutique des sabres laser")
    async def boutique_sabres(interaction: discord.Interaction):
        sabres      = get_tous_les_sabres()
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        inventaire  = profil_data.get("sabres", ["bleu"])
        embed       = discord.Embed(title="🗡️ BOUTIQUE DES SABRES LASER", color=0x00BFFF)
        for rarete_id, rarete_info in RARETES.items():
            sabres_rarete = [s for s in sabres.values() if s["rarete"] == rarete_id]
            if not sabres_rarete:
                continue
            texte = ""
            for s in sabres_rarete:
                possede  = "✅" if s["id"] in inventaire else ""
                prix_txt = "**GRATUIT**" if s["prix"] == 0 else f"🪙 {s['prix']}"
                texte   += f"{s['emoji']} **{s['nom']}** {possede}\n{prix_txt} | ✨ {s['speciale']['nom']}\n\n"
            embed.add_field(name=f"{rarete_info['emoji']} {rarete_info['label']}", value=texte, inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="acheter_sabre", description="Acheter un sabre laser")
    @app_commands.describe(sabre_id="L'ID du sabre à acheter (ex: rouge, violet, arc_en_ciel...)")
    async def acheter_sabre(interaction: discord.Interaction, sabre_id: str):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        sabre       = get_sabre(sabre_id)
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
            color=0x00FF00,
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="equiper_sabre", description="Équiper un sabre laser")
    @app_commands.describe(sabre_id="L'ID du sabre à équiper")
    async def equiper_sabre(interaction: discord.Interaction, sabre_id: str):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        inventaire  = profil_data.get("sabres", ["bleu"])
        if sabre_id not in inventaire:
            await interaction.response.send_message("❌ Tu ne possèdes pas ce sabre !", ephemeral=True)
            return
        sabre = get_sabre(sabre_id)
        db.update_profil(interaction.user.id, {"sabre_equipe": sabre_id})
        await interaction.response.send_message(f"✅ {sabre['emoji']} **{sabre['nom']}** équipé !")

    @bot.tree.command(name="mon_sabre", description="Voir ton sabre équipé")
    async def mon_sabre(interaction: discord.Interaction):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        sabre_id    = profil_data.get("sabre_equipe", "bleu")
        sabre       = get_sabre(sabre_id)
        rarete      = RARETES[sabre["rarete"]]
        inventaire  = profil_data.get("sabres", ["bleu"])
        embed = discord.Embed(title=f"{sabre['emoji']} {sabre['nom']}", description=sabre["description"], color=0x00BFFF)
        embed.add_field(name="Rareté", value=f"{rarete['emoji']} {rarete['label']}", inline=True)
        embed.add_field(
            name="Capacité Spéciale",
            value=f"✨ **{sabre['speciale']['nom']}**\n_{sabre['speciale']['description']}_",
            inline=False,
        )
        embed.add_field(name="Sabres possédés", value=f"{len(inventaire)} sabre(s)", inline=True)
        embed.set_footer(text="Utilise /equiper_sabre pour changer de sabre")
        await interaction.response.send_message(embed=embed)
