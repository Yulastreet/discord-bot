# duel_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from database import get_historique, get_combat_xp_progress, get_xp_pour_prochain_niveau, get_user_cosmetic
from services.i18n import DEFAULT_LOCALE, locale_of, t, ti
from duel.sabres import (get_sabre, get_tous_les_sabres, rarete_label, rarete_emoji,
                         sabre_nom, sabre_description, sabre_speciale_nom,
                         sabre_speciale_description)
from duel.combat import calculer_stats, calculer_degats, barre_hp
from duel.minigames import run_minigame


# Image files uploaded as Discord attachments (referenced through attachment://)
DEFENDER_IMAGE_PATH = "assets/duel_defender.png"
ATTACKER_IMAGE_PATH = "assets/duel_attacker.png"
DEFENDER_IMAGE_NAME = "duel_defender.png"
ATTACKER_IMAGE_NAME = "duel_attacker.png"

# Internal zone codes (custom_id + combat state) -> i18n key suffix.
# The codes themselves are never translated.
ZONE_KEYS = {
    "bras_g":   "left_arm",
    "bras_d":   "right_arm",
    "jambe_g":  "left_leg",
    "jambe_d":  "right_leg",
}


def zone_label(code, locale=DEFAULT_LOCALE):
    """Translated label of a body zone."""
    return t(f"duel.zones.{ZONE_KEYS.get(code, 'left_arm')}", locale)


class ZoneSelectView(discord.ui.View):
    """View attached to the main duel message to pick a zone."""
    def __init__(self, joueur_id: int, event: asyncio.Event, choix_ref: dict, mode: str = "defense",
                 locale: str = DEFAULT_LOCALE):
        super().__init__(timeout=30)
        self.joueur_id = joueur_id
        self.event     = event
        self.choix_ref = choix_ref
        self.locale    = locale

        for code in ZONE_KEYS:
            row = 0 if code.startswith("bras") else 1
            style = (discord.ButtonStyle.primary if mode == "defense"
                     else discord.ButtonStyle.danger)
            btn = discord.ui.Button(label=zone_label(code, locale), style=style, row=row,
                                    custom_id=f"zone_{mode}_{joueur_id}_{code}")

            async def cb(interaction: discord.Interaction, c=code):
                # Only the player concerned may click
                if interaction.user.id != self.joueur_id:
                    try:
                        await interaction.response.send_message(
                            ti(interaction, "duel.fight.not_your_turn"), ephemeral=True,
                        )
                    except Exception:
                        pass
                    return
                if self.choix_ref.get("zone"):
                    try:
                        await interaction.response.defer()
                    except Exception:
                        pass
                    return
                self.choix_ref["zone"] = c
                # Private confirmation reveals the zone to the clicker only
                try:
                    await interaction.response.send_message(
                        ti(interaction, "duel.zones.locked_confirm",
                           zone=zone_label(c, self.locale)), ephemeral=True,
                    )
                except Exception:
                    try:
                        await interaction.response.defer()
                    except Exception:
                        pass
                self.event.set()

            btn.callback = cb
            self.add_item(btn)


def make_zone_embed(mode: str, joueur, locale=DEFAULT_LOCALE) -> discord.Embed:
    """Zone menu embed, image referenced through attachment://."""
    if mode == "defense":
        title  = t("duel.zones.defense_title", locale)
        prompt = t("duel.zones.defense_prompt", locale, player=joueur.display_name)
        img    = DEFENDER_IMAGE_NAME
        color  = 0x4FB3FF
    else:
        title  = t("duel.zones.attack_title", locale)
        prompt = t("duel.zones.attack_prompt", locale, player=joueur.display_name)
        img    = ATTACKER_IMAGE_NAME
        color  = 0xFF4444

    embed = discord.Embed(
        title=title,
        description=prompt + "\n\n" + t("duel.zones.timeout_note", locale),
        color=color,
    )
    embed.set_image(url=f"attachment://{img}")
    return embed


def _zone_attachment(mode: str) -> "discord.File | None":
    """Return the discord.File to attach for the mode image."""
    import os as _os
    path = DEFENDER_IMAGE_PATH if mode == "defense" else ATTACKER_IMAGE_PATH
    name = DEFENDER_IMAGE_NAME if mode == "defense" else ATTACKER_IMAGE_NAME
    if _os.path.exists(path):
        return discord.File(path, filename=name)
    return None


async def _resolve_defense_zone_on_msg(msg, joueur, stats, locale=DEFAULT_LOCALE):
    """Show the defense zone menu on the main msg, wait for the pick (or timeout).
    Set defense_zone, defense_speciale_active, defense_speciale_cooldown.
    Reset pending_def_zone."""
    stats["pending_def_zone"] = False
    zone_event = asyncio.Event()
    zone_ref   = {}
    zone_view  = ZoneSelectView(joueur.id, zone_event, zone_ref, mode="defense", locale=locale)
    embed = make_zone_embed("defense", joueur, locale)
    file_ = _zone_attachment("defense")
    try:
        if file_:
            await msg.edit(embed=embed, view=zone_view, attachments=[file_])
        else:
            await msg.edit(embed=embed, view=zone_view, attachments=[])
    except Exception:
        # Edit failed -> fall back on the standard defense
        stats["defense_active"] = True
        return
    try:
        await asyncio.wait_for(zone_event.wait(), timeout=30)
    except asyncio.TimeoutError:
        zone_ref["zone"] = "bras_g"
    stats["defense_zone"]              = zone_ref["zone"]
    stats["defense_speciale_active"]   = True
    stats["defense_speciale_cooldown"] = 4
    # Update msg with a neutral "locked" visual while waiting for the rest
    locked_embed = discord.Embed(
        title=t("duel.zones.defense_locked_title", locale),
        description=t("duel.zones.defense_locked_desc", locale, player=joueur.display_name),
        color=0x4FB3FF,
    )
    try:
        await msg.edit(embed=locked_embed, view=None, attachments=[])
    except Exception:
        pass
    await asyncio.sleep(1)


async def _resolve_attack_zone_on_msg(msg, joueur, stats, locale=DEFAULT_LOCALE):
    """Show the attack zone menu on the main msg, wait for the pick (or timeout).
    Set attaque_zone."""
    zone_event = asyncio.Event()
    zone_ref   = {}
    zone_view  = ZoneSelectView(joueur.id, zone_event, zone_ref, mode="attaque", locale=locale)
    embed = make_zone_embed("attaque", joueur, locale)
    file_ = _zone_attachment("attaque")
    try:
        if file_:
            await msg.edit(embed=embed, view=zone_view, attachments=[file_])
        else:
            await msg.edit(embed=embed, view=zone_view, attachments=[])
    except Exception:
        stats["attaque_zone"] = "bras_g"
        return
    try:
        await asyncio.wait_for(zone_event.wait(), timeout=30)
    except asyncio.TimeoutError:
        zone_ref["zone"] = "bras_g"
    stats["attaque_zone"] = zone_ref["zone"]
    # Neutral interstitial message
    locked_embed = discord.Embed(
        title=t("duel.zones.attack_locked_title", locale),
        description=t("duel.zones.attack_locked_desc", locale, player=joueur.display_name),
        color=0xFF4444,
    )
    try:
        await msg.edit(embed=locked_embed, view=None, attachments=[])
    except Exception:
        pass
    await asyncio.sleep(1)


