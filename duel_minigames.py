# duel_minigames.py
import discord
import asyncio
import random
from duel_combat import barre_hp


# ─── RÉCOMPENSES ─────────────────────────────────────────────────────────────

def appliquer_recompense(winner_stats, reward_id):
    """Applique la récompense au gagnant. Retourne le texte de la récompense."""
    if reward_id == "hp_80":
        gain = min(80, winner_stats["hp_max"] - winner_stats["hp"])
        winner_stats["hp"] += gain
        return f"🩹 **+{gain} HP** récupérés !"
    elif reward_id == "speciale":
        if not winner_stats["speciale_dispo"]:
            winner_stats["speciale_dispo"] = True
            return "✨ **Spéciale rechargée !**"
        else:
            gain = min(50, winner_stats["hp_max"] - winner_stats["hp"])
            winner_stats["hp"] += gain
            return f"✨ Spéciale déjà dispo → 🩹 **+{gain} HP** à la place !"
    elif reward_id == "buff_degats":
        winner_stats["effets"]["buff_degats"] = 2
        return "⚡ **+30% de dégâts pendant 2 tours !**"
    elif reward_id == "hp_60":
        gain = min(60, winner_stats["hp_max"] - winner_stats["hp"])
        winner_stats["hp"] += gain
        return f"🩹 **+{gain} HP** récupérés !"
    elif reward_id == "parade_reset_hp":
        winner_stats["parade_cooldown"] = 0
        gain = min(40, winner_stats["hp_max"] - winner_stats["hp"])
        winner_stats["hp"] += gain
        return f"🔄 **Parade rechargée** + 🩹 **+{gain} HP** !"
    return "❓ Récompense inconnue."


# ─── 1. PIERRE FEUILLE CISEAUX ───────────────────────────────────────────────

PFC_EMOJIS = {"pierre": "🪨", "feuille": "📄", "ciseaux": "✂️"}
PFC_WINS   = {"pierre": "ciseaux", "feuille": "pierre", "ciseaux": "feuille"}

def _make_pfc_view(joueur_actif, event, choix_dict):
    view = discord.ui.View(timeout=20)
    for nom in PFC_EMOJIS:
        emoji = PFC_EMOJIS[nom]
        btn = discord.ui.Button(
            label=f"{emoji} {nom.capitalize()}",
            style=discord.ButtonStyle.primary,
            custom_id=f"pfc_{joueur_actif.id}_{nom}",
        )
        async def cb(interaction, n=nom):
            if interaction.user.id != joueur_actif.id:
                await interaction.response.defer()
                return
            if joueur_actif.id in choix_dict:
                await interaction.response.defer()
                return
            choix_dict[joueur_actif.id] = n
            for child in view.children:
                child.disabled = True
            await interaction.response.defer()
            event.set()
        btn.callback = cb
        view.add_item(btn)
    return view

