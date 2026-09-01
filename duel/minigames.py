# duel_minigames.py
import discord
import asyncio
import random
from services.i18n import DEFAULT_LOCALE, t
from services.ui_v2 import Panel, row
from duel.combat import barre_hp


def appliquer_recompense(winner_stats, reward_id, locale=DEFAULT_LOCALE):
    """Apply the reward to the winner. Return the reward text."""
    if reward_id == "hp_80":
        gain = min(80, winner_stats["hp_max"] - winner_stats["hp"])
        winner_stats["hp"] += gain
        return t("duel.minigames.reward.hp", locale, amount=gain)
    elif reward_id == "speciale":
        if not winner_stats["speciale_dispo"]:
            winner_stats["speciale_dispo"] = True
            return t("duel.minigames.reward.special_recharged", locale)
        else:
            gain = min(50, winner_stats["hp_max"] - winner_stats["hp"])
            winner_stats["hp"] += gain
            return t("duel.minigames.reward.special_already", locale, amount=gain)
    elif reward_id == "buff_degats":
        winner_stats["effets"]["buff_degats"] = 2
        return t("duel.minigames.reward.damage_buff", locale)
    elif reward_id == "hp_60":
        gain = min(60, winner_stats["hp_max"] - winner_stats["hp"])
        winner_stats["hp"] += gain
        return t("duel.minigames.reward.hp", locale, amount=gain)
    elif reward_id == "parade_reset_hp":
        winner_stats["parade_cooldown"] = 0
        gain = min(40, winner_stats["hp_max"] - winner_stats["hp"])
        winner_stats["hp"] += gain
        return t("duel.minigames.reward.parry_and_hp", locale, amount=gain)
    return t("duel.minigames.reward.unknown", locale)


# Keys stay in their original form: they feed the custom_id and the win table.
PFC_EMOJIS = {"pierre": "🪨", "feuille": "📄", "ciseaux": "✂️"}
PFC_WINS   = {"pierre": "ciseaux", "feuille": "pierre", "ciseaux": "feuille"}


def _pfc_label(nom, locale=DEFAULT_LOCALE):
    """Translated label of a rock/paper/scissors pick."""
    return t(f"duel.minigames.rps.{nom}", locale)