def make_tour_embed(tour, j1, s1, sb1, j2, s2, sb2, phase=1,
                    phase_actif=None, phase_attente=None, locale=DEFAULT_LOCALE):
    """phase_actif / phase_attente tune the footer depending on who plays first
    this turn (the initiative changes with the dice roll)."""
    actif   = phase_actif   or j1
    attente = phase_attente or j2
    if phase == 1:
        desc   = t("duel.turn.blind_pick", locale)
        footer = t("duel.turn.footer_phase1", locale,
                   active=actif.display_name, waiting=attente.display_name)
    elif phase == 2:
        desc   = t("duel.turn.blind_pick", locale)
        footer = t("duel.turn.footer_phase2", locale,
                   active=actif.display_name, waiting=attente.display_name)
    else:
        desc   = t("duel.turn.resolving", locale)
        footer = None

    embed = discord.Embed(
        title=t("duel.turn.title", locale, turn=tour),
        description=desc,
        color=0xFFD700,
    )

    # Player 1
    parade1 = (t("duel.turn.parry_ready", locale) if s1["parade_cooldown"] == 0
               else t("duel.turn.parry_cooldown", locale, turns=s1["parade_cooldown"]))
    spec1   = (t("duel.turn.special_ready", locale,
                 special=sabre_speciale_nom(sb1, locale),
                 description=sabre_speciale_description(sb1, locale))
               if s1["speciale_dispo"] else t("duel.turn.special_spent", locale))
    embed.add_field(
        name=f"❤️ {j1.display_name}",
        value=f"{barre_hp(s1['hp'], s1['hp_max'])}\n{parade1}\n{spec1}",
        inline=False,
    )

    # Player 2
    parade2 = (t("duel.turn.parry_ready", locale) if s2["parade_cooldown"] == 0
               else t("duel.turn.parry_cooldown", locale, turns=s2["parade_cooldown"]))
    spec2   = (t("duel.turn.special_ready", locale,
                 special=sabre_speciale_nom(sb2, locale),
                 description=sabre_speciale_description(sb2, locale))
               if s2["speciale_dispo"] else t("duel.turn.special_spent", locale))
    embed.add_field(
        name=f"❤️ {j2.display_name}",
        value=f"{barre_hp(s2['hp'], s2['hp_max'])}\n{parade2}\n{spec2}",
        inline=False,
    )

    if footer:
        embed.set_footer(text=footer)
    return embed


class TourView(discord.ui.View):
    # (action id, button style, row). The action id feeds the custom_id and the
    # combat logic, the label comes from duel.actions.<action id>.
    ACTIONS = [
        ("attaque",  discord.ButtonStyle.danger,    0),
        ("parade",   discord.ButtonStyle.secondary, 0),
        ("defense",  discord.ButtonStyle.primary,   0),
        ("coup_bas", discord.ButtonStyle.secondary, 1),
        ("speciale", discord.ButtonStyle.success,   1),
    ]

    def __init__(self, joueur_actif, stats_actif, event_choix, choix_state, tour,
                 choix_interactions=None, locale=DEFAULT_LOCALE):
        super().__init__(timeout=35)
        self.joueur_actif        = joueur_actif
        self.stats_actif         = stats_actif
        self.event_choix         = event_choix
        self.choix_state         = choix_state
        self.choix_interactions  = choix_interactions if choix_interactions is not None else {}
        self.locale              = locale

        for action, style, row in self.ACTIONS:
            disabled = False
            display  = t(f"duel.actions.{action}", locale)
            if action == "parade" and stats_actif["parade_cooldown"] > 0:
                disabled = True
                display  = t("duel.actions.parade_cooldown", locale,
                             turns=stats_actif["parade_cooldown"])
            elif action == "speciale" and not stats_actif["speciale_dispo"]:
                disabled = True
                display  = t("duel.actions.speciale_used", locale)
            elif action == "defense" and stats_actif.get("defense_speciale_cooldown", 0) == 0:
                # Special defense is ready: say so on the label
                display = t("duel.actions.defense_special", locale)
                style   = discord.ButtonStyle.success
            elif action == "defense" and stats_actif.get("defense_speciale_cooldown", 0) > 0:
                display = t("duel.actions.defense_countdown", locale,
                            turns=stats_actif["defense_speciale_cooldown"])

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

                # Defense + special available: flag pending_def_zone, the combat loop
                # will handle the zone menu on the main msg after the phase.
                if a == "defense" and self.stats_actif.get("defense_speciale_cooldown", 0) == 0:
                    self.stats_actif["pending_def_zone"]      = True
                    self.choix_state[self.joueur_actif.id]    = "defense"
                    self.choix_interactions[self.joueur_actif.id] = interaction
                    for child in self.children:
                        child.disabled = True
                    try:
                        await interaction.response.defer()
                    except Exception:
                        pass
                    self.event_choix.set()
                    return

                self.choix_state[self.joueur_actif.id] = a
                # Keep the interaction ref around so we can followup-ephemeral later
                # (attack zone when the opponent went for a special defense).
                self.choix_interactions[self.joueur_actif.id] = interaction
                for child in self.children:
                    child.disabled = True
                await interaction.response.defer()
                self.event_choix.set()

            btn.callback = callback
            self.add_item(btn)


class NextTurnView(discord.ui.View):
    """'Next turn' button between two turns, clickable by both duelists."""

    def __init__(self, joueur1, joueur2, event_next, tour, label=None,
                 locale=DEFAULT_LOCALE):
        super().__init__(timeout=120)
        self.joueur1   = joueur1
        self.joueur2   = joueur2
        self.event     = event_next
        self.locale    = locale

        btn = discord.ui.Button(
            label=label or t("duel.turn.next_button", locale),
            style=discord.ButtonStyle.primary,
            custom_id=f"next_tour_{tour}",
        )
        async def cb(interaction: discord.Interaction):
            if interaction.user.id not in (self.joueur1.id, self.joueur2.id):
                try:
                    await interaction.response.send_message(
                        ti(interaction, "duel.turn.next_not_duelist"), ephemeral=True)
                except Exception:
                    pass
                return
            for child in self.children:
                child.disabled = True
                child.label    = t("duel.turn.next_clicked", self.locale,
                                   player=interaction.user.display_name)
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
            self.event.set()
        btn.callback = cb
        self.add_item(btn)


class HistoriqueView(discord.ui.View):
    """'Fight log' button shown once the duel is over."""

    def __init__(self, historique: list, locale=DEFAULT_LOCALE):
        super().__init__(timeout=120)
        self.historique = historique
        self.locale     = locale

        btn = discord.ui.Button(label=t("duel.log.button", locale),
                                style=discord.ButtonStyle.secondary)

        async def cb(interaction: discord.Interaction):
            if not self.historique:
                await interaction.response.send_message(
                    ti(interaction, "duel.log.empty"), ephemeral=True)
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
            embed = discord.Embed(title=ti(interaction, "duel.log.title"),
                                  description=pages[0], color=0x888888)
            if len(pages) > 1:
                embed.set_footer(text=ti(interaction, "duel.log.page_footer", total=len(pages)))
            await interaction.response.send_message(embed=embed, ephemeral=True)

        btn.callback = cb
        self.add_item(btn)