async def pfc(msg, j1, s1, j2, s2):
    choix_dict = {}

    base_embed = discord.Embed(
        title="🎮 MINI-JEU : Pierre Feuille Ciseaux !",
        description=(
            "Le combat s'arrête ! Affrontez-vous au PFC.\n"
            "Le gagnant récupère **80 HP** !"
        ),
        color=0x00FF88,
    )
    base_embed.add_field(name=f"❤️ {j1.display_name}", value=barre_hp(s1["hp"], s1["hp_max"]), inline=True)
    base_embed.add_field(name=f"❤️ {j2.display_name}", value=barre_hp(s2["hp"], s2["hp_max"]), inline=True)

    # J1 choisit
    ev1 = asyncio.Event()
    e1  = discord.Embed.from_dict({**base_embed.to_dict()})
    e1.set_footer(text=f"⌛ {j1.display_name} choisit... — 20 secondes !")
    await msg.edit(embed=e1, view=_make_pfc_view(j1, ev1, choix_dict))
    try:
        await asyncio.wait_for(ev1.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j1.id] = random.choice(list(PFC_EMOJIS))

    # J2 choisit
    ev2 = asyncio.Event()
    e2  = discord.Embed.from_dict({**base_embed.to_dict()})
    e2.set_footer(text=f"✅ {j1.display_name} a choisi ! ⌛ {j2.display_name} — 20 secondes !")
    await msg.edit(embed=e2, view=_make_pfc_view(j2, ev2, choix_dict))
    try:
        await asyncio.wait_for(ev2.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j2.id] = random.choice(list(PFC_EMOJIS))

    c1, c2 = choix_dict[j1.id], choix_dict[j2.id]
    e1_label = f"{PFC_EMOJIS[c1]} {c1.capitalize()}"
    e2_label = f"{PFC_EMOJIS[c2]} {c2.capitalize()}"

    if c1 == c2:
        result_txt = "⚖️ **ÉGALITÉ !** Personne ne reçoit de récompense."
    elif PFC_WINS[c1] == c2:
        reward     = appliquer_recompense(s1, "hp_80")
        result_txt = f"🏆 **{j1.display_name} gagne !** {reward}"
    else:
        reward     = appliquer_recompense(s2, "hp_80")
        result_txt = f"🏆 **{j2.display_name} gagne !** {reward}"

    result_embed = discord.Embed(
        title=f"🎮 PFC — {j1.display_name} : {e1_label}  VS  {j2.display_name} : {e2_label}",
        description=result_txt,
        color=0x00FF88,
    )
    await msg.edit(embed=result_embed, view=None)
    await asyncio.sleep(3)
    return result_txt


# ─── 2. DUEL DE RÉFLEXE ──────────────────────────────────────────────────────

