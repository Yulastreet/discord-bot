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
def make_tour_embed(tour, j1, s1, sb1, j2, s2, sb2, phase=1,
                    phase_actif=None, phase_attente=None):
    """phase_actif / phase_attente permettent de customiser le footer selon
    qui joue en premier ce tour (initiative variable selon le jet de dé)."""
    actif   = phase_actif   or j1
    attente = phase_attente or j2
    if phase == 1:
        desc   = "Les deux joueurs choisissent chacun leur tour sans voir le choix adverse."
        footer = f"⌛ {actif.display_name} choisit... · ⏳ {attente.display_name} en attente — 30 secondes !"
    elif phase == 2:
        desc   = "Les deux joueurs choisissent chacun leur tour sans voir le choix adverse."
        footer = f"✅ {attente.display_name} a choisi ! · ⌛ {actif.display_name} choisit... — 30 secondes !"
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
class NextTurnView(discord.ui.View):
    """Bouton 'Tour suivant' entre deux tours, cliquable par les deux duellistes."""

    def __init__(self, joueur1, joueur2, event_next, tour, label="Tour suivant ⚔️"):
        super().__init__(timeout=120)
        self.joueur1   = joueur1
        self.joueur2   = joueur2
        self.event     = event_next

        btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"next_tour_{tour}",
        )
        async def cb(interaction: discord.Interaction):
            if interaction.user.id not in (self.joueur1.id, self.joueur2.id):
                try:
                    await interaction.response.send_message(
                        "❌ Seuls les duellistes peuvent passer au tour suivant.", ephemeral=True)
                except Exception:
                    pass
                return
            for child in self.children:
                child.disabled = True
                child.label    = f"⏩ Suivant — {interaction.user.display_name}"
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
            self.event.set()
        btn.callback = cb
        self.add_item(btn)


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
            # rapport["degats"] inclut deja tous les bonus (mini-jeu +30%, rage, overcharge, crit, etc.)
            # → on renvoie 100% des degats bonus inclus a l'attaquant
            for m in rapport["messages"]:
                desc += f"{m}\n"
            att_stats["hp"] = max(0, att_stats["hp"] - rapport["degats"])
            def_stats["parade_active"] = False
            desc += f"🔄 Parade de **{defenseur.display_name}** → **{rapport['degats']}** dégâts renvoyés à **{attaquant.display_name}** (bonus inclus) !\n"
        elif def_stats["defense_active"]:
            rapport = calculer_degats(att_stats, def_stats)
            dmg     = max(1, int(rapport["degats"] * 0.4))
            for m in rapport["messages"]:
                desc += f"{m}\n"
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
            for m in rapport["messages"]:
                desc += f"{m}\n"
            def_stats["hp"]            = max(0, def_stats["hp"] - dmg)
            def_stats["parade_active"] = False
            desc += f"💥 **{attaquant.display_name}** brise la parade ! **{dmg}** dégâts critiques !\n"
        else:
            rapport = calculer_degats(att_stats, def_stats)
            dmg     = max(1, rapport["degats"] // 2)
            for m in rapport["messages"]:
                desc += f"{m}\n"
            def_stats["hp"] = max(0, def_stats["hp"] - dmg)
            desc += f"👊 **{attaquant.display_name}** coup bas : **{dmg}** dégâts.\n"

    elif action == "speciale" and att_stats["speciale_dispo"]:
        rapport = calculer_degats(att_stats, def_stats, utilise_speciale=True, sabre_data=att_sabre)
        for m in rapport["messages"]:
            desc += f"{m}\n"
        if def_stats["parade_active"]:
            # Parade vs speciale : seulement 50% renvoyes a l'attaquant, l'autre 50% subi par le defenseur
            reflected = rapport["degats"] // 2
            taken     = rapport["degats"] - reflected
            att_stats["hp"] = max(0, att_stats["hp"] - reflected)
            def_stats["hp"] = max(0, def_stats["hp"] - taken)
            def_stats["parade_active"] = False
            desc += (
                f"🔄 Parade vs spéciale : **{reflected}** dégâts renvoyés à "
                f"**{attaquant.display_name}**, **{taken}** subis par "
                f"**{defenseur.display_name}**.\n"
            )
        else:
            def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])
            if rapport["degats"] > 0:
                desc += f"💥 **{rapport['degats']}** dégâts !\n"

    return desc