class DuelInviteView(discord.ui.View):
    def __init__(self, challenger_id, challenged_id, locale=DEFAULT_LOCALE):
        super().__init__(timeout=30)
        self.challenger_id     = challenger_id
        self.challenged_id     = challenged_id
        self.accepted          = None
        self.accept_interaction = None
        self.locale            = locale

        accept = discord.ui.Button(label=t("duel.invite.accept", locale),
                                   style=discord.ButtonStyle.success)
        async def on_accept(interaction: discord.Interaction):
            self.accepted           = True
            self.accept_interaction = interaction
            self.stop()
            await interaction.response.defer(ephemeral=True)
        accept.callback = on_accept
        self.add_item(accept)

        decline = discord.ui.Button(label=t("duel.invite.decline", locale),
                                    style=discord.ButtonStyle.danger)
        async def on_decline(interaction: discord.Interaction):
            self.accepted = False
            self.stop()
            await interaction.response.defer()
        decline.callback = on_decline
        self.add_item(decline)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message(
                ti(interaction, "duel.invite.not_for_you"), ephemeral=True)
            return False
        return True


def resoudre_tour(attaquant, att_stats, att_sabre, defenseur, def_stats, def_sabre, action,
                  locale=DEFAULT_LOCALE):
    desc = ""
    if action == "attaque":
        if def_stats["parade_active"]:
            rapport = calculer_degats(att_stats, def_stats, locale=locale)
            # rapport["degats"] already carries every bonus (mini-game +30%, rage, overcharge, crit...)
            # so 100% of the damage (bonuses included) goes back to the attacker
            for m in rapport["messages"]:
                desc += f"{m}\n"
            att_stats["hp"] = max(0, att_stats["hp"] - rapport["degats"])
            def_stats["parade_active"] = False
            desc += t("duel.resolve.parry_reflect", locale,
                      defender=defenseur.display_name, damage=rapport["degats"],
                      attacker=attaquant.display_name) + "\n"
        elif def_stats["defense_active"]:
            rapport = calculer_degats(att_stats, def_stats, locale=locale)
            dmg     = max(1, int(rapport["degats"] * 0.4))
            for m in rapport["messages"]:
                desc += f"{m}\n"
            def_stats["hp"]            = max(0, def_stats["hp"] - dmg)
            def_stats["defense_active"] = False
            desc += t("duel.resolve.defense_reduced", locale,
                      defender=defenseur.display_name, damage=dmg) + "\n"
        else:
            rapport = calculer_degats(att_stats, def_stats, locale=locale)
            def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])
            for m in rapport["messages"]:
                desc += f"{m}\n"
            if rapport["degats"] > 0:
                desc += t("duel.resolve.attack_hit", locale,
                          attacker=attaquant.display_name, damage=rapport["degats"]) + "\n"

    elif action == "coup_bas":
        if def_stats["parade_active"]:
            rapport = calculer_degats(att_stats, def_stats, locale=locale)
            dmg     = rapport["degats"] * 2
            for m in rapport["messages"]:
                desc += f"{m}\n"
            def_stats["hp"]            = max(0, def_stats["hp"] - dmg)
            def_stats["parade_active"] = False
            desc += t("duel.resolve.low_blow_break", locale,
                      attacker=attaquant.display_name, damage=dmg) + "\n"
        else:
            rapport = calculer_degats(att_stats, def_stats, locale=locale)
            dmg     = max(1, rapport["degats"] // 2)
            for m in rapport["messages"]:
                desc += f"{m}\n"
            def_stats["hp"] = max(0, def_stats["hp"] - dmg)
            desc += t("duel.resolve.low_blow_hit", locale,
                      attacker=attaquant.display_name, damage=dmg) + "\n"

    elif action == "speciale" and att_stats["speciale_dispo"]:
        rapport = calculer_degats(att_stats, def_stats, utilise_speciale=True,
                                  sabre_data=att_sabre, locale=locale)
        for m in rapport["messages"]:
            desc += f"{m}\n"
        if def_stats["parade_active"]:
            # Parry vs special: only 50% goes back to the attacker, the defender eats the rest
            reflected = rapport["degats"] // 2
            taken     = rapport["degats"] - reflected
            att_stats["hp"] = max(0, att_stats["hp"] - reflected)
            def_stats["hp"] = max(0, def_stats["hp"] - taken)
            def_stats["parade_active"] = False
            desc += t("duel.resolve.parry_vs_special", locale,
                      reflected=reflected, attacker=attaquant.display_name,
                      taken=taken, defender=defenseur.display_name) + "\n"
        else:
            def_stats["hp"] = max(0, def_stats["hp"] - rapport["degats"])
            if rapport["degats"] > 0:
                desc += t("duel.resolve.special_hit", locale, damage=rapport["degats"]) + "\n"

    return desc


async def lancer_combat(challenger_interaction, accept_interaction, joueur1, joueur2, sabre1_id, sabre2_id, db, nerf=False):
    channel = challenger_interaction.channel
    locale  = locale_of(challenger_interaction)

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

    nerf_label = t("duel.fight.nerf_tag", locale) if nerf else ""

    dice_embed = discord.Embed(
        title=t("duel.fight.dice_title", locale),
        description=t("duel.fight.dice_desc", locale,
                      player1=joueur1.display_name, emoji1=sabre1["emoji"],
                      player2=joueur2.display_name, emoji2=sabre2["emoji"]),
        color=0xFFD700,
    )
    # One single main message, edited on every phase (dice -> start -> turns)
    msg = await channel.send(embed=dice_embed)
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
        title=t("duel.fight.initiative_title", locale),
        description=t("duel.fight.initiative_desc", locale,
                      player1=joueur1.display_name, dice1=de1,
                      player2=joueur2.display_name, dice2=de2,
                      first=premier_obj.display_name),
        color=0x00FF88,
    )
    await msg.edit(embed=dice_result)
    await asyncio.sleep(2.5)

    embed_debut = discord.Embed(
        title=t("duel.fight.start_title", locale, nerf=nerf_label),
        description=(
            t("duel.fight.start_desc", locale,
              mention1=joueur1.mention, emoji1=sabre1["emoji"], saber1=sabre_nom(sabre1, locale),
              mention2=joueur2.mention, emoji2=sabre2["emoji"], saber2=sabre_nom(sabre2, locale),
              first=premier_obj.display_name)
            + (t("duel.fight.start_nerf_note", locale) if nerf else "")
        ),
        color=0x00AAFF if nerf else 0xFF0000,
    )
    embed_debut.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_debut.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    await msg.edit(embed=embed_debut)
    await asyncio.sleep(2)

    tour         = 1
    MAX_TOURS    = 20
    historique   = []
    next_minigame = random.randint(5, 8)   # first mini-game between turn 5 and 8

    labels_action = {
        "attaque":  t("duel.actions.attaque", locale),
        "parade":   t("duel.actions.parade", locale),
        "defense":  t("duel.actions.defense", locale),
        "coup_bas": t("duel.actions.coup_bas", locale),
        "speciale": t("duel.actions.speciale", locale),
    }

    while stats1["hp"] > 0 and stats2["hp"] > 0 and tour <= MAX_TOURS:

        if tour == next_minigame:
            mini_desc     = await run_minigame(msg, joueur1, stats1, joueur2, stats2, tour, locale)
            next_minigame = tour + random.randint(5, 8)

            # Resume only if the fight is not over
            if stats1["hp"] <= 0 or stats2["hp"] <= 0:
                break

            resume = discord.Embed(
                title=t("duel.fight.resume_title", locale),
                description=mini_desc,
                color=0xFF4444,
            )
            resume.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
            resume.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
            resume.set_footer(text=t("duel.fight.resume_footer", locale))

            event_next = asyncio.Event()
            view_next  = NextTurnView(joueur1, joueur2, event_next, tour, locale=locale)
            await msg.edit(embed=resume, view=view_next)
            try:
                await asyncio.wait_for(event_next.wait(), timeout=120)
            except asyncio.TimeoutError:
                pass
            tour += 1
            continue

        # --- Phase 1: the winner of the dice roll picks first
        # ordre[0] = who took the initiative (may be joueur1 OR joueur2)
        joueur_p1, stats_p1, _ = ordre[0]
        joueur_p2, stats_p2, _ = ordre[1]

        choix_state        = {}
        choix_interactions = {}
        event_p1    = asyncio.Event()
        view_p1     = TourView(joueur_p1, stats_p1, event_p1, choix_state, tour,
                               choix_interactions=choix_interactions, locale=locale)
        await msg.edit(
            embed=make_tour_embed(
                tour, joueur1, stats1, sabre1, joueur2, stats2, sabre2,
                phase=1, phase_actif=joueur_p1, phase_attente=joueur_p2, locale=locale,
            ),
            view=view_p1,
        )
        try:
            await asyncio.wait_for(event_p1.wait(), timeout=30)
        except asyncio.TimeoutError:
            choix_state[joueur_p1.id] = "attaque"

        event_p2 = asyncio.Event()
        view_p2  = TourView(joueur_p2, stats_p2, event_p2, choix_state, tour,
                            choix_interactions=choix_interactions, locale=locale)
        await msg.edit(
            embed=make_tour_embed(
                tour, joueur1, stats1, sabre1, joueur2, stats2, sabre2,
                phase=2, phase_actif=joueur_p2, phase_attente=joueur_p1, locale=locale,
            ),
            view=view_p2,
        )
        try:
            await asyncio.wait_for(event_p2.wait(), timeout=30)
        except asyncio.TimeoutError:
            choix_state[joueur_p2.id] = "attaque"

        # Both actions are locked in, so revealing "who went for the special
        # defense" no longer gives anyone an edge.
        # Order = initiative (joueur_p1 first if flagged).
        if stats_p1.get("pending_def_zone"):
            await _resolve_defense_zone_on_msg(msg, joueur_p1, stats_p1, locale)
        if stats_p2.get("pending_def_zone"):
            await _resolve_defense_zone_on_msg(msg, joueur_p2, stats_p2, locale)

        choix1 = choix_state.get(joueur1.id, "attaque")
        choix2 = choix_state.get(joueur2.id, "attaque")

        desc_result = t("duel.fight.picks", locale,
                        player1=joueur1.display_name, action1=labels_action[choix1],
                        player2=joueur2.display_name, action2=labels_action[choix2])

        for j, stats, choix in [(joueur1, stats1, choix1), (joueur2, stats2, choix2)]:
            if choix == "parade" and stats["parade_cooldown"] == 0:
                stats["parade_active"]   = True
                stats["parade_cooldown"] = 5
                desc_result += t("duel.fight.parry_up", locale, player=j.display_name) + "\n"
            elif choix == "defense":
                # Either the special defense (flag already set by TourView), or the standard one
                if stats.get("defense_speciale_active"):
                    desc_result += t("duel.fight.special_defense_up", locale,
                                     player=j.display_name) + "\n"
                else:
                    stats["defense_active"] = True
                    desc_result += t("duel.fight.defense_up", locale, player=j.display_name) + "\n"

        desc_result += "\n"

        # Show the zone menu on the main msg for the attacker.
        for (att, att_stats, _), (def_, def_stats, _) in [
            (ordre[0], ordre[1]),
            (ordre[1], ordre[0]),
        ]:
            att_choix = choix_state.get(att.id)
            if att_choix in ("attaque", "coup_bas") and def_stats.get("defense_speciale_active"):
                await _resolve_attack_zone_on_msg(msg, att, att_stats, locale)

        for (att, att_stats, att_sabre), (def_, def_stats, def_sabre) in [
            (ordre[0], ordre[1]),
            (ordre[1], ordre[0]),
        ]:
            if stats1["hp"] <= 0 or stats2["hp"] <= 0:
                break
            att_choix = choix_state[att.id]
            # Special defense active on the defender + normal attack or low blow
            if att_choix in ("attaque", "coup_bas") and def_stats.get("defense_speciale_active"):
                att_zone = att_stats.get("attaque_zone", "bras_g")
                def_zone = def_stats.get("defense_zone", "bras_g")
                if att_zone == def_zone:
                    # MATCH: damage cancelled + -20% atk penalty over 2 active turns
                    att_stats["malus_attaque_tours"] = 3   # +1 because it ticks down at the end of this turn
                    desc_result += t("duel.fight.zone_match", locale,
                                     defender=def_.display_name, attacker=att.display_name,
                                     zone=zone_label(def_zone, locale))
                else:
                    # MISS: apply the standard defense reduction (40% of the damage)
                    def_stats["defense_active"] = True
                    desc_result += t("duel.fight.zone_miss", locale,
                                     defender=def_.display_name,
                                     defense_zone=zone_label(def_zone, locale),
                                     attacker=att.display_name,
                                     attack_zone=zone_label(att_zone, locale))
                    desc_result += resoudre_tour(att, att_stats, att_sabre, def_, def_stats,
                                                 def_sabre, att_choix, locale)
                continue
            if att_choix in ("attaque", "coup_bas", "speciale"):
                desc_result += resoudre_tour(att, att_stats, att_sabre, def_, def_stats,
                                             def_sabre, att_choix, locale)

        # End-of-turn cooldown update
        for j, stats, choix in [(joueur1, stats1, choix1), (joueur2, stats2, choix2)]:
            if choix != "parade" and stats["parade_cooldown"] > 0:
                stats["parade_cooldown"] -= 1
            if choix != "defense":
                stats["defense_active"] = False
            # Parry not consumed by an incoming attack: drop it.
            # Only the defense used to persist, the parry now behaves the same.
            if stats.get("parade_active"):
                stats["parade_active"] = False
            # Special defense: cooldown ticks down every turn, reset flag/zone
            if stats.get("defense_speciale_cooldown", 0) > 0:
                stats["defense_speciale_cooldown"] -= 1
            stats["defense_speciale_active"] = False
            stats["defense_zone"]            = None
            stats["attaque_zone"]            = None
            stats["pending_def_zone"]        = False
            # Attack penalty: ticks down too (down to 0)
            if stats.get("malus_attaque_tours", 0) > 0:
                stats["malus_attaque_tours"] -= 1

        embed_result = discord.Embed(
            title=t("duel.turn.result_title", locale, turn=tour),
            description=desc_result,
            color=0xFF4444,
        )
        embed_result.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
        embed_result.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)

        historique.append(
            t("duel.fight.log_entry", locale, turn=tour, recap=desc_result,
              player1=joueur1.display_name, hp1=stats1["hp"], max1=stats1["hp_max"],
              player2=joueur2.display_name, hp2=stats2["hp"], max2=stats2["hp_max"])
        )

        # If the fight is over we skip the button and let the loop exit
        if stats1["hp"] <= 0 or stats2["hp"] <= 0 or tour >= MAX_TOURS:
            await msg.edit(embed=embed_result, view=None)
            await asyncio.sleep(1)
            tour += 1
            continue

        # "Next turn" button - either duelist can click to move on
        embed_result.set_footer(text=t("duel.turn.result_footer", locale))
        event_next = asyncio.Event()
        view_next  = NextTurnView(joueur1, joueur2, event_next, tour, locale=locale)
        await msg.edit(embed=embed_result, view=view_next)
        try:
            await asyncio.wait_for(event_next.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass  # auto advance after 2 min of inactivity
        tour += 1

    if stats1["hp"] <= 0 and stats2["hp"] <= 0:
        gagnant, perdant = None, None
        desc_fin = t("duel.fight.draw", locale)
    elif stats1["hp"] <= 0:
        gagnant, perdant = joueur2, joueur1
        desc_fin = t("duel.fight.winner", locale, player=joueur2.display_name)
    elif stats2["hp"] <= 0:
        gagnant, perdant = joueur1, joueur2
        desc_fin = t("duel.fight.winner", locale, player=joueur1.display_name)
    else:
        gagnant, perdant = (joueur1, joueur2) if stats1["hp"] >= stats2["hp"] else (joueur2, joueur1)
        desc_fin = t("duel.fight.winner_on_points", locale, player=gagnant.display_name)

    tookcoins_gain = 100
    xp_combat_win  = 150
    xp_combat_lose = 50

    if gagnant:
        db.add_tookcoins(gagnant.id, tookcoins_gain)
        db.add_victoire(gagnant.id)
        db.add_defaite(perdant.id)
        db.sauvegarder(joueur1.id, joueur2.id, gagnant.id, tookcoins_gain, 0)
        desc_fin += t("duel.fight.coins_gain", locale, amount=tookcoins_gain)
        # Battle Pass: +1 play_duels for both players
        try:
            from bot import _track_pass_quest as _pq
            _pq(joueur1.id, "play_duels", 1)
            _pq(joueur2.id, "play_duels", 1)
        except Exception:
            pass

        # Combat XP
        new_lvl_g, lvl_up_g = db.add_combat_xp(gagnant.id, xp_combat_win)
        new_lvl_p, lvl_up_p = db.add_combat_xp(perdant.id, xp_combat_lose)
        desc_fin += t("duel.fight.xp_gain", locale,
                      xp_win=xp_combat_win, winner=gagnant.display_name,
                      xp_lose=xp_combat_lose, loser=perdant.display_name)

        if lvl_up_g:
            desc_fin += t("duel.fight.level_up", locale,
                          player=gagnant.display_name, level=new_lvl_g)
        if lvl_up_p:
            desc_fin += t("duel.fight.level_up", locale,
                          player=perdant.display_name, level=new_lvl_p)

    embed_fin = discord.Embed(title=t("duel.fight.end_title", locale),
                              description=desc_fin, color=0xFFD700)
    embed_fin.add_field(name=f"❤️ {joueur1.display_name}", value=barre_hp(stats1["hp"], stats1["hp_max"]), inline=False)
    embed_fin.add_field(name=f"❤️ {joueur2.display_name}", value=barre_hp(stats2["hp"], stats2["hp_max"]), inline=False)
    await msg.edit(embed=embed_fin, view=HistoriqueView(historique, locale))


def _build_duel_info_embeds(locale=DEFAULT_LOCALE):
    overview = discord.Embed(
        title=t("duel.info.overview_title", locale),
        description=t("duel.info.overview_desc", locale),
        color=0xFFD700,
    )
    overview.add_field(
        name=t("duel.info.philosophy_title", locale),
        value=t("duel.info.philosophy_value", locale),
        inline=False,
    )
    overview.add_field(
        name=t("duel.info.progression_title", locale),
        value=t("duel.info.progression_value", locale),
        inline=False,
    )

    actions = discord.Embed(
        title=t("duel.info.actions_title", locale),
        description=t("duel.info.actions_desc", locale),
        color=0x00AAFF,
    )
    actions.add_field(
        name=t("duel.info.attack_title", locale),
        value=t("duel.info.attack_value", locale),
        inline=False,
    )
    actions.add_field(
        name=t("duel.info.parry_title", locale),
        value=t("duel.info.parry_value", locale),
        inline=False,
    )
    actions.add_field(
        name=t("duel.info.defense_title", locale),
        value=t("duel.info.defense_value", locale),
        inline=False,
    )
    actions.add_field(
        name=t("duel.info.special_defense_title", locale),
        value=t("duel.info.special_defense_value", locale),
        inline=False,
    )
    actions.add_field(
        name=t("duel.info.low_blow_title", locale),
        value=t("duel.info.low_blow_value", locale),
        inline=False,
    )
    actions.add_field(
        name=t("duel.info.special_title", locale),
        value=t("duel.info.special_value", locale),
        inline=False,
    )

    bonuses = discord.Embed(
        title=t("duel.info.bonuses_title", locale),
        description=t("duel.info.bonuses_desc", locale),
        color=0x9B59B6,
    )
    bonuses.add_field(
        name=t("duel.info.rewards_title", locale),
        value=t("duel.info.rewards_value", locale),
        inline=False,
    )
    bonuses.add_field(
        name=t("duel.info.why_luck_title", locale),
        value=t("duel.info.why_luck_value", locale),
        inline=False,
    )
    bonuses.set_footer(text=t("duel.info.footer", locale))

    return [overview, actions, bonuses]


def setup_duel_commands(bot, db):

    duel_group = app_commands.Group(name="duel", description="Lightsaber duel commands")

    @duel_group.command(name="fight", description="Challenge a player to a lightsaber duel")
    @app_commands.describe(
        opponent="The player you want to challenge",
        nerf="Balanced mode: ignore levels and assigned stats (sabers and specials kept)",
    )
    async def duel_fight(interaction: discord.Interaction, opponent: discord.Member, nerf: bool = False):
        locale = locale_of(interaction)
        if opponent.id == interaction.user.id:
            await interaction.response.send_message(t("duel.invite.self", locale), ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message(t("duel.invite.bot", locale), ephemeral=True)
            return

        profil1   = db.ensure_profil(interaction.user.id, interaction.user.name)
        profil2   = db.ensure_profil(opponent.id, opponent.name)
        sabre1_id = profil1.get("sabre_equipe", "bleu")
        sabre2_id = profil2.get("sabre_equipe", "bleu")
        sabre1    = get_sabre(sabre1_id)
        sabre2    = get_sabre(sabre2_id)

        view  = DuelInviteView(interaction.user.id, opponent.id, locale)
        embed = discord.Embed(
            title=t("duel.invite.title", locale) + (t("duel.fight.nerf_tag", locale) if nerf else ""),
            description=(
                t("duel.invite.desc", locale,
                  challenger=interaction.user.mention, opponent=opponent.mention,
                  challenger_name=interaction.user.display_name,
                  emoji1=sabre1["emoji"], saber1=sabre_nom(sabre1, locale),
                  rarity_emoji1=rarete_emoji(sabre1["rarete"]),
                  rarity1=rarete_label(sabre1["rarete"], locale),
                  opponent_name=opponent.display_name,
                  emoji2=sabre2["emoji"], saber2=sabre_nom(sabre2, locale),
                  rarity_emoji2=rarete_emoji(sabre2["rarete"]),
                  rarity2=rarete_label(sabre2["rarete"], locale))
                + (t("duel.invite.nerf_note", locale) if nerf else "")
            ),
            color=0x00AAFF if nerf else 0xFF0000,
        )
        embed.set_footer(text=t("duel.invite.footer", locale, player=opponent.display_name))
        await interaction.response.send_message(embed=embed, view=view)
        await view.wait()

        if view.accepted is None:
            await interaction.followup.send(
                t("duel.invite.timeout", locale, player=opponent.display_name))
        elif not view.accepted:
            await interaction.followup.send(
                t("duel.invite.declined", locale, player=opponent.display_name))
        else:
            await interaction.followup.send(
                t("duel.invite.accepted", locale, player=opponent.display_name))
            await lancer_combat(
                interaction, view.accept_interaction,
                interaction.user, opponent,
                sabre1_id, sabre2_id, db,
                nerf=nerf,
            )

    @duel_group.command(name="info", description="Get the full duel system guide by DM")
    async def duel_info(interaction: discord.Interaction):
        locale = locale_of(interaction)
        try:
            await interaction.user.send(embeds=_build_duel_info_embeds(locale))
        except discord.Forbidden:
            await interaction.response.send_message(
                t("duel.info.dm_forbidden", locale), ephemeral=True)
            return
        except Exception:
            await interaction.response.send_message(
                t("duel.info.dm_failed", locale), ephemeral=True)
            return
        await interaction.response.send_message(t("duel.info.dm_sent", locale), ephemeral=True)

    bot.tree.add_command(duel_group)

    @bot.tree.command(name="profile", description="View your duel profile")
    @app_commands.describe(member="The member whose profile you want to see")
    async def profile(interaction: discord.Interaction, member: discord.Member = None):
        locale      = locale_of(interaction)
        member      = member or interaction.user
        profil_data = db.ensure_profil(member.id, member.name)
        sabre_id    = profil_data.get("sabre_equipe", "bleu")
        sabre       = get_sabre(sabre_id)
        total       = profil_data["victoires"] + profil_data["defaites"]
        ratio       = f"{profil_data['victoires']}/{total}" if total > 0 else "0/0"

        # Combat level + progression
        combat_xp   = profil_data.get("combat_xp", 0)
        clvl, xp_in, xp_needed = get_combat_xp_progress(combat_xp)
        stat_points = profil_data.get("stat_points", 0)

        # Pass cosmetics (emoji prefix + title)
        cosmetic = get_user_cosmetic(member.id)
        emoji_prefix = cosmetic.get("emoji") or ""
        pass_title   = cosmetic.get("title")
        display_name = f"{emoji_prefix} {member.display_name}".strip()

        embed = discord.Embed(title=t("duel.profile.title", locale, name=display_name),
                              color=discord.Color.red())
        embed.set_thumbnail(url=member.display_avatar.url)
        if pass_title:
            embed.description = t("duel.profile.pass_title", locale, title=pass_title)

        # Base stats
        embed.add_field(name=t("duel.profile.tookcoins", locale),
                        value=f"**{profil_data['tookcoins']}** 🪙", inline=True)
        embed.add_field(name=t("duel.profile.wins", locale),
                        value=f"**{profil_data['victoires']}**", inline=True)
        embed.add_field(name=t("duel.profile.losses", locale),
                        value=f"**{profil_data['defaites']}**", inline=True)
        embed.add_field(name=t("duel.profile.ratio", locale), value=ratio, inline=True)
        embed.add_field(
            name=t("duel.profile.equipped_saber", locale),
            value=t("duel.profile.equipped_value", locale,
                    emoji=sabre["emoji"], name=sabre_nom(sabre, locale),
                    rarity_emoji=rarete_emoji(sabre["rarete"]),
                    rarity=rarete_label(sabre["rarete"], locale)),
            inline=True,
        )
        embed.add_field(name=t("duel.profile.collection", locale),
                        value=t("duel.profile.collection_value", locale,
                                count=len(profil_data["sabres"])),
                        inline=True)

        # Combat level
        if xp_needed > 0:
            xp_bar_pct = int((xp_in / xp_needed) * 10)
            xp_bar     = "🟦" * xp_bar_pct + "⬛" * (10 - xp_bar_pct)
            xp_txt     = t("duel.profile.xp_progress", locale,
                           bar=xp_bar, current=xp_in, needed=xp_needed)
        else:
            xp_txt = t("duel.profile.max_level", locale)

        pts_txt = (t("duel.profile.points_to_spend", locale, points=stat_points)
                   if stat_points > 0 else "")
        embed.add_field(
            name=t("duel.profile.combat_level", locale, level=clvl, points=pts_txt),
            value=xp_txt,
            inline=False,
        )

        # Assigned stats
        sf = profil_data.get('stat_force',     0)
        sa = profil_data.get('stat_agilite',   0)
        sd = profil_data.get('stat_defense',   0)
        se = profil_data.get('stat_endurance', 0)
        sc = profil_data.get('stat_chance',    0)
        stats_txt = t("duel.profile.stats_block", locale,
                      strength=sf, attack=sf * 5,
                      agility=sa, dodge=sa * 4,
                      defense=sd, def_bonus=sd * 3,
                      endurance=se, hp=se * 25,
                      luck=sc, crit=sc * 5)
        embed.add_field(name=t("duel.profile.stats_title", locale), value=stats_txt, inline=False)

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="statpoint", description="Spend a stat point earned by gaining combat levels")
    @app_commands.describe(stat="The stat to upgrade")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Strength (+5 attack per point)",   value="force"),
        app_commands.Choice(name="Agility (+4% dodge per point)",    value="agilite"),
        app_commands.Choice(name="Defense (+3 defense per point)",   value="defense"),
        app_commands.Choice(name="Endurance (+25 max HP per point)", value="endurance"),
        app_commands.Choice(name="Luck (+5% crit per point)",        value="chance"),
    ])
    async def statpoint(interaction: discord.Interaction, stat: str):
        locale      = locale_of(interaction)
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        points      = profil_data.get("stat_points", 0)
        if points <= 0:
            await interaction.response.send_message(
                t("duel.statpoint.none_available", locale), ephemeral=True)
            return
        success = db.attribuer_stat(interaction.user.id, stat)
        if not success:
            await interaction.response.send_message(
                t("duel.statpoint.failed", locale), ephemeral=True)
            return

        stat_labels = {
            "force":     t("duel.statpoint.label_force", locale),
            "agilite":   t("duel.statpoint.label_agilite", locale),
            "defense":   t("duel.statpoint.label_defense", locale),
            "endurance": t("duel.statpoint.label_endurance", locale),
            "chance":    t("duel.statpoint.label_chance", locale),
        }
        stat_effects = {
            "force":     t("duel.statpoint.effect_force", locale),
            "agilite":   t("duel.statpoint.effect_agilite", locale),
            "defense":   t("duel.statpoint.effect_defense", locale),
            "endurance": t("duel.statpoint.effect_endurance", locale),
            "chance":    t("duel.statpoint.effect_chance", locale),
        }
        embed = discord.Embed(
            title=t("duel.statpoint.title", locale),
            description=t("duel.statpoint.desc", locale,
                          stat=stat_labels[stat], effect=stat_effects[stat],
                          remaining=points - 1),
            color=0x00FF88,
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="collection", description="View your saber collection")
    @app_commands.describe(member="The member whose collection you want to see")
    async def collection(interaction: discord.Interaction, member: discord.Member = None):
        locale      = locale_of(interaction)
        member      = member or interaction.user
        profil_data = db.ensure_profil(member.id, member.name)
        sabres      = profil_data.get("sabres", ["bleu"])
        sabre_equipe = profil_data.get("sabre_equipe", "bleu")

        embed = discord.Embed(title=t("duel.collection.title", locale, name=member.display_name),
                              color=discord.Color.blue())
        description = ""
        for sabre_id in sabres:
            sabre = get_sabre(sabre_id)
            if not sabre:
                continue
            equipe = (t("duel.collection.equipped_tag", locale)
                      if sabre_id == sabre_equipe else "")
            description += t("duel.collection.line", locale,
                             emoji=sabre["emoji"], name=sabre_nom(sabre, locale),
                             rarity_emoji=rarete_emoji(sabre["rarete"]),
                             rarity=rarete_label(sabre["rarete"], locale),
                             equipped=equipe) + "\n"
        embed.description = description or t("duel.collection.empty", locale)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="history", description="View your latest duels")
    @app_commands.describe(member="The member whose history you want to see")
    async def history_cmd(interaction: discord.Interaction, member: discord.Member = None):
        locale = locale_of(interaction)
        member = member or interaction.user
        duels  = get_historique(member.id, limit=5)
        if not duels:
            await interaction.response.send_message(
                t("duel.history.empty", locale, member=member.display_name), ephemeral=True)
            return
        embed = discord.Embed(title=t("duel.history.title", locale, name=member.display_name),
                              color=discord.Color.blurple())
        description = ""
        for duel in duels:
            gagne       = str(duel["gagnant_id"]) == str(member.id)
            result      = (t("duel.history.win", locale) if gagne
                           else t("duel.history.loss", locale))
            adversaire_id = duel["user_id_2"] if str(duel["user_id_1"]) == str(member.id) else duel["user_id_1"]
            try:
                adversaire     = await interaction.client.fetch_user(int(adversaire_id))
                adversaire_nom = adversaire.name
            except Exception:
                adversaire_nom = t("duel.history.unknown_opponent", locale, id=adversaire_id)
            coins       = duel["tookcoins_gagnant"] if gagne else duel["tookcoins_perdant"]
            description += t("duel.history.line", locale, result=result,
                             opponent=adversaire_nom, coins=coins, date=duel["date"][:10])
        embed.description = description
        await interaction.response.send_message(embed=embed)

    RARETE_ORDER = ["C", "UC", "R", "SR", "SSR"]
    SEPARATOR    = "─" * 30

    def _can_equip_seasonal(profil_data, sabre, locale=DEFAULT_LOCALE):
        """Anti-P2W: a seasonal saber (id 'season_*') can only be equipped once the
        user owns at least one NON-seasonal saber of the same rarity.

        Return (True, None) when it is fine, otherwise (False, error message).
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
        return False, t("duel.saber.seasonal_locked", locale,
                        rarity=rarete_label(rarete, locale))

    def _build_equipped_embed(profil_data, locale=DEFAULT_LOCALE):
        sabre_id   = profil_data.get("sabre_equipe", "bleu")
        sabre      = get_sabre(sabre_id)
        inventaire = profil_data.get("sabres", ["bleu"])
        coins      = profil_data.get("tookcoins", 0)
        embed = discord.Embed(
            title=f"{sabre['emoji']} {sabre_nom(sabre, locale)}",
            description=sabre_description(sabre, locale),
            color=0x00BFFF,
        )
        embed.add_field(name=t("duel.saber.rarity_field", locale),
                        value=f"{rarete_emoji(sabre['rarete'])} {rarete_label(sabre['rarete'], locale)}",
                        inline=True)
        embed.add_field(name=t("duel.saber.tookcoins_field", locale),
                        value=f"🪙 {coins}", inline=True)
        embed.add_field(name=t("duel.saber.owned_field", locale),
                        value=t("duel.saber.owned_value", locale, count=len(inventaire)),
                        inline=True)
        embed.add_field(
            name=t("duel.saber.special_field", locale),
            value=t("duel.saber.special_value", locale,
                    name=sabre_speciale_nom(sabre, locale),
                    description=sabre_speciale_description(sabre, locale)),
            inline=False,
        )
        embed.set_footer(text=t("duel.saber.equipped_footer", locale))
        return embed

    def _build_collection_embed(profil_data, member_name, locale=DEFAULT_LOCALE):
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

        # "Equipped saber" block on top
        if equipped:
            parts.append(t("duel.saber.collection_equipped_line", locale,
                           emoji=equipped["emoji"], name=sabre_nom(equipped, locale),
                           rarity_emoji=rarete_emoji(equipped["rarete"]),
                           rarity=rarete_label(equipped["rarete"], locale)))
        else:
            parts.append(t("duel.saber.collection_equipped_none", locale))
        parts.append(t("duel.saber.collection_totals", locale,
                       coins=profil_data.get("tookcoins", 0), count=len(sabres_ids)))

        for rarete_id in RARETE_ORDER:
            sabres_rar = per_rarete.get(rarete_id) or []
            if not sabres_rar:
                continue
            sabres_rar.sort(key=lambda x: (x["id"] != sabre_equipe,
                                           sabre_nom(x, locale).lower()))

            parts.append("")
            parts.append(SEPARATOR)
            parts.append(t("duel.saber.rarity_header", locale,
                           emoji=rarete_emoji(rarete_id),
                           label=rarete_label(rarete_id, locale),
                           count=len(sabres_rar)))
            parts.append(SEPARATOR)
            parts.append("")

            for s in sabres_rar:
                is_equipped = (s["id"] == sabre_equipe)
                can, _ = _can_equip_seasonal(profil_data, s, locale)
                if is_equipped:
                    status = t("duel.saber.status_equipped", locale)
                elif not can:
                    status = t("duel.saber.status_locked", locale)
                else:
                    status = t("duel.saber.status_owned", locale)

                special = s.get("speciale") or {}
                spec_emoji = special.get("emoji", "✨")
                spec_nom   = sabre_speciale_nom(s, locale)

                parts.append(t("duel.saber.collection_saber_line", locale,
                               emoji=s["emoji"], name=sabre_nom(s, locale), status=status))
                parts.append(t("duel.saber.collection_saber_special", locale,
                               emoji=spec_emoji, special=spec_nom))
                parts.append("")

        if len(per_rarete) == 0 or all(not v for v in per_rarete.values()):
            parts.append("")
            parts.append(t("duel.saber.collection_empty", locale))

        description = "\n".join(parts)
        if len(description) > 4000:
            description = description[:3990] + "\n…"

        embed = discord.Embed(
            title=t("duel.saber.collection_title", locale, name=member_name),
            description=description,
            color=0x9B59B6,
        )
        embed.set_footer(text=t("duel.saber.collection_footer", locale))
        return embed

    def _build_shop_embed(profil_data, locale=DEFAULT_LOCALE):
        sabres      = get_tous_les_sabres()
        inventaire  = profil_data.get("sabres", ["bleu"])
        coins       = profil_data.get("tookcoins", 0)

        parts = [t("duel.saber.shop_balance", locale, coins=coins)]

        for rarete_id in RARETE_ORDER:
            sabres_rarete = [
                s for s in sabres.values()
                if s["rarete"] == rarete_id and not s["id"].startswith("season_")
            ]
            if not sabres_rarete:
                continue

            # Section header with separators
            parts.append("")
            parts.append(SEPARATOR)
            parts.append(t("duel.saber.shop_rarity_header", locale,
                           emoji=rarete_emoji(rarete_id),
                           label=rarete_label(rarete_id, locale)))
            parts.append(SEPARATOR)
            parts.append("")

            # Sabers
            for s in sabres_rarete:
                possede = s["id"] in inventaire
                if s["prix"] == 0:
                    prix_line = t("duel.saber.shop_free", locale)
                else:
                    affordable = coins >= s["prix"]
                    prix_line = t("duel.saber.shop_price", locale, price=s["prix"]) + (
                        "" if affordable else t("duel.saber.shop_not_affordable", locale)
                    )
                status = t("duel.saber.shop_already_owned", locale) if possede else prix_line

                spec = s.get("speciale") or {}
                spec_emoji = spec.get("emoji", "✨")
                spec_nom   = sabre_speciale_nom(s, locale)
                spec_desc  = sabre_speciale_description(s, locale)
                if len(spec_desc) > 70:
                    spec_desc = spec_desc[:67] + "…"

                parts.append(t("duel.saber.shop_saber_line", locale,
                               emoji=s["emoji"], name=sabre_nom(s, locale), status=status))
                parts.append(t("duel.saber.shop_saber_special", locale,
                               emoji=spec_emoji, special=spec_nom, description=spec_desc))
                parts.append("")  # blank line between sabers

        # 4096 char limit (discord description)
        description = "\n".join(parts)
        if len(description) > 4000:
            description = description[:3990] + "\n…"

        embed = discord.Embed(
            title=t("duel.saber.shop_title", locale),
            description=description,
            color=0x00BFFF,
        )
        embed.set_footer(text=t("duel.saber.shop_footer", locale))
        return embed

    class SabreMenuView(discord.ui.View):
        """Unified interactive view: one message, 3 modes (equipped / collection / shop)."""

        def __init__(self, user_id, user_name, mode="equipped", locale=DEFAULT_LOCALE):
            super().__init__(timeout=180)
            self.user_id   = user_id
            self.user_name = user_name
            self.mode      = mode
            self.locale    = locale
            self._rebuild()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    ti(interaction, "duel.saber.menu_not_yours"), ephemeral=True)
                return False
            return True

        def _rebuild(self):
            self.clear_items()
            # Navigation buttons (row 0)
            if self.mode != "equipped":
                self.add_item(self._make_nav_btn(t("duel.saber.nav_equipped", self.locale),
                                                 "equipped", discord.ButtonStyle.primary))
            if self.mode != "collection":
                self.add_item(self._make_nav_btn(t("duel.saber.nav_collection", self.locale),
                                                 "collection", discord.ButtonStyle.secondary))
            if self.mode != "shop":
                self.add_item(self._make_nav_btn(t("duel.saber.nav_shop", self.locale),
                                                 "shop", discord.ButtonStyle.success))

            # Contextual selects (row 1+)
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
                    embed = _build_equipped_embed(profil_data, self.locale)
                elif mode == "collection":
                    embed = _build_collection_embed(profil_data, self.user_name, self.locale)
                else:
                    embed = _build_shop_embed(profil_data, self.locale)
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
                # Visual "locked" marker for seasonal sabers as long as the user
                # does not own the standard saber of the same rarity.
                can, _ = _can_equip_seasonal(profil_data, s, self.locale)
                lock = "" if can else "🔒 "
                desc = t("duel.saber.equip_option_desc", self.locale, lock=lock,
                         rarity=rarete_label(s["rarete"], self.locale),
                         special=sabre_speciale_nom(s, self.locale))
                options.append(discord.SelectOption(
                    label=sabre_nom(s, self.locale)[:100],
                    value=sid,
                    description=desc[:100],
                    emoji=s["emoji"] if s["emoji"] else None,
                ))
            if not options:
                return None
            sel = discord.ui.Select(
                placeholder=t("duel.saber.equip_placeholder", self.locale),
                options=options[:25],
                row=1,
            )
            async def cb(interaction: discord.Interaction):
                sid = sel.values[0]
                sabre = get_sabre(sid)
                # Anti-P2W: refuse equipping a seasonal saber without its standard counterpart.
                profil_now = db.ensure_profil(self.user_id, self.user_name)
                can, err = _can_equip_seasonal(profil_now, sabre, self.locale)
                if not can:
                    await interaction.response.send_message(err, ephemeral=True)
                    return
                db.update_profil(self.user_id, {"sabre_equipe": sid})
                self._rebuild()
                profil_data = db.ensure_profil(self.user_id, self.user_name)
                embed = _build_collection_embed(profil_data, self.user_name, self.locale)
                await interaction.response.edit_message(
                    content=t("duel.saber.equipped_confirm", self.locale,
                              emoji=sabre["emoji"], name=sabre_nom(sabre, self.locale)),
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
                # Seasonal sabers are excluded from the shop
                if s["id"].startswith("season_"):
                    continue
                prix_txt = (t("duel.saber.buy_free", self.locale) if s["prix"] == 0
                            else t("duel.saber.buy_price", self.locale, price=s["prix"]))
                options.append(discord.SelectOption(
                    label=sabre_nom(s, self.locale)[:100],
                    value=s["id"],
                    description=t("duel.saber.buy_option_desc", self.locale,
                                  rarity=rarete_label(s["rarete"], self.locale),
                                  price=prix_txt)[:100],
                    emoji=s["emoji"] if s["emoji"] else None,
                ))
            if not options:
                return None
            sel = discord.ui.Select(
                placeholder=t("duel.saber.buy_placeholder", self.locale),
                options=options[:25],
                row=1,
            )
            async def cb(interaction: discord.Interaction):
                sid = sel.values[0]
                profil_now = db.ensure_profil(self.user_id, self.user_name)
                sabre = get_sabre(sid)
                if not sabre:
                    await interaction.response.send_message(
                        t("duel.saber.not_found", self.locale), ephemeral=True)
                    return
                inventaire = profil_now.get("sabres", ["bleu"])
                if sid in inventaire:
                    await interaction.response.send_message(
                        t("duel.saber.already_owned", self.locale,
                          name=sabre_nom(sabre, self.locale)), ephemeral=True)
                    return
                coins = profil_now.get("tookcoins", 0)
                if coins < sabre["prix"]:
                    await interaction.response.send_message(
                        t("duel.saber.not_enough_coins", self.locale,
                          price=sabre["prix"], coins=coins), ephemeral=True)
                    return
                # Purchase
                db.add_tookcoins(self.user_id, -sabre["prix"])
                db.update_profil(self.user_id, {"sabres": inventaire + [sid]})
                # Refresh shop view
                self._rebuild()
                profil_data = db.ensure_profil(self.user_id, self.user_name)
                embed = _build_shop_embed(profil_data, self.locale)
                await interaction.response.edit_message(
                    content=t("duel.saber.bought", self.locale,
                              name=sabre_nom(sabre, self.locale), emoji=sabre["emoji"],
                              special=sabre_speciale_nom(sabre, self.locale),
                              description=sabre_speciale_description(sabre, self.locale)),
                    embed=embed, view=self,
                )
            sel.callback = cb
            return sel

    @bot.tree.command(name="saber", description="Open the saber menu (equipped, collection, shop)")
    async def saber_menu(interaction: discord.Interaction):
        locale      = locale_of(interaction)
        profil_data = db.ensure_profil(interaction.user.id, interaction.user.name)
        embed = _build_equipped_embed(profil_data, locale)
        view  = SabreMenuView(interaction.user.id, interaction.user.name,
                              mode="equipped", locale=locale)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