async def reflexe(msg, j1, s1, j2, s2):
    """Compte à rebours, puis premier clic gagne."""
    # Countdown
    for i in range(3, 0, -1):
        embed = discord.Embed(
            title="⚡ MINI-JEU : Duel de Réflexe !",
            description=(
                f"Récompense : **Spéciale rechargée** (ou +50 HP si déjà dispo)\n\n"
                f"Préparez-vous... **{i}...**"
            ),
            color=0xFFFF00,
        )
        await msg.edit(embed=embed, view=None)
        await asyncio.sleep(1)

    # Délai aléatoire imprévisible
    await asyncio.sleep(random.uniform(0.3, 2.5))

    first = {"uid": None}
    ev    = asyncio.Event()

    view = discord.ui.View(timeout=5)
    btn  = discord.ui.Button(label="⚡ FRAPPE !", style=discord.ButtonStyle.success, custom_id="reflexe_frappe")

    async def cb(interaction):
        if interaction.user.id not in (j1.id, j2.id):
            await interaction.response.defer()
            return
        if first["uid"] is None:
            first["uid"] = interaction.user.id
            for child in view.children:
                child.disabled = True
            await interaction.response.defer()
            ev.set()
        else:
            await interaction.response.defer()

    btn.callback = cb
    view.add_item(btn)

    go_embed = discord.Embed(
        title="⚡ MAINTENANT !",
        description="🟢 Appuyez sur **FRAPPE !** le plus vite possible !",
        color=0x00FF00,
    )
    await msg.edit(embed=go_embed, view=view)

    try:
        await asyncio.wait_for(ev.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass

    if first["uid"]:
        winner_stats = s1 if first["uid"] == j1.id else s2
        winner_name  = j1.display_name if first["uid"] == j1.id else j2.display_name
        reward       = appliquer_recompense(winner_stats, "speciale")
        result_txt   = f"⚡ **{winner_name}** a frappé le premier ! {reward}"
    else:
        result_txt = "😴 Personne n'a réagi à temps... Aucune récompense."

    result_embed = discord.Embed(title="⚡ Résultat — Duel de Réflexe", description=result_txt, color=0x00FF00)
    await msg.edit(embed=result_embed, view=None)
    await asyncio.sleep(3)
    return result_txt


# ─── 3. DEVINETTE DE NOMBRE ──────────────────────────────────────────────────

def _make_devinette_view(joueur_actif, event, choix_dict):
    view = discord.ui.View(timeout=20)
    for n in range(1, 11):
        btn = discord.ui.Button(
            label=str(n),
            style=discord.ButtonStyle.secondary,
            custom_id=f"dev_{joueur_actif.id}_{n}",
            row=0 if n <= 5 else 1,
        )
        async def cb(interaction, num=n):
            if interaction.user.id != joueur_actif.id:
                await interaction.response.defer()
                return
            if joueur_actif.id in choix_dict:
                await interaction.response.defer()
                return
            choix_dict[joueur_actif.id] = num
            for child in view.children:
                child.disabled = True
            await interaction.response.defer()
            event.set()
        btn.callback = cb
        view.add_item(btn)
    return view

async def devinette(msg, j1, s1, j2, s2):
    """Deviner le nombre secret 1-10. Plus proche gagne +30% dégâts 2 tours."""
    nombre     = random.randint(1, 10)
    choix_dict = {}

    base_desc = "Devinez le **nombre secret entre 1 et 10** !\nLe plus proche gagne **+30% de dégâts pendant 2 tours** !"

    ev1    = asyncio.Event()
    embed1 = discord.Embed(title="🔢 MINI-JEU : La Devinette !", description=base_desc, color=0x9B59B6)
    embed1.set_footer(text=f"⌛ {j1.display_name} choisit... — 20 secondes !")
    await msg.edit(embed=embed1, view=_make_devinette_view(j1, ev1, choix_dict))
    try:
        await asyncio.wait_for(ev1.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j1.id] = random.randint(1, 10)

    ev2    = asyncio.Event()
    embed2 = discord.Embed(title="🔢 MINI-JEU : La Devinette !", description=base_desc, color=0x9B59B6)
    embed2.set_footer(text=f"✅ {j1.display_name} a choisi ! ⌛ {j2.display_name} — 20 secondes !")
    await msg.edit(embed=embed2, view=_make_devinette_view(j2, ev2, choix_dict))
    try:
        await asyncio.wait_for(ev2.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j2.id] = random.randint(1, 10)

    c1, c2 = choix_dict[j1.id], choix_dict[j2.id]
    d1, d2 = abs(c1 - nombre), abs(c2 - nombre)

    result = (
        f"🎯 Nombre secret : **{nombre}**\n"
        f"{j1.display_name} : **{c1}** (écart {d1})  ·  {j2.display_name} : **{c2}** (écart {d2})\n\n"
    )

    if d1 < d2:
        reward     = appliquer_recompense(s1, "buff_degats")
        result    += f"🏆 **{j1.display_name} gagne !** {reward}"
    elif d2 < d1:
        reward     = appliquer_recompense(s2, "buff_degats")
        result    += f"🏆 **{j2.display_name} gagne !** {reward}"
    else:
        result    += "⚖️ **ÉGALITÉ parfaite !** Personne ne reçoit de récompense."

    result_embed = discord.Embed(title="🔢 Résultat — Devinette", description=result, color=0x9B59B6)
    await msg.edit(embed=result_embed, view=None)
    await asyncio.sleep(3)
    return result


# ─── 4. DUEL DE DÉS ──────────────────────────────────────────────────────────

async def duel_des(msg, j1, s1, j2, s2):
    """Les deux joueurs lancent 3d6 simultanément. Meilleur total gagne +60 HP."""
    rolled = {"j1": None, "j2": None}
    ev1    = asyncio.Event()
    ev2    = asyncio.Event()

    # Vue avec deux boutons indépendants (simultané)
    view = discord.ui.View(timeout=25)

    btn1 = discord.ui.Button(
        label=f"🎲 {j1.display_name} — Lancer !",
        style=discord.ButtonStyle.primary,
        custom_id=f"des_{j1.id}",
        row=0,
    )
    async def roll1(interaction):
        if interaction.user.id != j1.id or rolled["j1"] is not None:
            await interaction.response.defer()
            return
        rolled["j1"] = [random.randint(1, 6) for _ in range(3)]
        btn1.disabled = True
        btn1.label    = f"✅ {j1.display_name} : {rolled['j1']} = {sum(rolled['j1'])}"
        await interaction.response.edit_message(view=view)
        ev1.set()
    btn1.callback = roll1
    view.add_item(btn1)

    btn2 = discord.ui.Button(
        label=f"🎲 {j2.display_name} — Lancer !",
        style=discord.ButtonStyle.danger,
        custom_id=f"des_{j2.id}",
        row=1,
    )
    async def roll2(interaction):
        if interaction.user.id != j2.id or rolled["j2"] is not None:
            await interaction.response.defer()
            return
        rolled["j2"] = [random.randint(1, 6) for _ in range(3)]
        btn2.disabled = True
        btn2.label    = f"✅ {j2.display_name} : {rolled['j2']} = {sum(rolled['j2'])}"
        await interaction.response.edit_message(view=view)
        ev2.set()
    btn2.callback = roll2
    view.add_item(btn2)

    embed = discord.Embed(
        title="🎲 MINI-JEU : Duel de Dés !",
        description=(
            "Lancez vos **3 dés** ! Le total le plus élevé gagne **+60 HP** !\n"
            "Vous pouvez lancer en même temps !"
        ),
        color=0xE67E22,
    )
    embed.add_field(name=f"❤️ {j1.display_name}", value=barre_hp(s1["hp"], s1["hp_max"]), inline=True)
    embed.add_field(name=f"❤️ {j2.display_name}", value=barre_hp(s2["hp"], s2["hp_max"]), inline=True)
    await msg.edit(embed=embed, view=view)

    try:
        await asyncio.wait_for(asyncio.gather(ev1.wait(), ev2.wait()), timeout=25)
    except asyncio.TimeoutError:
        if rolled["j1"] is None:
            rolled["j1"] = [random.randint(1, 6) for _ in range(3)]
        if rolled["j2"] is None:
            rolled["j2"] = [random.randint(1, 6) for _ in range(3)]

    t1, t2 = sum(rolled["j1"]), sum(rolled["j2"])
    result = (
        f"🎲 **{j1.display_name}** : `{rolled['j1']}` = **{t1}**\n"
        f"🎲 **{j2.display_name}** : `{rolled['j2']}` = **{t2}**\n\n"
    )

    if t1 > t2:
        reward  = appliquer_recompense(s1, "hp_60")
        result += f"🏆 **{j1.display_name} gagne !** {reward}"
    elif t2 > t1:
        reward  = appliquer_recompense(s2, "hp_60")
        result += f"🏆 **{j2.display_name} gagne !** {reward}"
    else:
        result += "⚖️ **ÉGALITÉ !** Personne ne reçoit de récompense."

    result_embed = discord.Embed(title="🎲 Résultat — Duel de Dés", description=result, color=0xE67E22)
    await msg.edit(embed=result_embed, view=None)
    await asyncio.sleep(3)
    return result


# ─── 5. CALCUL RAPIDE ────────────────────────────────────────────────────────

QUESTIONS = [
    {"q": "Combien font **7 × 8** ?",    "r": 56,  "choices": [56, 48, 63, 42]},
    {"q": "Combien font **12 + 37** ?",   "r": 49,  "choices": [49, 47, 51, 43]},
    {"q": "Combien font **100 − 36** ?",  "r": 64,  "choices": [64, 66, 62, 74]},
    {"q": "Combien font **9 × 9** ?",     "r": 81,  "choices": [81, 72, 84, 63]},
    {"q": "Combien font **144 ÷ 12** ?",  "r": 12,  "choices": [12, 11, 14, 13]},
    {"q": "Combien font **6³** ?",        "r": 216, "choices": [216, 196, 243, 208]},
    {"q": "Combien font **15 × 4** ?",    "r": 60,  "choices": [60, 55, 64, 48]},
    {"q": "Combien font **25 × 3** ?",    "r": 75,  "choices": [75, 70, 80, 65]},
    {"q": "Combien font **200 − 77** ?",  "r": 123, "choices": [123, 127, 113, 133]},
    {"q": "Combien font **8 × 7** ?",     "r": 56,  "choices": [56, 54, 63, 48]},
]

async def calcul_rapide(msg, j1, s1, j2, s2):
    """Question de maths, premier à cliquer la bonne réponse gagne."""
    question = random.choice(QUESTIONS)
    choices  = question["choices"].copy()
    random.shuffle(choices)

    first   = {"uid": None}
    ev      = asyncio.Event()

    view = discord.ui.View(timeout=15)
    for i, choice in enumerate(choices):
        btn = discord.ui.Button(
            label=str(choice),
            style=discord.ButtonStyle.primary,
            custom_id=f"calc_{choice}_{i}",
            row=0 if i < 2 else 1,
        )
        async def cb(interaction, c=choice):
            if interaction.user.id not in (j1.id, j2.id):
                await interaction.response.defer()
                return
            if c == question["r"] and first["uid"] is None:
                first["uid"] = interaction.user.id
                for child in view.children:
                    child.disabled = True
                await interaction.response.defer()
                ev.set()
            else:
                await interaction.response.defer()
        btn.callback = cb
        view.add_item(btn)

    embed = discord.Embed(
        title="🧮 MINI-JEU : Calcul Rapide !",
        description=(
            f"{question['q']}\n\n"
            "Premier à cliquer la bonne réponse gagne :\n"
            "**Parade rechargée + 40 HP** !"
        ),
        color=0x3498DB,
    )
    embed.set_footer(text="⏱️ 15 secondes !")
    await msg.edit(embed=embed, view=view)

    try:
        await asyncio.wait_for(ev.wait(), timeout=15)
    except asyncio.TimeoutError:
        pass

    if first["uid"]:
        winner_stats = s1 if first["uid"] == j1.id else s2
        winner_name  = j1.display_name if first["uid"] == j1.id else j2.display_name
        reward       = appliquer_recompense(winner_stats, "parade_reset_hp")
        result_txt   = f"🧮 Réponse : **{question['r']}**\n🏆 **{winner_name}** a répondu en premier ! {reward}"
    else:
        result_txt = f"🧮 Réponse : **{question['r']}**\n⏰ Temps écoulé ! Personne ne gagne."

    result_embed = discord.Embed(title="🧮 Résultat — Calcul Rapide", description=result_txt, color=0x3498DB)
    await msg.edit(embed=result_embed, view=None)
    await asyncio.sleep(3)
    return result_txt


# ─── POINT D'ENTRÉE ──────────────────────────────────────────────────────────

NOM_JEUX = {
    pfc:           "🪨 Pierre Feuille Ciseaux",
    reflexe:       "⚡ Duel de Réflexe",
    devinette:     "🔢 Devinette de Nombre",
    duel_des:      "🎲 Duel de Dés",
    calcul_rapide: "🧮 Calcul Rapide",
}

async def run_minigame(msg, j1, s1, j2, s2, tour):
    """Choisit et lance un mini-jeu aléatoire. Retourne la description du résultat."""
    jeu = random.choice(list(NOM_JEUX.keys()))

    annonce = discord.Embed(
        title=f"🎮 MINI-JEU au Tour {tour} !",
        description=(
            f"Le combat s'interrompt !\n\n"
            f"**{NOM_JEUX[jeu]}** commence dans un instant..."
        ),
        color=0x00FF88,
    )
    annonce.add_field(name=f"❤️ {j1.display_name}", value=barre_hp(s1["hp"], s1["hp_max"]), inline=True)
    annonce.add_field(name=f"❤️ {j2.display_name}", value=barre_hp(s2["hp"], s2["hp_max"]), inline=True)
    await msg.edit(embed=annonce, view=None)
    await asyncio.sleep(2)

    return await jeu(msg, j1, s1, j2, s2)