def _make_pfc_view(panel, joueur_actif, event, choix_dict, locale=DEFAULT_LOCALE):
    view = discord.ui.LayoutView(timeout=20)
    view.add_item(panel.container())
    ar      = discord.ui.ActionRow()
    buttons = []
    for nom in PFC_EMOJIS:
        emoji = PFC_EMOJIS[nom]
        btn = discord.ui.Button(
            label=f"{emoji} {_pfc_label(nom, locale)}",
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
            for child in buttons:
                child.disabled = True
            await interaction.response.defer()
            event.set()
        btn.callback = cb
        ar.add_item(btn)
        buttons.append(btn)
    view.add_item(ar)
    return view

async def pfc(msg, j1, s1, j2, s2, locale=DEFAULT_LOCALE):
    choix_dict = {}

    def _base_panel():
        """Fresh copy of the shared header (was a re-hydrated embed dict)."""
        p = Panel(
            t("duel.minigames.rps.title", locale),
            t("duel.minigames.rps.desc", locale),
        )
        p.field(f"❤️ {j1.display_name}", barre_hp(s1["hp"], s1["hp_max"]), inline=True)
        p.field(f"❤️ {j2.display_name}", barre_hp(s2["hp"], s2["hp_max"]), inline=True)
        return p

    # Player 1 picks
    ev1 = asyncio.Event()
    p1  = _base_panel()
    p1.footer(t("duel.minigames.waiting_first", locale, player=j1.display_name))
    await msg.edit(view=_make_pfc_view(p1, j1, ev1, choix_dict, locale))
    try:
        await asyncio.wait_for(ev1.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j1.id] = random.choice(list(PFC_EMOJIS))

    # Player 2 picks
    ev2 = asyncio.Event()
    p2  = _base_panel()
    p2.footer(t("duel.minigames.waiting_second", locale,
                player1=j1.display_name, player2=j2.display_name))
    await msg.edit(view=_make_pfc_view(p2, j2, ev2, choix_dict, locale))
    try:
        await asyncio.wait_for(ev2.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j2.id] = random.choice(list(PFC_EMOJIS))

    c1, c2 = choix_dict[j1.id], choix_dict[j2.id]
    e1_label = f"{PFC_EMOJIS[c1]} {_pfc_label(c1, locale)}"
    e2_label = f"{PFC_EMOJIS[c2]} {_pfc_label(c2, locale)}"

    if c1 == c2:
        result_txt = t("duel.minigames.draw", locale)
    elif PFC_WINS[c1] == c2:
        reward     = appliquer_recompense(s1, "hp_80", locale)
        result_txt = t("duel.minigames.winner", locale, player=j1.display_name, reward=reward)
    else:
        reward     = appliquer_recompense(s2, "hp_80", locale)
        result_txt = t("duel.minigames.winner", locale, player=j2.display_name, reward=reward)

    result_panel = Panel(
        t("duel.minigames.rps.result_title", locale,
          player1=j1.display_name, choice1=e1_label,
          player2=j2.display_name, choice2=e2_label),
        result_txt,
    )
    await msg.edit(view=result_panel.view())
    await asyncio.sleep(3)
    return result_txt


async def reflexe(msg, j1, s1, j2, s2, locale=DEFAULT_LOCALE):
    """Countdown, then the first click wins."""
    # Countdown
    for i in range(3, 0, -1):
        countdown = Panel(
            t("duel.minigames.reflex.title", locale),
            t("duel.minigames.reflex.desc", locale, count=i),
        )
        await msg.edit(view=countdown.view())
        await asyncio.sleep(1)

    # Unpredictable random delay
    await asyncio.sleep(random.uniform(0.3, 2.5))

    first = {"uid": None}
    ev    = asyncio.Event()

    btn  = discord.ui.Button(label=t("duel.minigames.reflex.button", locale),
                             style=discord.ButtonStyle.success, custom_id="reflexe_frappe")

    async def cb(interaction):
        if interaction.user.id not in (j1.id, j2.id):
            await interaction.response.defer()
            return
        if first["uid"] is None:
            first["uid"] = interaction.user.id
            btn.disabled = True
            await interaction.response.defer()
            ev.set()
        else:
            await interaction.response.defer()

    btn.callback = cb

    go_panel = Panel(
        t("duel.minigames.reflex.go_title", locale),
        t("duel.minigames.reflex.go_desc", locale),
    )
    view = go_panel.view(row(btn), timeout=5)
    await msg.edit(view=view)

    try:
        await asyncio.wait_for(ev.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass

    if first["uid"]:
        winner_stats = s1 if first["uid"] == j1.id else s2
        winner_name  = j1.display_name if first["uid"] == j1.id else j2.display_name
        reward       = appliquer_recompense(winner_stats, "speciale", locale)
        result_txt   = t("duel.minigames.reflex.winner", locale, player=winner_name, reward=reward)
    else:
        result_txt = t("duel.minigames.reflex.nobody", locale)

    result_panel = Panel(t("duel.minigames.reflex.result_title", locale), result_txt)
    await msg.edit(view=result_panel.view())
    await asyncio.sleep(3)
    return result_txt


def _make_devinette_view(panel, joueur_actif, event, choix_dict):
    view = discord.ui.LayoutView(timeout=20)
    view.add_item(panel.container())
    # 1-5 on the first ActionRow, 6-10 on the second (was row=0/1).
    rows    = [discord.ui.ActionRow(), discord.ui.ActionRow()]
    buttons = []
    for n in range(1, 11):
        btn = discord.ui.Button(
            label=str(n),
            style=discord.ButtonStyle.secondary,
            custom_id=f"dev_{joueur_actif.id}_{n}",
        )
        async def cb(interaction, num=n):
            if interaction.user.id != joueur_actif.id:
                await interaction.response.defer()
                return
            if joueur_actif.id in choix_dict:
                await interaction.response.defer()
                return
            choix_dict[joueur_actif.id] = num
            for child in buttons:
                child.disabled = True
            await interaction.response.defer()
            event.set()
        btn.callback = cb
        rows[0 if n <= 5 else 1].add_item(btn)
        buttons.append(btn)
    for r in rows:
        view.add_item(r)
    return view

async def devinette(msg, j1, s1, j2, s2, locale=DEFAULT_LOCALE):
    """Guess the secret number 1-10. Closest pick wins +30% damage for 2 turns."""
    nombre     = random.randint(1, 10)
    choix_dict = {}

    base_desc = t("duel.minigames.guess.desc", locale)

    ev1    = asyncio.Event()
    panel1 = Panel(t("duel.minigames.guess.title", locale), base_desc)
    panel1.footer(t("duel.minigames.waiting_first", locale, player=j1.display_name))
    await msg.edit(view=_make_devinette_view(panel1, j1, ev1, choix_dict))
    try:
        await asyncio.wait_for(ev1.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j1.id] = random.randint(1, 10)

    ev2    = asyncio.Event()
    panel2 = Panel(t("duel.minigames.guess.title", locale), base_desc)
    panel2.footer(t("duel.minigames.waiting_second", locale,
                    player1=j1.display_name, player2=j2.display_name))
    await msg.edit(view=_make_devinette_view(panel2, j2, ev2, choix_dict))
    try:
        await asyncio.wait_for(ev2.wait(), timeout=20)
    except asyncio.TimeoutError:
        choix_dict[j2.id] = random.randint(1, 10)

    c1, c2 = choix_dict[j1.id], choix_dict[j2.id]
    d1, d2 = abs(c1 - nombre), abs(c2 - nombre)

    result = t("duel.minigames.guess.reveal", locale, number=nombre,
               player1=j1.display_name, guess1=c1, gap1=d1,
               player2=j2.display_name, guess2=c2, gap2=d2)

    if d1 < d2:
        reward     = appliquer_recompense(s1, "buff_degats", locale)
        result    += t("duel.minigames.winner", locale, player=j1.display_name, reward=reward)
    elif d2 < d1:
        reward     = appliquer_recompense(s2, "buff_degats", locale)
        result    += t("duel.minigames.winner", locale, player=j2.display_name, reward=reward)
    else:
        result    += t("duel.minigames.guess.perfect_draw", locale)

    result_panel = Panel(t("duel.minigames.guess.result_title", locale), result)
    await msg.edit(view=result_panel.view())
    await asyncio.sleep(3)
    return result


async def duel_des(msg, j1, s1, j2, s2, locale=DEFAULT_LOCALE):
    """Both players roll 3d6 at the same time. Highest total wins +60 HP."""
    rolled = {"j1": None, "j2": None}
    ev1    = asyncio.Event()
    ev2    = asyncio.Event()

    # Two independent buttons (simultaneous), one ActionRow each (was row=0/1)
    btn1 = discord.ui.Button(
        label=t("duel.minigames.dice.button", locale, player=j1.display_name),
        style=discord.ButtonStyle.primary,
        custom_id=f"des_{j1.id}",
    )
    async def roll1(interaction):
        if interaction.user.id != j1.id or rolled["j1"] is not None:
            await interaction.response.defer()
            return
        rolled["j1"] = [random.randint(1, 6) for _ in range(3)]
        btn1.disabled = True
        btn1.label    = t("duel.minigames.dice.rolled", locale, player=j1.display_name,
                          dice=rolled["j1"], total=sum(rolled["j1"]))
        await interaction.response.edit_message(view=view)
        ev1.set()
    btn1.callback = roll1

    btn2 = discord.ui.Button(
        label=t("duel.minigames.dice.button", locale, player=j2.display_name),
        style=discord.ButtonStyle.danger,
        custom_id=f"des_{j2.id}",
    )
    async def roll2(interaction):
        if interaction.user.id != j2.id or rolled["j2"] is not None:
            await interaction.response.defer()
            return
        rolled["j2"] = [random.randint(1, 6) for _ in range(3)]
        btn2.disabled = True
        btn2.label    = t("duel.minigames.dice.rolled", locale, player=j2.display_name,
                          dice=rolled["j2"], total=sum(rolled["j2"]))
        await interaction.response.edit_message(view=view)
        ev2.set()
    btn2.callback = roll2

    panel = Panel(
        t("duel.minigames.dice.title", locale),
        t("duel.minigames.dice.desc", locale),
    )
    panel.field(f"❤️ {j1.display_name}", barre_hp(s1["hp"], s1["hp_max"]), inline=True)
    panel.field(f"❤️ {j2.display_name}", barre_hp(s2["hp"], s2["hp_max"]), inline=True)
    view = panel.view(row(btn1), row(btn2), timeout=25)
    await msg.edit(view=view)

    try:
        await asyncio.wait_for(asyncio.gather(ev1.wait(), ev2.wait()), timeout=25)
    except asyncio.TimeoutError:
        if rolled["j1"] is None:
            rolled["j1"] = [random.randint(1, 6) for _ in range(3)]
        if rolled["j2"] is None:
            rolled["j2"] = [random.randint(1, 6) for _ in range(3)]

    t1, t2 = sum(rolled["j1"]), sum(rolled["j2"])
    result = (
        t("duel.minigames.dice.line", locale, player=j1.display_name, dice=rolled["j1"], total=t1)
        + t("duel.minigames.dice.line", locale, player=j2.display_name, dice=rolled["j2"], total=t2)
        + "\n"
    )

    if t1 > t2:
        reward  = appliquer_recompense(s1, "hp_60", locale)
        result += t("duel.minigames.winner", locale, player=j1.display_name, reward=reward)
    elif t2 > t1:
        reward  = appliquer_recompense(s2, "hp_60", locale)
        result += t("duel.minigames.winner", locale, player=j2.display_name, reward=reward)
    else:
        result += t("duel.minigames.draw", locale)

    result_panel = Panel(t("duel.minigames.dice.result_title", locale), result)
    await msg.edit(view=result_panel.view())
    await asyncio.sleep(3)
    return result


# "expr" is rendered inside duel.minigames.quickmath.question, answers untouched.
QUESTIONS = [
    {"expr": "7 × 8",     "r": 56,  "choices": [56, 48, 63, 42]},
    {"expr": "12 + 37",   "r": 49,  "choices": [49, 47, 51, 43]},
    {"expr": "100 − 36",  "r": 64,  "choices": [64, 66, 62, 74]},
    {"expr": "9 × 9",     "r": 81,  "choices": [81, 72, 84, 63]},
    {"expr": "144 ÷ 12",  "r": 12,  "choices": [12, 11, 14, 13]},
    {"expr": "6³",        "r": 216, "choices": [216, 196, 243, 208]},
    {"expr": "15 × 4",    "r": 60,  "choices": [60, 55, 64, 48]},
    {"expr": "25 × 3",    "r": 75,  "choices": [75, 70, 80, 65]},
    {"expr": "200 − 77",  "r": 123, "choices": [123, 127, 113, 133]},
    {"expr": "8 × 7",     "r": 56,  "choices": [56, 54, 63, 48]},
]

async def calcul_rapide(msg, j1, s1, j2, s2, locale=DEFAULT_LOCALE):
    """Math question, first player to click the right answer wins."""
    question = random.choice(QUESTIONS)
    choices  = question["choices"].copy()
    random.shuffle(choices)

    first   = {"uid": None}
    ev      = asyncio.Event()

    # 2 answers per ActionRow (was row=0/1)
    rows    = [discord.ui.ActionRow(), discord.ui.ActionRow()]
    buttons = []
    for i, choice in enumerate(choices):
        btn = discord.ui.Button(
            label=str(choice),
            style=discord.ButtonStyle.primary,
            custom_id=f"calc_{choice}_{i}",
        )
        async def cb(interaction, c=choice):
            if interaction.user.id not in (j1.id, j2.id):
                await interaction.response.defer()
                return
            if c == question["r"] and first["uid"] is None:
                first["uid"] = interaction.user.id
                for child in buttons:
                    child.disabled = True
                await interaction.response.defer()
                ev.set()
            else:
                await interaction.response.defer()
        btn.callback = cb
        rows[0 if i < 2 else 1].add_item(btn)
        buttons.append(btn)

    panel = Panel(
        t("duel.minigames.quickmath.title", locale),
        t("duel.minigames.quickmath.desc", locale,
          question=t("duel.minigames.quickmath.question", locale, expr=question["expr"])),
    )
    panel.footer(t("duel.minigames.quickmath.footer", locale))
    view = panel.view(*rows, timeout=15)
    await msg.edit(view=view)

    try:
        await asyncio.wait_for(ev.wait(), timeout=15)
    except asyncio.TimeoutError:
        pass

    if first["uid"]:
        winner_stats = s1 if first["uid"] == j1.id else s2
        winner_name  = j1.display_name if first["uid"] == j1.id else j2.display_name
        reward       = appliquer_recompense(winner_stats, "parade_reset_hp", locale)
        result_txt   = t("duel.minigames.quickmath.winner", locale,
                         answer=question["r"], player=winner_name, reward=reward)
    else:
        result_txt = t("duel.minigames.quickmath.timeout", locale, answer=question["r"])

    result_panel = Panel(t("duel.minigames.quickmath.result_title", locale), result_txt)
    await msg.edit(view=result_panel.view())
    await asyncio.sleep(3)
    return result_txt


# Mini-game -> i18n key suffix under duel.minigames.names
NOM_JEUX = {
    pfc:           "rps",
    reflexe:       "reflex",
    devinette:     "guess",
    duel_des:      "dice",
    calcul_rapide: "quickmath",
}

async def run_minigame(msg, j1, s1, j2, s2, tour, locale=DEFAULT_LOCALE):
    """Pick and run a random mini-game. Return the result description."""
    jeu = random.choice(list(NOM_JEUX.keys()))
    jeu_nom = t(f"duel.minigames.names.{NOM_JEUX[jeu]}", locale)

    annonce = Panel(
        t("duel.minigames.announce_title", locale, turn=tour),
        t("duel.minigames.announce_desc", locale, game=jeu_nom),
    )
    annonce.field(f"❤️ {j1.display_name}", barre_hp(s1["hp"], s1["hp_max"]), inline=True)
    annonce.field(f"❤️ {j2.display_name}", barre_hp(s2["hp"], s2["hp_max"]), inline=True)
    # The mini-game runs on the main duel message, which may still carry the zone
    # image: clear the attachment so nothing dangles on a V2 message.
    await msg.edit(view=annonce.view(), attachments=[])
    await asyncio.sleep(2)

    return await jeu(msg, j1, s1, j2, s2, locale)
