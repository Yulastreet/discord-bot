# duel_combat.py
import random
from services.i18n import DEFAULT_LOCALE, t
from duel.sabres import get_sabre, RARETES, sabre_speciale_nom

def calculer_stats(profil, sabre_data):
    """Compute a player's combat stats."""
    level   = profil.get("combat_level", 1)   # combat level (separate from the message level)
    rarete  = sabre_data["rarete"]
    bonus   = RARETES[rarete]["bonus_stats"]

    # Stats spent by the player
    force     = profil.get("stat_force",     0)
    agilite   = profil.get("stat_agilite",   0)
    def_stat  = profil.get("stat_defense",   0)
    endurance = profil.get("stat_endurance", 0)
    chance    = profil.get("stat_chance",    0)

    hp_max  = 250 + (level * 10) + (endurance * 25)
    attaque = 15  + (level * 2)  + (bonus * 5) + (force * 5)
    defense = 5   +  level       +  bonus       + (def_stat * 3)

    return {
        "hp":              hp_max,
        "hp_max":          hp_max,
        "attaque":         attaque,
        "defense":         defense,
        "speciale_dispo":  True,
        "effets":          {},
        "parade_active":   False,
        "parade_cooldown": 0,
        "defense_active":  False,
        # Directional special defense (mindgame read)
        "defense_speciale_active":   False,   # True once the zone has been locked this turn
        "defense_speciale_cooldown": 0,       # ticks down every turn of the player
        "defense_zone":              None,    # "bras_g" | "bras_d" | "jambe_g" | "jambe_d"
        "malus_attaque_tours":       0,       # turns left with -20% atk (if the opponent read it)
        # Passives coming from the stats
        "esquive_chance":  min(agilite * 0.04, 0.40),   # 4 % / point, max 40 %
        "crit_chance":     min(chance  * 0.05, 0.50),   # 5 % / point, max 50 %
    }


def appliquer_effet(attaquant_stats, defenseur_stats, effet):
    """Apply a special effect."""
    effets     = defenseur_stats["effets"]
    att_effets = attaquant_stats["effets"]

    if effet == "absorb_next":
        effets["absorb_next"] = True
    elif effet == "lifesteal_50":
        att_effets["lifesteal_50"] = True
    elif effet == "lifesteal_75":
        att_effets["lifesteal_75"] = True
    elif effet == "rage_next":
        att_effets["rage_next"] = True
    elif effet == "double_attaque":
        att_effets["double_attaque"] = True
    elif effet == "overcharge":
        att_effets["overcharge"] = True
    elif effet == "paralyze_next":
        effets["paralyze"] = True
    elif effet == "reflect_100":
        effets["reflect_100"] = True
    elif effet == "ultimate":
        att_effets["ultimate"] = True
    # void_strike + stellar_burst: immediate effects consumed inside
    # calculer_degats right after appliquer_effet, so no flag needed.


def calculer_degats(attaquant_stats, defenseur_stats, utilise_speciale=False, sabre_data=None,
                    locale=DEFAULT_LOCALE):
    """Compute the damage of an attack and return a report."""
    rapport = {"degats": 0, "soin": 0, "messages": [], "double": False}

    esquive = defenseur_stats.get("esquive_chance", 0)
    if esquive > 0 and random.random() < esquive:
        rapport["messages"].append(t("duel.combat.dodge", locale))
        return rapport

    atk    = attaquant_stats["attaque"]
    def_   = defenseur_stats["defense"]
    effets_att = attaquant_stats["effets"]
    effets_def = defenseur_stats["effets"]

    variation   = random.uniform(0.85, 1.15)
    degats_base = max(1, int((atk - def_ + random.randint(5, 15)) * variation))

    if attaquant_stats.get("malus_attaque_tours", 0) > 0:
        degats_base = max(1, int(degats_base * 0.8))
        rapport["messages"].append(t("duel.combat.read_penalty", locale))

    crit = attaquant_stats.get("crit_chance", 0)
    if crit > 0 and random.random() < crit:
        degats_base *= 2
        rapport["messages"].append(t("duel.combat.critical", locale))

    if "buff_degats" in effets_att:
        degats_base = int(degats_base * 1.30)
        effets_att["buff_degats"] -= 1
        if effets_att["buff_degats"] <= 0:
            del effets_att["buff_degats"]
        rapport["messages"].append(t("duel.combat.minigame_buff", locale))

    if utilise_speciale and sabre_data and attaquant_stats["speciale_dispo"]:
        effet = sabre_data["speciale"]["effet"]
        appliquer_effet(attaquant_stats, defenseur_stats, effet)
        attaquant_stats["speciale_dispo"] = False
        rapport["messages"].append(t("duel.combat.special_triggered", locale,
                                     special=sabre_speciale_nom(sabre_data, locale)))

        if effet == "ultimate":
            degats_base  = defenseur_stats["hp"]
            rapport["soin"] = degats_base
            rapport["messages"].append(t("duel.combat.ultimate", locale))
        elif effet == "void_strike":
            # At least 50% of the target max HP, full drain, goes through shields + reflect
            dmg = max(degats_base, int(defenseur_stats["hp_max"] * 0.5))
            degats_base = dmg
            attaquant_stats["hp"] = min(attaquant_stats["hp_max"], attaquant_stats["hp"] + dmg)
            rapport["soin"] = dmg
            rapport["messages"].append(t("duel.combat.void_strike", locale, damage=dmg))
            effets_def.pop("absorb_next", None)
            effets_def.pop("reflect_100", None)
        elif effet == "stellar_burst":
            degats_base = int(degats_base * 2.5)
            rapport["messages"].append(t("duel.combat.stellar_burst", locale))

    if "rage_next" in effets_att:
        degats_base *= 2
        rapport["messages"].append(t("duel.combat.rage", locale))
        del effets_att["rage_next"]

    if "overcharge" in effets_att:
        degats_base = int(degats_base * 1.5)
        rapport["messages"].append(t("duel.combat.overcharge", locale))
        del effets_att["overcharge"]

    if "absorb_next" in effets_def:
        rapport["messages"].append(t("duel.combat.shield_absorb", locale))
        del effets_def["absorb_next"]
        degats_base = 0

    if "reflect_100" in effets_def:
        rapport["messages"].append(t("duel.combat.reflect", locale))
        attaquant_stats["hp"] -= degats_base
        del effets_def["reflect_100"]
        degats_base = 0

    if "lifesteal_50" in effets_att and degats_base > 0:
        soin = int(degats_base * 0.5)
        attaquant_stats["hp"] = min(attaquant_stats["hp_max"], attaquant_stats["hp"] + soin)
        rapport["soin"] = soin
        rapport["messages"].append(t("duel.combat.lifesteal", locale, heal=soin))
        del effets_att["lifesteal_50"]

    if "lifesteal_75" in effets_att and degats_base > 0:
        soin = int(degats_base * 0.75)
        attaquant_stats["hp"] = min(attaquant_stats["hp_max"], attaquant_stats["hp"] + soin)
        rapport["soin"] = soin
        rapport["messages"].append(t("duel.combat.drain", locale, heal=soin))
        del effets_att["lifesteal_75"]

    rapport["degats"] = degats_base
    return rapport


def barre_hp(hp, hp_max):
    """Build a visual health bar."""
    pct    = hp / hp_max
    filled = int(pct * 10)
    bar    = "🟩" * filled + "⬛" * (10 - filled)
    return f"{bar} {hp}/{hp_max} HP"
