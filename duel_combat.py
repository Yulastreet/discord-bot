# duel_combat.py
import random
from duel_sabres import get_sabre, RARETES

def calculer_stats(profil, sabre_data):
    """Calcule les stats d'un joueur pour le combat."""
    level   = profil.get("combat_level", 1)   # niveau de combat (séparé du niveau message)
    rarete  = sabre_data["rarete"]
    bonus   = RARETES[rarete]["bonus_stats"]

    # Stats attribuées par le joueur
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
        # Passifs issus des stats
        "esquive_chance":  min(agilite * 0.04, 0.40),   # 4 % / point, max 40 %
        "crit_chance":     min(chance  * 0.05, 0.50),   # 5 % / point, max 50 %
    }


def appliquer_effet(attaquant_stats, defenseur_stats, effet):
    """Applique un effet spécial."""
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


def calculer_degats(attaquant_stats, defenseur_stats, utilise_speciale=False, sabre_data=None):
    """Calcule les dégâts d'une attaque et retourne un rapport."""
    rapport = {"degats": 0, "soin": 0, "messages": [], "double": False}

    # ── Esquive ────────────────────────────────────────────────────
    esquive = defenseur_stats.get("esquive_chance", 0)
    if esquive > 0 and random.random() < esquive:
        rapport["messages"].append("💨 Esquive !")
        return rapport

    atk    = attaquant_stats["attaque"]
    def_   = defenseur_stats["defense"]
    effets_att = attaquant_stats["effets"]
    effets_def = defenseur_stats["effets"]

    variation   = random.uniform(0.85, 1.15)
    degats_base = max(1, int((atk - def_ + random.randint(5, 15)) * variation))

    # ── Coup critique ──────────────────────────────────────────────
    crit = attaquant_stats.get("crit_chance", 0)
    if crit > 0 and random.random() < crit:
        degats_base *= 2
        rapport["messages"].append("💥 Coup critique !")

    # ── Buff dégâts mini-jeu (+30 %, 2 tours) ─────────────────────
    if "buff_degats" in effets_att:
        degats_base = int(degats_base * 1.30)
        effets_att["buff_degats"] -= 1
        if effets_att["buff_degats"] <= 0:
            del effets_att["buff_degats"]
        rapport["messages"].append("⚡ +30% dégâts (mini-jeu) !")

    # ── Capacité spéciale ──────────────────────────────────────────
    if utilise_speciale and sabre_data and attaquant_stats["speciale_dispo"]:
        effet = sabre_data["speciale"]["effet"]
        appliquer_effet(attaquant_stats, defenseur_stats, effet)
        attaquant_stats["speciale_dispo"] = False
        rapport["messages"].append(f"✨ **{sabre_data['speciale']['nom']}** activé !")

        if effet == "ultimate":
            degats_base  = defenseur_stats["hp"]
            rapport["soin"] = degats_base
            rapport["messages"].append("👑 Dégâts absolus + soin total !")

    # ── Effets sur l'attaquant ─────────────────────────────────────
    if "rage_next" in effets_att:
        degats_base *= 2
        rapport["messages"].append("😡 Rage active : dégâts doublés !")
        del effets_att["rage_next"]

    if "overcharge" in effets_att:
        degats_base = int(degats_base * 1.5)
        rapport["messages"].append("⚡ Overcharge : +50% de dégâts !")
        del effets_att["overcharge"]

    # ── Effets sur le défenseur ────────────────────────────────────
    if "absorb_next" in effets_def:
        rapport["messages"].append("🛡️ Bouclier absorbé les dégâts !")
        del effets_def["absorb_next"]
        degats_base = 0

    if "reflect_100" in effets_def:
        rapport["messages"].append("🪞 Dégâts réfléchis sur l'attaquant !")
        attaquant_stats["hp"] -= degats_base
        del effets_def["reflect_100"]
        degats_base = 0

    # ── Lifesteal ──────────────────────────────────────────────────
    if "lifesteal_50" in effets_att and degats_base > 0:
        soin = int(degats_base * 0.5)
        attaquant_stats["hp"] = min(attaquant_stats["hp_max"], attaquant_stats["hp"] + soin)
        rapport["soin"] = soin
        rapport["messages"].append(f"⚖️ Lifesteal : +{soin} HP !")
        del effets_att["lifesteal_50"]

    if "lifesteal_75" in effets_att and degats_base > 0:
        soin = int(degats_base * 0.75)
        attaquant_stats["hp"] = min(attaquant_stats["hp_max"], attaquant_stats["hp"] + soin)
        rapport["soin"] = soin
        rapport["messages"].append(f"💗 Drain : +{soin} HP !")
        del effets_att["lifesteal_75"]

    rapport["degats"] = degats_base
    return rapport


def barre_hp(hp, hp_max):
    """Génère une barre de vie visuelle."""
    pct    = hp / hp_max
    filled = int(pct * 10)
    bar    = "🟩" * filled + "⬛" * (10 - filled)
    return f"{bar} {hp}/{hp_max} HP"