# ─── FONCTION PRINCIPALE DE COMBAT ───────────────────────────
async def lancer_combat(challenger_interaction, accept_interaction, joueur1, joueur2, sabre1_id, sabre2_id, db, nerf=False):
    channel = challenger_interaction.channel

    profil1 = db.ensure_profil(joueur1.id, joueur1.name)
    profil2 = db.ensure_profil(joueur2.id, joueur2.name)
    sabre1  = get_sabre(sabre1_id)
    sabre2  = get_sabre(sabre2_id)

    if nerf:
        _nerf = {"combat_level": 1, "stat_force": 0, "stat_agilite": 0,
                 "stat_defense": 0, "stat_endurance": 0, "stat_chance": 0}
        profil1 = {**profil1, **_nerf}
        profil2 = {**profil2, **_nerf}

    stats1  = calculer_stats(profil1, sabre1)
    stats2  = calculer_stats(profil2, sabre2)

    # ─── Jet de dé d'initiative (message dédié, suspense) ──────────
    nerf_label = " 〔MODE NERF〕" if nerf else ""

    dice_embed = discord.Embed(
        title="🎲 Jet de dé d'initiative",
        description=(
            f"**{joueur1.display_name}** {sabre1['emoji']}  vs  "
            f"**{joueur2.display_name}** {sabre2['emoji']}\n\n"
            f"_Lancement des dés..._"
        ),
        color=0xFFD700,
    )
    dice_msg = await channel.send(embed=dice_embed)
    await asyncio.sleep(1.5)

    while True:
        de1, de2 = random.randint(1, 6), random.randint(1, 6)
        if de1 != de2:
            break
    if de1 > de2:
        ordre   = [(joueur1, stats1, sabre1), (joueur2, stats2, sabre2)]
        premier_obj = joueur1
    else:
        ordre   = [(joueur2, stats2, sabre2), (joueur1, stats1, sabre1)]
        premier_obj = joueur2

    dice_result = discord.Embed(
        title="🎲 Initiative",
        description=(
            f"🎲 **{joueur1.display_name}** : `{de1}`\n"
            f"🎲 **{joueur2.display_name}** : `{de2}`\n\n"
            f"➡️ **{premier_obj.display_name}** prend l'initiative et joue en premier !"
        ),
        color=0x00FF88,
    )
    await dice_msg.edit(embed=dice_result)
    await asyncio.sleep(2.5)

    # ─── Embed de début de combat (message séparé) ─────────────────
    embed_debut = discord.Embed(
        title=f"⚔️ DUEL DE SABRES LASER{nerf_label}",
        description=(
            f"{joueur1.mention} ({sabre1['emoji']} {sabre1['nom']}) "
            f"VS {joueur2.mention} ({sabre2['emoji']} {sabre2['nom']})\n\n"
            f"⚡ **{premier_obj.display_name}** commence."
            + (f"\n\n⚖️ *Stats de niveau et points attribués ignorés — terrain égal !*" if nerf else "")
        ),
        color=0x00AAFF if nerf else 0xFF0000,
    )
    embed_debut.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_debut.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    msg = await channel.send(embed=embed_debut)
    await asyncio.sleep(2)

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
            resume.set_footer(text="📖 Lisez le récap du mini-jeu puis cliquez sur Tour suivant.")

            event_next = asyncio.Event()
            view_next  = NextTurnView(joueur1, joueur2, event_next, tour, label="Tour suivant ⚔️")
            await msg.edit(embed=resume, view=view_next)
            try:
                await asyncio.wait_for(event_next.wait(), timeout=120)
            except asyncio.TimeoutError:
                pass
            tour += 1
            continue

        # ─── Phase 1 : c'est le gagnant du jet de dé qui choisit en premier
        # ordre[0] = qui a remporté l'initiative (peut être joueur1 OU joueur2)
        joueur_p1, stats_p1, _ = ordre[0]
        joueur_p2, stats_p2, _ = ordre[1]

        choix_state = {}
        event_p1    = asyncio.Event()
        view_p1     = TourView(joueur_p1, stats_p1, event_p1, choix_state, tour)
        await msg.edit(
            embed=make_tour_embed(
                tour, joueur1, stats1, sabre1, joueur2, stats2, sabre2,
                phase=1, phase_actif=joueur_p1, phase_attente=joueur_p2,
            ),
            view=view_p1,
        )
        try:
            await asyncio.wait_for(event_p1.wait(), timeout=30)
        except asyncio.TimeoutError:
            choix_state[joueur_p1.id] = "attaque"

        # ─── Phase 2 : le second joue ────────────────────────────────
        event_p2 = asyncio.Event()
        view_p2  = TourView(joueur_p2, stats_p2, event_p2, choix_state, tour)
        await msg.edit(
            embed=make_tour_embed(
                tour, joueur1, stats1, sabre1, joueur2, stats2, sabre2,
                phase=2, phase_actif=joueur_p2, phase_attente=joueur_p1,
            ),
            view=view_p2,
        )
        try:
            await asyncio.wait_for(event_p2.wait(), timeout=30)
        except asyncio.TimeoutError:
            choix_state[joueur_p2.id] = "attaque"

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
            # Parade non consommée par une attaque adverse : on la coupe.
            # Seule la défense persistait avant, la parade fait pareil maintenant.
            if stats.get("parade_active"):
                stats["parade_active"] = False

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

        # Si le combat est fini, on skip le bouton et on laisse la boucle sortir
        if stats1["hp"] <= 0 or stats2["hp"] <= 0 or tour >= MAX_TOURS:
            await msg.edit(embed=embed_result, view=None)
            await asyncio.sleep(1)
            tour += 1
            continue

        # Bouton "Tour suivant" — l'un ou l'autre duelliste clique pour avancer
        embed_result.set_footer(text="📖 Lisez le récap puis cliquez sur Tour suivant pour continuer.")
        event_next = asyncio.Event()
        view_next  = NextTurnView(joueur1, joueur2, event_next, tour)
        await msg.edit(embed=embed_result, view=view_next)
        try:
            await asyncio.wait_for(event_next.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass  # avance auto après 2 min d'inactivité
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
        # Battle Pass : +1 play_duels pour les deux joueurs
        try:
            from bot import _track_pass_quest as _pq
            _pq(joueur1.id, "play_duels", 1)
            _pq(joueur2.id, "play_duels", 1)
        except Exception:
            pass

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
    @app_commands.describe(
        adversaire="Le joueur que tu veux défier",
        nerf="Mode équilibré : ignore les niveaux et stats attribuées (sabres et spéciales conservés)",
    )
    async def duel(interaction: discord.Interaction, adversaire: discord.Member, nerf: bool = False):
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
        nerf_note = "\n\n⚖️ **MODE NERF** — niveaux et stats attribuées ignorés !" if nerf else ""
        embed = discord.Embed(
            title="⚔️ DÉFI EN DUEL !" + (" 〔MODE NERF〕" if nerf else ""),
            description=(
                f"{interaction.user.mention} défie {adversaire.mention} en duel !\n\n"
                f"{interaction.user.display_name} : {sabre1['emoji']} **{sabre1['nom']}** "
                f"({RARETES[sabre1['rarete']]['emoji']} {RARETES[sabre1['rarete']]['label']})\n"
                f"{adversaire.display_name} : {sabre2['emoji']} **{sabre2['nom']}** "
                f"({RARETES[sabre2['rarete']]['emoji']} {RARETES[sabre2['rarete']]['label']})"
                + nerf_note
            ),
            color=0x00AAFF if nerf else 0xFF0000,
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
                nerf=nerf,
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

    @bot.tree.command(name="statpoint", description="Attribuer un point de stat gagne en montant de niveau de combat")
    @app_commands.describe(stat="La statistique a ameliorer")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Force (+5 attaque par point)",     value="force"),
        app_commands.Choice(name="Agilite (+4% esquive par point)",  value="agilite"),
        app_commands.Choice(name="Defense (+3 defense par point)",   value="defense"),
        app_commands.Choice(name="Endurance (+25 HP max par point)", value="endurance"),
        app_commands.Choice(name="Chance (+5% critique par point)",  value="chance"),
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

    # =====================================================================
    # /sabre — menu unifie : equipe / collection / boutique avec navigation
    # =====================================================================
    RARETE_ORDER = ["C", "UC", "R", "SR", "SSR"]

    def _can_equip_seasonal(profil_data, sabre):
        """Anti-P2W : un sabre saisonnier (id 'season_*') ne peut etre equipe
        que si l'user possede au moins un sabre NON-saisonnier de la meme rarete.

        Retourne (True, None) si OK, sinon (False, message d'erreur).
        """
        sid = sabre.get("id", "")
        if not sid.startswith("season_"):
            return True, None
        rarete = sabre.get("rarete")
        inventaire = profil_data.get("sabres", ["bleu"])
        for owned_id in inventaire:
            if owned_id.startswith("season_"):
                continue
            owned = get_sabre(owned_id)
            if owned and owned.get("rarete") == rarete:
                return True, None
        rar_label = RARETES.get(rarete, {}).get("label", rarete)
        return False, (
            f"🔒 Tu dois d'abord posséder un sabre **{rar_label}** classique "
            f"(via la boutique) pour pouvoir équiper ce skin saisonnier."
        )

    def _build_equipped_embed(profil_data):
        sabre_id   = profil_data.get("sabre_equipe", "bleu")
        sabre      = get_sabre(sabre_id)
        rarete     = RARETES[sabre["rarete"]]
        inventaire = profil_data.get("sabres", ["bleu"])
        coins      = profil_data.get("tookcoins", 0)
        embed = discord.Embed(
            title=f"{sabre['emoji']} {sabre['nom']}",
            description=sabre["description"],
            color=0x00BFFF,
        )
        embed.add_field(name="Rareté", value=f"{rarete['emoji']} {rarete['label']}", inline=True)
        embed.add_field(name="TookCoins", value=f"🪙 {coins}", inline=True)
        embed.add_field(name="Sabres possédés", value=f"{len(inventaire)} sabre(s)", inline=True)
        embed.add_field(
            name="✨ Capacité spéciale",
            value=f"**{sabre['speciale']['nom']}**\n_{sabre['speciale']['description']}_",
            inline=False,
        )
        embed.set_footer(text="🗡️ Sabre équipé · utilise les boutons pour ouvrir Collection ou Boutique")
        return embed

    def _build_collection_embed(profil_data, member_name):
        sabres_ids   = profil_data.get("sabres", ["bleu"])
        sabre_equipe = profil_data.get("sabre_equipe", "bleu")

        per_rarete: dict[str, list] = {r: [] for r in RARETE_ORDER}
        for sid in sabres_ids:
            s = get_sabre(sid)
            if not s:
                continue
            if s["rarete"] in per_rarete:
                per_rarete[s["rarete"]].append(s)
            else:
                per_rarete.setdefault(s["rarete"], []).append(s)

        equipped = get_sabre(sabre_equipe) if sabre_equipe else None
        parts = []

        # Bloc "Sabre equipe" en haut
        if equipped:
            equipped_rar = RARETES.get(equipped["rarete"], {})
            parts.append(
                f"⚔️  **Sabre équipé :**  {equipped['emoji']}  **{equipped['nom']}**  "
                f"·  {equipped_rar.get('emoji','')} {equipped_rar.get('label','')}"
            )
        else:
            parts.append("⚔️  **Sabre équipé :**  _aucun_")
        parts.append(
            f"🪙  **{profil_data.get('tookcoins', 0)}** TookCoins  ·  "
            f"📦  **{len(sabres_ids)}** sabre(s) au total"
        )

        for rarete_id in RARETE_ORDER:
            sabres_rar = per_rarete.get(rarete_id) or []
            if not sabres_rar:
                continue
            rarete_info = RARETES[rarete_id]
            sabres_rar.sort(key=lambda x: (x["id"] != sabre_equipe, x["nom"].lower()))

            parts.append("")
            parts.append(SEPARATOR)
            parts.append(
                f"{rarete_info['emoji']}  ·  **{rarete_info['label']}**  ·  {len(sabres_rar)} sabre(s)"
            )
            parts.append(SEPARATOR)
            parts.append("")

            for s in sabres_rar:
                is_equipped = (s["id"] == sabre_equipe)
                can, _ = _can_equip_seasonal(profil_data, s)
                if is_equipped:
                    status = "🟢 **ÉQUIPÉ**"
                elif not can:
                    status = "🔒 *Verrouillé*"
                else:
                    status = "▫️ *Possédé*"

                special = s.get("speciale") or {}
                spec_emoji = special.get("emoji", "✨")
                spec_nom   = special.get("nom", "")

                parts.append(f"{s['emoji']}  **{s['nom']}**  ·  {status}")
                parts.append(f"╰─  {spec_emoji}  _{spec_nom}_")
                parts.append("")

        if len(per_rarete) == 0 or all(not v for v in per_rarete.values()):
            parts.append("")
            parts.append("_Aucun sabre dans ta collection._")

        description = "\n".join(parts)
        if len(description) > 4000:
            description = description[:3990] + "\n…"

        embed = discord.Embed(
            title=f"🗂️  Collection de {member_name}",
            description=description,
            color=0x9B59B6,
        )
        embed.set_footer(
            text="Sélectionne un sabre dans le menu ci-dessous pour l'équiper  ·  🔒 sabre saisonnier verrouillé"
        )
        return embed

    SEPARATOR = "─" * 30

    def _build_shop_embed(profil_data):
        sabres      = get_tous_les_sabres()
        inventaire  = profil_data.get("sabres", ["bleu"])
        coins       = profil_data.get("tookcoins", 0)

        parts = [f"🪙  **Solde :** {coins} TookCoins"]

        for rarete_id in RARETE_ORDER:
            rarete_info = RARETES[rarete_id]
            sabres_rarete = [
                s for s in sabres.values()
                if s["rarete"] == rarete_id and not s["id"].startswith("season_")
            ]
            if not sabres_rarete:
                continue

            # Header de section avec separateurs
            parts.append("")
            parts.append(SEPARATOR)
            parts.append(f"{rarete_info['emoji']}  ·  **{rarete_info['label']}**")
            parts.append(SEPARATOR)
            parts.append("")

            # Sabres
            for s in sabres_rarete:
                possede = s["id"] in inventaire
                if s["prix"] == 0:
                    prix_line = "🎁  **GRATUIT**"
                else:
                    affordable = coins >= s["prix"]
                    prix_line = f"💰  **{s['prix']}** TookCoins" + (
                        "" if affordable else "  ·  *fonds insuffisants*"
                    )
                status = "✅ **DÉJÀ POSSÉDÉ**" if possede else prix_line

                spec = s.get("speciale") or {}
                spec_emoji = spec.get("emoji", "✨")
                spec_nom   = spec.get("nom", "")
                spec_desc  = spec.get("description", "")
                if len(spec_desc) > 70:
                    spec_desc = spec_desc[:67] + "…"

                parts.append(f"{s['emoji']}  **{s['nom']}**  ·  {status}")
                parts.append(f"╰─  {spec_emoji}  *{spec_nom}*  —  {spec_desc}")
                parts.append("")  # blank entre sabres

        # Limite 4096 chars (description discord)
        description = "\n".join(parts)
        if len(description) > 4000:
            description = description[:3990] + "\n…"

        embed = discord.Embed(
            title="🛒  Boutique des sabres laser",
            description=description,
            color=0x00BFFF,
        )
        embed.set_footer(text="Sélectionne un sabre dans le menu ci-dessous pour l'acheter")
        return embed

    class SabreMenuView(discord.ui.View):
        """Vue interactive unifiee : un seul message, 3 modes (equipped / collection / shop)."""

        def __init__(self, user_id, user_name, mode="equipped"):
            super().__init__(timeout=180)
            self.user_id   = user_id
            self.user_name = user_name
            self.mode      = mode
            self._rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "❌ Ce menu n'est pas le tien. Lance `/sabre` pour ouvrir le tien.",
                    ephemeral=True,
                )
                return False
            return True

        def _rebuild(self):
            self.clear_items()
            # Boutons de navigation (row 0)
            if self.mode != "equipped":
                self.add_item(self._make_nav_btn("⚔️ Sabre équipé", "equipped", discord.ButtonStyle.primary))
            if self.mode != "collection":
                self.add_item(self._make_nav_btn("🗂️ Collection", "collection", discord.ButtonStyle.secondary))
            if self.mode != "shop":
                self.add_item(self._make_nav_btn("🛒 Boutique", "shop", discord.ButtonStyle.success))

            # Selects contextuels (row 1+)
            profil_data = db.ensure_profil(self.user_id, self.user_name)
            if self.mode == "collection":
                sel = self._make_equip_select(profil_data)
                if sel:
                    self.add_item(sel)
            elif self.mode == "shop":
                sel = self._make_buy_select(profil_data)
                if sel:
                    self.add_item(sel)

        def _make_nav_btn(self, label, mode, style):
            btn = discord.ui.Button(label=label, style=style, row=0)
            async def cb(interaction: discord.Interaction):
                self.mode = mode
                self._rebuild()
                profil_data = db.ensure_profil(self.user_id, self.user_name)
                if mode == "equipped":
                    embed = _build_equipped_embed(profil_data)
                elif mode == "collection":
                    embed = _build_collection_embed(profil_data, self.user_name)
                else:
                    embed = _build_shop_embed(profil_data)
                await interaction.response.edit_message(embed=embed, view=self)
            btn.callback = cb
            return btn

        def _make_equip_select(self, profil_data):
            inventaire   = profil_data.get("sabres", ["bleu"])
            sabre_equipe = profil_data.get("sabre_equipe", "bleu")
            options = []
            for sid in inventaire:
                s = get_sabre(sid)
                if not s:
                    continue
                if sid == sabre_equipe:
                    continue
                rarete = RARETES[s["rarete"]]
                # Marqueur visuel "verrouille" pour les sabres saisonniers
                # tant que l'user n'a pas le sabre classique de meme rarete.
                can, _ = _can_equip_seasonal(profil_data, s)
                lock = "" if can else "🔒 "
                desc = f"{lock}{rarete['label']} · {s['speciale']['nom']}"
                options.append(discord.SelectOption(
                    label=s["nom"][:100],
                    value=sid,
                    description=desc[:100],
                    emoji=s["emoji"] if s["emoji"] else None,
                ))
            if not options:
                return None
            sel = discord.ui.Select(
                placeholder="🗡️ Équiper un sabre de ta collection...",
                options=options[:25],
                row=1,
            )
            async def cb(interaction: discord.Interaction):
                sid = sel.values[0]
                sabre = get_sabre(sid)
                # Anti-P2W : refuse l'equip d'un sabre saisonnier sans equivalent classique.
                profil_now = db.ensure_profil(self.user_id, self.user_name)
                can, err = _can_equip_seasonal(profil_now, sabre)
                if not can:
                    await interaction.response.send_message(err, ephemeral=True)
                    return
                db.update_profil(self.user_id, {"sabre_equipe": sid})
                self._rebuild()
                profil_data = db.ensure_profil(self.user_id, self.user_name)
                embed = _build_collection_embed(profil_data, self.user_name)
                await interaction.response.edit_message(
                    content=f"✅ {sabre['emoji']} **{sabre['nom']}** équipé !",
                    embed=embed, view=self,
                )
            sel.callback = cb
            return sel

        def _make_buy_select(self, profil_data):
            sabres     = get_tous_les_sabres()
            inventaire = profil_data.get("sabres", ["bleu"])
            options = []
            for s in sorted(sabres.values(),
                            key=lambda x: (RARETE_ORDER.index(x["rarete"]) if x["rarete"] in RARETE_ORDER else 99,
                                           x["prix"])):
                if s["id"] in inventaire:
                    continue
                # Exclut les sabres saisonniers de la boutique
                if s["id"].startswith("season_"):
                    continue
                rarete = RARETES[s["rarete"]]
                prix_txt = "GRATUIT" if s["prix"] == 0 else f"{s['prix']} TookCoins"
                options.append(discord.SelectOption(
                    label=s["nom"][:100],
                    value=s["id"],
                    description=f"{rarete['label']} · {prix_txt}"[:100],
                    emoji=s["emoji"] if s["emoji"] else None,
                ))
            if not options:
                return None
            sel = discord.ui.Select(
                placeholder="💰 Acheter un sabre...",
                options=options[:25],
                row=1,
            )
            async def cb(interaction: discord.Interaction):
                sid = sel.values[0]
                profil_now = db.ensure_profil(self.user_id, self.user_name)
                sabre = get_sabre(sid)
                if not sabre:
                    await interaction.response.send_message("❌ Sabre introuvable.", ephemeral=True)
                    return
                inventaire = profil_now.get("sabres", ["bleu"])
                if sid in inventaire:
                    await interaction.response.send_message(f"❌ Tu possèdes déjà **{sabre['nom']}**.", ephemeral=True)
                    return
                coins = profil_now.get("tookcoins", 0)
                if coins < sabre["prix"]:
                    await interaction.response.send_message(
                        f"❌ Pas assez de TookCoins. Il te faut 🪙 {sabre['prix']} (tu as 🪙 {coins}).",
                        ephemeral=True,
                    )
                    return
                # Achat
                db.add_tookcoins(self.user_id, -sabre["prix"])
                db.update_profil(self.user_id, {"sabres": inventaire + [sid]})
                # Refresh shop view
                self._rebuild()
                profil_data = db.ensure_profil(self.user_id, self.user_name)
                embed = _build_shop_embed(profil_data)
                await interaction.response.edit_message(
                    content=(
                        f"✅ Tu as acheté **{sabre['nom']}** {sabre['emoji']} !\n"
                        f"✨ Spéciale : **{sabre['speciale']['nom']}** — _{sabre['speciale']['description']}_"
                    ),
                    embed=embed, view=self,
                )
            sel.callback = cb
            return sel

    @bot.tree.command(name="sabre", description="Ouvre le menu sabre (équipé, collection, boutique)")
    async def sabre_menu(interaction: discord.Interaction):
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        embed = _build_equipped_embed(profil_data)
        view  = SabreMenuView(interaction.user.id, interaction.user.name, mode="equipped")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)