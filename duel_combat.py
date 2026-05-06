# duel_combat.py
import random
from duel_sabres import get_sabre, RARETES

def calculer_stats(profil, sabre_data):
    """Calcule les stats d'un joueur pour le combat."""
    level = profil["level"]
    rarete = sabre_data["rarete"]
    bonus = RARETES[rarete]["bonus_stats"]
    
    hp_max = 100 + (level * 10)
    attaque = 15 + (level * 2) + (bonus * 5)
    defense = 5 + level + bonus
    
    return {
        "hp": hp_max,
        "hp_max": hp_max,
        "attaque": attaque,
        "defense": defense,
        "speciale_dispo": True,
        "effets": {},
        "parade_active": False,
        "parade_cooldown": 0,
        "defense_active": False,
    }

def appliquer_effet(attaquant_stats, defenseur_stats, effet):
    """Applique un effet spécial."""
    effets = defenseur_stats["effets"]
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
    
    atk = attaquant_stats["attaque"]
    def_ = defenseur_stats["defense"]
    effets_att = attaquant_stats["effets"]
    effets_def = defenseur_stats["effets"]
    
    # Variation aléatoire
    variation = random.uniform(0.85, 1.15)
    degats_base = max(1, int((atk - def_ + random.randint(5, 15)) * variation))
    
    if utilise_speciale and sabre_data and attaquant_stats["speciale_dispo"]:
        effet = sabre_data["speciale"]["effet"]
        appliquer_effet(attaquant_stats, defenseur_stats, effet)
        attaquant_stats["speciale_dispo"] = False
        rapport["messages"].append(f"✨ **{sabre_data['speciale']['nom']}** activé !")
        
        if effet == "ultimate":
            degats_base = defenseur_stats["hp"]
            rapport["soin"] = degats_base
            rapport["messages"].append("👑 Dégâts absolus + soin total !")
    
    # Effets actifs sur attaquant
    if "rage_next" in effets_att:
        degats_base *= 2
        rapport["messages"].append("😡 Rage active : dégâts doublés !")
        del effets_att["rage_next"]
    
    if "overcharge" in effets_att:
        degats_base = int(degats_base * 1.5)
        rapport["messages"].append("⚡ Overcharge : +50% de dégâts !")
        del effets_att["overcharge"]
    
    # Effets actifs sur défenseur
    if "absorb_next" in effets_def:
        rapport["messages"].append("🛡️ Bouclier absorbé les dégâts !")
        del effets_def["absorb_next"]
        degats_base = 0
    
    if "reflect_100" in effets_def:
        rapport["messages"].append("🪞 Dégâts réfléchis sur l'attaquant !")
        attaquant_stats["hp"] -= degats_base
        del effets_def["reflect_100"]
        degats_base = 0
    
    # Lifesteal
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
    pct = hp / hp_max
    filled = int(pct * 10)
    bar = "🟩" * filled + "⬛" * (10 - filled)
    return f"{bar} {hp}/{hp_max} HP"