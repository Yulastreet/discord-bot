"""Combat de boss coopératif (auto).

Flow :
1. Le boss apparaît -> phase de recrutement.
2. Les joueurs cliquent "Rejoindre" (stats issues de leur collection) et peuvent
   "Choisir ma carte" (définit l'élément de combat).
3. Le combat démarre 2 min après l'apparition, ou 10 s si 5 joueurs ont rejoint.
4. Combat AUTOMATIQUE au tour par tour : l'équipe tape, le boss riposte, jusqu'à
   la mort du boss ou l'anéantissement de l'équipe. Loot pour les participants.
"""
from __future__ import annotations

import asyncio
import random

import discord

from database import (
    BOSS_TIERS, card_boss_create, card_boss_get, card_boss_set_message,
    card_boss_apply_damage, card_boss_set_status, card_boss_set_start,
    boss_participant_add, boss_participant_get, boss_participants_list,
    boss_participant_update, compute_player_combat_stats, engaged_combat_stats,
    element_matchup, BOSS_TIER_SCALE, card_boss_set_stats,
    card_pick_random_exact_rarity, card_get, card_get_by_name, currency_add,
    user_card_add, user_card_count_owned, CARD_ELEMENT_LABELS, element_weaknesses,
    CARD_ELEMENTS, combat_power, boss_event_add,
)
import os as _os
import time as _t

_RECRUIT_SECONDS = 120     # delai de combat apres le 1er joueur
_JOIN_EXPIRE = 900         # si personne ne rejoint, le boss disparaît (15 min)
_QUICK_START_AT = 5        # nb de joueurs qui declenche le demarrage rapide
_MAX_PLAYERS = 5           # equipe limitee a 5 joueurs max
# Poids du bonus de fusion DANS le scaling du boss (0 = la fusion ne gonfle pas le
# boss du tout, 1 = compte plein). Le joueur garde son bonus complet pour SES degats.
_FUSION_BOSS_SCALE_WEIGHT = 0.75
_QUICK_SECONDS = 60        # delai du demarrage rapide
_TURN_DELAY = 4.8          # secondes entre 2 tours auto
_MAX_TURNS = 60
_BOSS_RATIO = 0.5          # le boss frappe a 50% de son atk

_RARITY_RANK = {"common": 0, "rare": 1, "epic": 2, "legendary": 3, "mythic": 4, "secret": 5}
_SORT_CYCLE = [None, "nom", "rareté", "étoiles", "optimisation"]
_SORT_BTN_LBL = {None: "🔃 Trier", "nom": "🔤 Nom ↑", "rareté": "🎯 Rareté ↓",
                 "étoiles": "⭐ Étoiles ↓", "optimisation": "⚡ Optimal"}


def _card_atk_mult(c):
    """Multiplicateur d'ATK REEL de la carte = rareté × etoiles, avec le cas
    special secret 5★ = 999. Une common 5★ peut depasser une epic 0★."""
    from database import CARD_RARITY_COMBAT_MULT, CARD_STAR_COMBAT_BONUS
    rar = c.get("rarity") or ""
    stars = int(c.get("stars", 0))
    if rar == "secret" and stars >= 5:
        return 999.0
    rar_mult = CARD_RARITY_COMBAT_MULT.get(rar, 1.0)
    star_mult = 1.0 + min(5, stars) * CARD_STAR_COMBAT_BONUS
    return rar_mult * star_mult


def _card_effectiveness(c, boss_element):
    """Efficacite totale contre CE boss = multiplicateur ATK × bonus elementaire."""
    return _card_atk_mult(c) * element_matchup(c.get("element") or "", boss_element or "")


def _sort_cards(rows, mode, boss_element=None):
    if mode == "nom":
        return sorted(rows, key=lambda c: c["name"].lower())
    if mode == "rareté":
        return sorted(rows, key=lambda c: -_RARITY_RANK.get(c.get("rarity", ""), 0))
    if mode == "étoiles":
        return sorted(rows, key=lambda c: -int(c.get("stars", 0)))
    if mode == "optimisation":
        # tri par l'efficacite reelle (mult ATK reel × avantage elementaire) :
        # une carte moins rare mais plus etoilee passe devant si elle frappe plus fort.
        return sorted(rows, key=lambda c: -_card_effectiveness(c, boss_element))
    return rows

# Fourchette de rareté de la carte "avatar" du boss selon le tier
_TIER_RANGE = {
    1: ["common", "rare"],
    2: ["rare", "epic"],
    3: ["rare", "epic", "legendary"],
    4: ["epic", "legendary", "mythic"],
    5: ["legendary", "mythic", "secret"],
}
# Rareté du loot = le max de la fourchette du tier
def _tier_loot_rarity(tier):
    return _TIER_RANGE.get(tier, ["epic"])[-1]

# Difficulte supplementaire selon la rareté de l'avatar DANS la fourchette du tier.
# Multiplicatif : facteur = step^cran, applique aux PV ET a l'ATK du boss (par-dessus
# le scaling d'equipe). Plus le tier est haut, plus l'ecart entre raretes est brutal.
# T5 : Legendary x1.0, Mythic x1.5, Secret x2.25. Reglable.
_TIER_RARITY_STEP = {1: 1.10, 2: 1.12, 3: 1.15, 4: 1.25, 5: 1.50}


def _avatar_difficulty(tier, idx):
    return _TIER_RARITY_STEP.get(tier, 1.12) ** idx


_RARITY_ORDER = ["common", "rare", "epic", "legendary", "mythic", "secret"]


def _avatar_idx(tier, rarity):
    """Cran de difficulte de la rareté dans la fourchette du tier. Hors fourchette
    (rareté forcee depuis le dashboard) : clamp selon le rang global."""
    rng = _TIER_RANGE.get(tier, ["epic"])
    if rarity in rng:
        return rng.index(rarity)
    try:
        rank = _RARITY_ORDER.index(rarity)
    except ValueError:
        return 0
    rng_ranks = [_RARITY_ORDER.index(r) for r in rng if r in _RARITY_ORDER]
    if rng_ranks and rank >= max(rng_ranks):
        return len(rng) - 1
    return 0


# Rolls offerts en victoire selon le tier (le max a ~5% de chance).
#   T4 : 2-5 (5=5%) | T5 : 5-15 (15=5%). T1-3 : aucun.
_BOSS_ROLL_WEIGHTS = {
    4: {2: 45, 3: 30, 4: 20, 5: 5},
    5: {5: 26, 6: 18, 7: 14, 8: 11, 9: 8, 10: 6, 11: 5, 12: 3,
        13: 2, 14: 2, 15: 5},
}


def _boss_roll_reward(tier):
    w = _BOSS_ROLL_WEIGHTS.get(tier)
    if not w:
        return 0
    return random.choices(list(w.keys()), weights=list(w.values()), k=1)[0]

# Aptitudes de combat (5 roles distincts, chacun avec un cout)
_APT_LABELS = {
    "berserker": "Berserker", "gardien": "Gardien", "soigneur": "Soigneur",
    "duelliste": "Duelliste", "executeur": "Exécuteur",
}
_APT_EMOJI = {
    "berserker": "🩸", "gardien": "🛡️", "soigneur": "💚",
    "duelliste": "⚔️", "executeur": "💀",
}
# Berserker : +ATK / +degats subis
_BERSERK_ATK = 1.30
_BERSERK_TAKEN = 1.25
# Gardien : -degats subis / -ATK
_GARDIEN_TAKEN = 0.65
_GARDIEN_ATK = 0.85
# Soigneur : soigne le plus blesse / -ATK
_SOIGNEUR_ATK = 0.85
_SOIGNEUR_HEAL = 0.08     # % PV max rendu au plus blesse, par tour d'equipe
# Duelliste : avantage elementaire amplifie
_DUELLISTE_ADV = 1.50     # remplace le x1.25 quand on a l'avantage
# Executeur : +ATK quand le boss est dechaine (<50% PV)
_EXECUTEUR_ATK = 1.40


def _apt_atk_mult(apt, matchup, boss_enraged):
    """Multiplicateur d'ATK offensif du a l'aptitude (hors matchup de base)."""
    if apt == "berserker":
        return _BERSERK_ATK
    if apt == "gardien":
        return _GARDIEN_ATK
    if apt == "soigneur":
        return _SOIGNEUR_ATK
    if apt == "executeur" and boss_enraged:
        return _EXECUTEUR_ATK
    return 1.0


def _apt_matchup(apt, matchup):
    """Matchup effectif : le Duelliste amplifie l'avantage elementaire."""
    if apt == "duelliste" and matchup > 1.0:
        return _DUELLISTE_ADV
    return matchup


def _apt_taken_mult(apt):
    """Multiplicateur de degats SUBIS du a l'aptitude."""
    if apt == "berserker":
        return _BERSERK_TAKEN
    if apt == "gardien":
        return _GARDIEN_TAKEN
    return 1.0


def _apt_badge(apt):
    e = _APT_EMOJI.get(apt)
    return f" {e}" if e else ""


def _build_battlefield(bid):
    """Compose le champ de bataille sur le fond bossfightbg.png : 5 cartes joueurs
    en haut, VS au centre, boss en bas. Cartes joueurs avec bordure + etoiles.
    Retourne le chemin local ou None."""
    import os
    from PIL import Image
    from services.card_render import _ROOT, _load_base
    from services.card_profile import _card_image_for
    try:
        boss = card_boss_get(bid)
        parts = boss_participants_list(bid)
        bg_path = os.path.join(_ROOT, "assets", "cardrelated", "bossfightbg.png")
        if os.path.exists(bg_path):
            canvas = Image.open(bg_path).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (1672, 941), (24, 16, 32, 255))
        W, H = canvas.size

        # Tailles uniformes joueurs + boss
        cw, ch = 250, 375
        gap = 30

        def _place_card(img, x, y):
            r = img.convert("RGBA").resize((cw, ch), Image.LANCZOS)
            canvas.paste(r, (int(x), int(y)), r)

        # Cartes joueurs (haut, max 5) avec bordure + etoiles
        top_y = 25
        n = min(5, len(parts))
        if n:
            total_w = n * cw + (n - 1) * gap
            x0 = (W - total_w) // 2
            for i, p in enumerate(parts[:5]):
                img = None
                if p.get("card_id"):
                    img = _card_image_for(p["user_id"], int(p["card_id"]), allow_alt=True)
                    if img is None:
                        img = _load_base(int(p["card_id"]), None)
                if img is not None:
                    _place_card(img, x0 + i * (cw + gap), top_y)

        # Boss (bas centre, meme taille). PRIORITE AU RENDER LOCAL de la carte
        # avatar (anti-liens-morts) : _load_base cherche card_renders/<card_id>
        # puis tombe sur image_url http seulement si pas de render local.
        by = H - ch - 25
        bx = (W - cw) // 2
        bcid = boss.get("card_id")
        bimg = _load_base(int(bcid) if bcid else 0, boss.get("image_url"))
        if bimg is not None:
            _place_card(bimg, bx, by)

        out_dir = os.path.join(_ROOT, "static", "card_boss")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{bid}.png")
        canvas.convert("RGB").save(out, "PNG", optimize=True)
        return out
    except Exception as e:
        print(f"[boss] battlefield err: {e}")
        return None


def _cemoji(bot, name, fallback):
    """Emoji personnalisé du serveur support (résolu par nom), sinon fallback."""
    try:
        e = discord.utils.get(bot.emojis, name=name)
        if e:
            return str(e)
    except Exception:
        pass
    return fallback


def _power_compact(n) -> str:
    """>=1M -> 'XmYYY' (millions + m + milliers, unites droppees). Ex 1345986 -> '1m345'.
    Sinon le nombre entier. Le 'm' = emoji custom million."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n // 1_000_000}m{(n // 1000) % 1000:03d}"
    return str(n)


def _power_digits(bot, n, suffix="_") -> str:
    """Nombre -> emojis chiffres custom du SEUL serveur support.
    suffix='_' -> '0_'..'9_' (joueurs) + format compact million (emoji 'm_').
    suffix='boss' -> '0boss'..'9boss', PAS d'emoji million -> nombre entier.
    Restreint au support (collision de noms courts)."""
    sg = int((_os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
    guild = bot.get_guild(sg) if sg else None
    by_name = {}
    if guild:
        for e in guild.emojis:
            by_name[e.name.lower()] = str(e)
    # boss : pas d'emoji million -> nombre entier complet. Joueurs : format compact.
    s = str(int(n)) if suffix == "boss" else _power_compact(n)
    out = []
    for ch in s:
        if ch == "m":
            out.append(by_name.get("m_", "M"))
        elif ch.isdigit():
            out.append(by_name.get(f"{ch}{suffix}", ch))
        else:
            out.append(ch)
    return "".join(out)


def _boss_image_url(boss):
    """URL http affichable pour l'image du boss. image_url distante directe,
    sinon resout le render local de l'avatar via le domaine (PUBLIC_BASE_URL).
    Les cartes hebergees ont une image_url /static relative que set_image refuse."""
    img = boss.get("image_url") or ""
    if isinstance(img, str) and img.startswith("http"):
        return img
    try:
        from commandes.cards import _resolve_card_image
        cid = boss.get("card_id")
        card = card_get(cid) if cid else None
        if not card and cid:
            card = {"id": cid, "image_url": img}
        if card:
            url, _file = _resolve_card_image(card)
            if url:
                return url
    except Exception:
        pass
    return None


def _bar(bot, cur, mx, enraged=False, segments=15):
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    pct = int(round(100 * cur / mx)) if mx > 0 else 0
    # Emojis perso : pleine / déchainée (sections restantes) / vide (sections perdues)
    full = _cemoji(bot, "lifebardechaine" if enraged else "lifebarfull", "🟥")
    empty = _cemoji(bot, "lifebarempty", "⬛")
    return full * filled + empty * (segments - filled) + f"  **{pct}%**"


def _fit_segments(n_players: int) -> int:
    """Nb de segments de barre de vie joueur tenant sous la limite 1024/champ.
    Les emojis de barre sont lourds (~39 chars), on reduit si beaucoup de joueurs."""
    if n_players <= 1:
        return 8
    budget = max(120, 1000 // n_players - 80)
    return max(3, min(8, budget // 39))


def _player_bar(bot, cur, mx, segments=8):
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    full = _cemoji(bot, "playerlifebarfull", "🟩")
    empty = _cemoji(bot, "lifebarempty", "⬛")
    return full * filled + empty * (segments - filled)


def _default_card(user_id):
    """Carte de combat par defaut : carte 'milieu' du profil, sinon 1ere possedee."""
    from database import card_profile_get, get_db
    prof = card_profile_get(user_id) or {}
    cid = prof.get("mid_id") or prof.get("left_id") or prof.get("right_id")
    if cid:
        card = card_get(int(cid))
        if card:
            return card
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT c.* FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                  "WHERE uc.user_id = ? LIMIT 1", (str(user_id),)).fetchone()
    conn.close()
    return dict(r) if r else None


def _elem(bot, e):
    try:
        from commandes.cards import _get_element_emoji
        return _get_element_emoji(bot, e)
    except Exception:
        return ""


def _fmt(n):
    return f"{int(n):,}".replace(",", " ")


def _default_element(user_id):
    from database import card_profile_get, get_db
    prof = card_profile_get(user_id) or {}
    cid = prof.get("mid_id") or prof.get("left_id") or prof.get("right_id")
    if cid:
        card = card_get(int(cid))
        if card and card.get("element"):
            return card["element"]
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT c.element FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                  "WHERE uc.user_id = ? AND c.element IS NOT NULL LIMIT 1",
                  (str(user_id),)).fetchone()
    conn.close()
    return r["element"] if r else "eclat"


def build_boss_embed(bot, boss, phase_text="", log=None, battle=False):
    boss = card_boss_get(boss["id"])
    parts = boss_participants_list(boss["id"])
    color = {"defeated": 0x4ade80, "wiped": 0xff3d57}.get(boss["status"], 0x8e44ad)
    embed = discord.Embed(
        title=f"⚔️ Boss {BOSS_TIERS.get(boss['tier'], {}).get('label','')} ｜ {boss['name']}",
        color=color)
    embed.add_field(name="Élément",
                    value=f"{_elem(bot, boss['element'])} {CARD_ELEMENT_LABELS.get(boss['element'],'?')}",
                    inline=True)
    weak = element_weaknesses(boss["element"])
    weak_txt = " ".join(f"{_elem(bot, w)} {CARD_ELEMENT_LABELS.get(w,'?')}" for w in weak) or "—"
    embed.add_field(name="Faible contre", value=weak_txt, inline=True)
    embed.add_field(name="ATK", value=f"🗡️ {_fmt(boss['atk'])}", inline=True)
    _enraged = boss["status"] == "fighting" and boss["hp"] < boss["max_hp"] * 0.5
    boss_pw = _power_digits(bot, combat_power(boss['max_hp'], boss['atk']), suffix="boss")
    embed.add_field(name=f"❤️ PV du boss : {_fmt(boss['hp'])} / {_fmt(boss['max_hp'])}",
                    value=_bar(bot, boss['hp'], boss['max_hp'], enraged=_enraged)
                          + f"\n⚡ **PUISSANCE** : {boss_pw}",
                    inline=False)
    # Info (recrutement / résultat) JUSTE SOUS les PV
    info = ""
    if phase_text:
        info = phase_text
    elif boss["status"] == "recruiting":
        if boss.get("start_at"):
            info = (f"🐲 **Recrutement !** Le combat démarre <t:{int(boss['start_at'])}:R>\n"
                    f"(ou {_QUICK_SECONDS} s si **{_QUICK_START_AT}** joueurs).\n"
                    f"🛡️ **Rejoindre** puis 🎴 **Carte** / 🩸 **Aptitude**.")
        else:
            info = ("🐲 **En attente d'un premier combattant…**\n"
                    "Le timer de 2 min démarre dès qu'un joueur rejoint.\n"
                    "🛡️ **Rejoindre** puis ⚙️ **Paramètres de combat**.")
    elif boss["status"] == "defeated":
        info = "🎉 **Boss vaincu !**"
    elif boss["status"] == "wiped":
        info = "💀 **L'équipe a été anéantie.** Le boss survit."
    if info:
        embed.add_field(name="​", value=info, inline=False)
    if parts:
        plist = parts[:12]
        in_battle = bool(battle) or boss["status"] == "fighting"
        if in_battle:
            # EN COMBAT : barre de vie 10 segments + HP exact qui descend.
            blocks = []
            for p in plist:
                ko = " 💀" if p["hp"] <= 0 else ""
                mx = p.get("max_hp") or p["hp"]
                bar = _player_bar(bot, p["hp"], mx, 10)
                blocks.append(
                    f"{_elem(bot, p['element'])} **{p['name']}**{_apt_badge(p.get('aptitude'))}{ko} "
                    f"🗡️ {_fmt(p['atk'])}\n"
                    f"HP ❤️ {bar} **{_fmt(max(0, p['hp']))}**")
            embed.add_field(name=f"🛡️ Équipe ({len(parts)})", value="​", inline=False)
            if len(blocks) <= 8:
                # 1 champ par joueur -> espacement uniforme
                for b in blocks:
                    embed.add_field(name="​", value=b, inline=False)
            else:
                # trop de joueurs : on regroupe pour tenir sous 25 champs / 6000 chars
                cur = ""
                for b in blocks:
                    add = ("\n" if cur else "") + b
                    if cur and len(cur) + len(add) > 950:
                        embed.add_field(name="​", value=cur, inline=False); cur = b
                    else:
                        cur += add
                if cur:
                    embed.add_field(name="​", value=cur, inline=False)
        else:
            # PREPA : 3 colonnes inline alignees par Discord, puissance sous le pseudo.
            # Degradation douce vs limite 1024 : emojis -> texte -> rien (puissance
            # tjrs visible meme avec 5 joueurs ou des puissances a 7 chiffres).
            def _name_cells(power_mode):  # 'emoji' | 'plain' | None
                cells = []
                for p in plist:
                    ko = " 💀" if p["hp"] <= 0 else ""
                    line = f"{_elem(bot, p['element'])} **{p['name']}**{_apt_badge(p.get('aptitude'))}{ko}"
                    pw = combat_power(p.get('max_hp') or p['hp'], p['atk'])
                    if power_mode == "emoji":
                        line += f"\n⚡{_power_digits(bot, pw)}"
                    elif power_mode == "plain":
                        line += f"\n⚡ {_fmt(pw)}"
                    cells.append(line)
                return cells
            mode = "emoji"
            name_cells = _name_cells("emoji")
            if len("​\n" + "\n".join(name_cells)) > 1000:
                mode = "plain"
                name_cells = _name_cells("plain")
                if len("​\n" + "\n".join(name_cells)) > 1000:
                    mode = None
                    name_cells = _name_cells(None)
            pad = "\n​" if mode else ""
            hp_cells = [f"{_fmt(max(0, p['hp']))}{pad}" for p in plist]
            atk_cells = [f"{_fmt(p['atk'])}{pad}" for p in plist]
            embed.add_field(name=f"🛡️ Équipe ({len(parts)})",
                            value="​\n" + "\n".join(name_cells), inline=True)
            embed.add_field(name="❤️ PV", value="​\n" + "\n".join(hp_cells), inline=True)
            embed.add_field(name="🗡️ ATK", value="​\n" + "\n".join(atk_cells), inline=True)
        total_pw = sum(combat_power(p.get('max_hp') or p['hp'], p['atk']) for p in parts)
        embed.add_field(name="⚡ Puissance totale du groupe",
                        value=_power_digits(bot, total_pw), inline=False)
    if log:
        embed.add_field(name="📜 Combat", value="\n".join(log[-4:]), inline=False)
    # Image : battlefield pendant le combat, sinon carte du boss durant le recrutement
    if battle:
        embed.set_image(url="attachment://battle.png")
    elif boss["status"] == "recruiting":
        burl = _boss_image_url(boss)
        if burl:
            embed.set_image(url=burl)
    return embed


def _boss_live_url(boss_id):
    base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/cards/boss/{int(boss_id)}" if base else None


class JoinView(discord.ui.View):
    def __init__(self, boss_id):
        super().__init__(timeout=None)
        self.boss_id = boss_id
        # Bouton lien -> combat live sur le dashboard (animations temps reel)
        url = _boss_live_url(boss_id)
        if url:
            self.add_item(discord.ui.Button(label="⚔️ Voir le combat en live",
                                            style=discord.ButtonStyle.link, url=url))

    @discord.ui.button(label="Rejoindre", style=discord.ButtonStyle.success, emoji="🛡️",
                       custom_id="boss_join")
    async def join(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True)
            return
        uid = interaction.user.id
        if boss_participant_get(self.boss_id, uid):
            await interaction.response.send_message("Tu es déjà dans l'équipe.", ephemeral=True)
            return
        if len(boss_participants_list(self.boss_id)) >= _MAX_PLAYERS:
            await interaction.response.send_message(
                f"⚔️ L'équipe est complète (**{_MAX_PLAYERS} joueurs** max).", ephemeral=True)
            return
        dcard = _default_card(uid)
        delem = (dcard.get("element") if dcard else None) or "eclat"
        dcid = dcard["id"] if dcard else None
        stats = engaged_combat_stats(uid, dcid) if dcid else compute_player_combat_stats(uid)
        d_atk = int(stats["atk"] * (_event_boss_dmg_mult(dcard, boss.get("guild_id")) if dcard else 1.0))
        boss_participant_add(self.boss_id, uid, interaction.user.display_name,
                             delem, stats["hp"], d_atk, card_id=dcid)
        # 1er joueur -> demarre le timer de 2 min
        if not boss.get("start_at"):
            card_boss_set_start(self.boss_id, _t.time() + _RECRUIT_SECONDS)
        # Scaling live : le boss grossit selon l'equipe presente (aperçu pendant le recrutement)
        _scale_boss_to_team(self.boss_id)
        await interaction.response.send_message(
            "🛡️ Tu as rejoint ! Élément par défaut = ta carte vedette.\n"
            "Utilise **🎴 Carte** et **🩸 Aptitude** pour te préparer.", ephemeral=True)
        try:
            await interaction.message.edit(embed=build_boss_embed(interaction.client, card_boss_get(self.boss_id)), view=self)
        except Exception:
            pass

    def _check(self, interaction):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            return "Le recrutement est terminé."
        if not boss_participant_get(self.boss_id, interaction.user.id):
            return "Rejoins d'abord (🛡️)."
        return None

    @discord.ui.button(label="Carte", style=discord.ButtonStyle.secondary, emoji="🎴",
                       custom_id="boss_card")
    async def card_btn(self, interaction, btn):
        err = self._check(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        # defer : charger toute la collection peut depasser les 3s (sinon "Echec
        # de l'interaction"). On envoie ensuite l'ephemere en followup.
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = _CardPickerView(self.boss_id, interaction.user.id, interaction.client)
        await interaction.followup.send(content="🎴 **Choisis ta carte de combat**",
                                        embed=view.build_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Aptitude", style=discord.ButtonStyle.secondary, emoji="🩸",
                       custom_id="boss_apt")
    async def apt_btn(self, interaction, btn):
        err = self._check(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        p = boss_participant_get(self.boss_id, interaction.user.id)
        await interaction.response.send_message(
            _aptitude_text(_APT_LABELS.get(p.get("aptitude"))),
            view=_AptitudeView(self.boss_id), ephemeral=True)


def _aptitude_text(cur_apt_label):
    base = ("🩸 **Aptitude de combat**\n"
            "**🩸 Berserker** — +30% ATK, mais +25% dégâts subis.\n"
            "**🛡️ Gardien** — -35% dégâts subis, mais -15% ATK.\n"
            "**💚 Soigneur** — soigne le plus blessé de +8% PV/tour, -15% ATK.\n"
            "**⚔️ Duelliste** — avantage élémentaire ×1.5 (au lieu de ×1.25).\n"
            "**💀 Exécuteur** — +40% ATK quand le boss est déchaîné (<50% PV).\n\n"
            "Choisis ci-dessous.")
    if cur_apt_label:
        base += f"\n✅ Actuelle : **{cur_apt_label}**."
    return base


async def _refresh_boss_msg(client, boss_id):
    """Rafraichit l'embed du message principal du boss (appelé depuis l'éphémère)."""
    boss = card_boss_get(boss_id)
    if not boss or not boss.get("message_id"):
        return
    ch = client.get_channel(int(boss["channel_id"]))
    if not ch:
        return
    try:
        m = await ch.fetch_message(int(boss["message_id"]))
        await m.edit(embed=build_boss_embed(client, boss))
    except Exception:
        pass


class _AptitudeView(discord.ui.View):
    def __init__(self, boss_id):
        super().__init__(timeout=300)
        self.boss_id = boss_id

    @discord.ui.select(placeholder="Choisir une aptitude…", min_values=1, max_values=1,
                       options=[
                           discord.SelectOption(label="Berserker", value="berserker", emoji="🩸",
                                                description="+30% ATK, +25% dégâts subis"),
                           discord.SelectOption(label="Gardien", value="gardien", emoji="🛡️",
                                                description="-35% dégâts subis, -15% ATK"),
                           discord.SelectOption(label="Soigneur", value="soigneur", emoji="💚",
                                                description="Soigne le plus blessé +8% PV/tour, -15% ATK"),
                           discord.SelectOption(label="Duelliste", value="duelliste", emoji="⚔️",
                                                description="Avantage élémentaire ×1.5"),
                           discord.SelectOption(label="Exécuteur", value="executeur", emoji="💀",
                                                description="+40% ATK quand boss déchaîné"),
                           discord.SelectOption(label="Aucune", value="none", emoji="➖",
                                                description="Pas d'aptitude"),
                       ])
    async def pick_apt(self, interaction, select):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True); return
        if not boss_participant_get(self.boss_id, interaction.user.id):
            await interaction.response.send_message("Rejoins d'abord (🛡️).", ephemeral=True); return
        val = select.values[0]
        boss_participant_update(self.boss_id, interaction.user.id,
                                aptitude=("" if val == "none" else val))
        lbl = _APT_LABELS.get(val)
        await interaction.response.edit_message(content=_aptitude_text(lbl), view=self)
        await _refresh_boss_msg(interaction.client, self.boss_id)


class _ElementFilterSelect(discord.ui.Select):
    def __init__(self, current):
        opts = [discord.SelectOption(label="Tous les éléments", value="all",
                                     default=(current is None))]
        for e in CARD_ELEMENTS:
            opts.append(discord.SelectOption(label=CARD_ELEMENT_LABELS.get(e, e),
                                             value=e, default=(current == e)))
        super().__init__(placeholder="Filtrer par élément…", min_values=1, max_values=1,
                         options=opts, row=0)

    async def callback(self, interaction):
        view = self.view
        v = self.values[0]
        view.element = None if v == "all" else v
        view.page = 1
        view._load()
        view._sync_dynamic()
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class _PageCardSelect(discord.ui.Select):
    def __init__(self, page_rows):
        from commandes.cards import RARITY_EMOJIS
        opts = []
        for c in page_rows[:25]:
            cnt = f" x{c['count']}" if c.get("count", 1) > 1 else ""
            st = int(c.get("stars", 0))
            star_lbl = f" {st}★" if st else ""
            desc = f"{(c.get('rarity') or '?')}{(' '+st*'⭐') if st else ''} · {(c.get('universe') or '?')}"[:100]
            opts.append(discord.SelectOption(
                label=f"{c['name']}{star_lbl}{cnt}"[:100], value=str(c["card_id"]),
                emoji=RARITY_EMOJIS.get(c.get("rarity"), "⚪"), description=desc))
        if not opts:
            opts = [discord.SelectOption(label="(aucune carte)", value="none")]
        super().__init__(placeholder="Choisir une carte de cette page…",
                         min_values=1, max_values=1, options=opts, row=1)

    async def callback(self, interaction):
        if self.values[0] == "none":
            await interaction.response.defer(); return
        from database import card_get
        card = card_get(int(self.values[0]))
        if not card:
            await interaction.response.send_message("Carte introuvable.", ephemeral=True); return
        await _apply_card_choice(interaction, self.view.boss_id, card)


class _CardPickerView(discord.ui.View):
    def __init__(self, boss_id, user_id, bot, element=None, sort_mode=None):
        super().__init__(timeout=300)
        self.boss_id = boss_id
        self.user_id = user_id
        self.bot = bot
        self.element = element
        self.sort_mode = sort_mode
        self.page = 1
        self._load()
        self._sync_dynamic()

    def _load(self):
        from database import user_card_list, user_card_fusion_map
        cards = user_card_list(self.user_id)
        fmap = user_card_fusion_map(self.user_id)
        grouped = {}
        for c in cards:
            if self.element and (c.get("element") or "") != self.element:
                continue
            cid = c["card_id"]
            if cid not in grouped:
                grouped[cid] = {**c, "count": 0, "stars": int(fmap.get(cid, 0))}
            grouped[cid]["count"] += 1
        boss = card_boss_get(self.boss_id)
        boss_elem = boss["element"] if boss else None
        self.boss_element = boss_elem
        self.rows = _sort_cards(list(grouped.values()), self.sort_mode, boss_element=boss_elem)
        self.total_pages = max(1, (len(self.rows) + 24) // 25)
        if self.page > self.total_pages:
            self.page = self.total_pages

    def _sync_dynamic(self):
        # reconstruit les 2 selects (filtre élément + cartes de la page courante)
        for it in list(self.children):
            if isinstance(it, (_ElementFilterSelect, _PageCardSelect)):
                self.remove_item(it)
        self.add_item(_ElementFilterSelect(self.element))
        page_rows = self.rows[(self.page - 1) * 25: self.page * 25]
        self.add_item(_PageCardSelect(page_rows))
        self._refresh()

    def _refresh(self):
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages
        self.counter.label = f"{self.page} / {self.total_pages}"
        self.sort_btn.label = _SORT_BTN_LBL[self.sort_mode]

    def build_embed(self):
        from commandes.cards import RARITY_EMOJIS
        page_rows = self.rows[(self.page - 1) * 25: self.page * 25]
        opt_mode = self.sort_mode == "optimisation" and self.boss_element
        lines = []
        for i, c in enumerate(page_rows):
            emoji = RARITY_EMOJIS.get(c["rarity"], "⚪")
            el = _elem(self.bot, c.get("element"))
            pre = f"{emoji}｜{el}" if el else emoji
            cnt = f" x{c['count']}" if c["count"] > 1 else ""
            stars = "⭐" * int(c.get("stars", 0))
            uni = c.get("universe") or "?"
            if opt_mode:
                m = element_matchup(c.get("element") or "", self.boss_element)
                match_tag = " 🔥" if m > 1 else (" 🟦" if m < 1 else "")
                # score d'efficacite reel = rarete × etoiles × bonus element
                eff = _card_effectiveness(c, self.boss_element)
                crown = "👑 " if (self.page == 1 and i == 0) else ""
                score = f" `×{eff:.2f}`"
                lines.append(f"{crown}{pre} **{c['name']}**{stars}{cnt}{match_tag}{score} · _{uni}_")
            else:
                lines.append(f"{pre} **{c['name']}**{stars}{cnt} · _{uni}_")
        elem_lbl = CARD_ELEMENT_LABELS.get(self.element, "tous") if self.element else "tous"
        if self.sort_mode == "optimisation":
            boss_lbl = CARD_ELEMENT_LABELS.get(self.boss_element, "?") if self.boss_element else "?"
            sort_lbl = f" · ⚡ optimal vs boss {boss_lbl}"
        elif self.sort_mode:
            sort_lbl = f" · trié: {self.sort_mode}"
        else:
            sort_lbl = ""
        head = f"**{len(self.rows)}** cartes · élément : **{elem_lbl}**{sort_lbl}"
        if opt_mode:
            head += ("\n_Classé par dégâts réels (rareté × étoiles × bonus élément). "
                     "👑 = meilleure carte pour ce boss._")
        embed = discord.Embed(
            title="🎴 Choisis ta carte de combat", color=0x8e44ad,
            description=head + "\n\n" + ("\n".join(lines) if lines else "_(aucune carte)_"))
        footer_extra = " · 🔥 avantage 🟦 désavantage" if opt_mode else ""
        embed.set_footer(text=f"Page {self.page}/{self.total_pages} · "
                              f"choisis dans la liste déroulante ou par nom 🎴{footer_extra}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=2)
    async def prev_btn(self, interaction, btn):
        if self.page > 1:
            self.page -= 1; self._sync_dynamic()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.primary, disabled=True, row=2)
    async def counter(self, interaction, btn):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_btn(self, interaction, btn):
        if self.page < self.total_pages:
            self.page += 1; self._sync_dynamic()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="🔃 Trier", style=discord.ButtonStyle.secondary, row=2)
    async def sort_btn(self, interaction, btn):
        idx = _SORT_CYCLE.index(self.sort_mode)
        self.sort_mode = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
        self._load()
        self.page = 1
        self._sync_dynamic()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Choisir par nom", style=discord.ButtonStyle.success, emoji="🎴", row=3)
    async def confirm(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True); return
        await interaction.response.send_modal(_ChooseCardModal(self.boss_id))


class _ChooseCardModal(discord.ui.Modal, title="Choisir ma carte de combat"):
    nom = discord.ui.TextInput(label="Nom de la carte (que tu possèdes)",
                                placeholder="ex: Goku", required=True, max_length=100)

    def __init__(self, boss_id):
        super().__init__()
        self.boss_id = boss_id

    async def on_submit(self, interaction):
        card = card_get_by_name(str(self.nom.value).strip())
        if not card:
            await interaction.response.send_message("Carte introuvable.", ephemeral=True); return
        await _apply_card_choice(interaction, self.boss_id, card)


# Bonus de degats des cartes d'event sur les boss (UNIQUEMENT pendant l'event).
_EVENT_BOSS_DMG_MULT = 1.15


def _event_boss_dmg_mult(card, guild_id) -> float:
    """1.15 si la carte appartient a l'event actif (sur ce serveur), sinon 1.0."""
    try:
        ek = (card.get("event_key") or "")
        if not ek:
            return 1.0
        from database import global_event_for_guild
        ev = global_event_for_guild(guild_id)
        if ev.get("active") and ev.get("key") == ek:
            return _EVENT_BOSS_DMG_MULT
    except Exception:
        pass
    return 1.0


async def _apply_card_choice(interaction, boss_id, card):
    """Engage la carte `card` pour le joueur : recalcule ATK/PV, maj équipe, confirme."""
    uid = interaction.user.id
    boss = card_boss_get(boss_id)
    if not boss or boss["status"] != "recruiting":
        await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True); return
    if user_card_count_owned(uid, card["id"]) <= 0:
        await interaction.response.send_message(f"Tu ne possèdes pas **{card['name']}**.", ephemeral=True); return
    elem = card.get("element") or "eclat"
    stats = engaged_combat_stats(uid, card["id"])
    # Bonus event (degats +15% pendant l'event pour les cartes de l'event)
    ev_mult = _event_boss_dmg_mult(card, boss.get("guild_id"))
    atk = int(stats["atk"] * ev_mult)
    # Recalcule PV/ATK selon la carte engagée (PV plein car recrutement)
    boss_participant_update(boss_id, uid, element=elem, card_id=card["id"],
                            atk=atk, max_hp=stats["hp"], hp=stats["hp"])
    m = element_matchup(elem, boss["element"]) if boss else 1.0
    if m > 1:
        match_txt = "🔥 **Avantage** contre le boss (x1.25 dégâts)"
    elif m < 1:
        match_txt = "🟦 **Désavantage** contre le boss (x0.8 dégâts)"
    else:
        match_txt = "⚪ Neutre contre le boss"
    # Detail du calcul ATK : rareté + etoiles de fusion
    if stats.get("secret_max"):
        calc = "🌈 secret 5⭐ MAX = **×999**"
    else:
        calc = f"{stats['rarity'] or '?'} ×{stats['rar_mult']:.2f}"
        if stats["stars"] > 0:
            calc += (f" · {stats['stars']}⭐ +{int(stats['stars'] * 20)}% "
                     f"(×{stats['star_mult']:.2f})")
        calc += f" = **×{stats['mult']:.2f}**"
    ev_txt = "  ·  ☀️ **carte d'event +15% dégâts**" if ev_mult > 1 else ""
    await interaction.response.send_message(
        f"🎴 **{card['name']}** ({_elem(interaction.client, elem)} {CARD_ELEMENT_LABELS.get(elem,'?')})\n"
        f"🗡️ ATK **{_fmt(atk)}** _({calc})_{ev_txt} "
        f"· ❤️ PV **{_fmt(stats['hp'])}** _(collection)_\n{match_txt}",
        ephemeral=True)
    await _refresh_boss_msg(interaction.client, boss_id)


async def spawn_boss(bot, guild_id, channel_id, tier=1, element=None, rarity=None):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return None
    tier = max(1, min(5, int(tier)))
    cfg = BOSS_TIERS[tier]
    # element optionnel : avatar contraint a cet element (sinon n'importe lequel)
    elem_filter = element if element in CARD_ELEMENT_LABELS else None
    rarity = rarity if rarity in _RARITY_ORDER else None
    avatar = None
    # rareté forcee (dashboard) : avatar de CETTE rareté exacte
    if rarity:
        avatar = (card_pick_random_exact_rarity(rarity, element=elem_filter)
                  or card_pick_random_exact_rarity(rarity))
    # sinon : rareté aléatoire dans la fourchette du tier
    if not avatar:
        rng = list(_TIER_RANGE.get(tier, ["epic"]))
        random.shuffle(rng)
        for _r in rng:
            avatar = card_pick_random_exact_rarity(_r, element=elem_filter)
            if avatar:
                break
        if not avatar and elem_filter:
            # aucune carte de cet element dans la fourchette -> on relâche le filtre
            for _r in rng:
                avatar = card_pick_random_exact_rarity(_r)
                if avatar:
                    break
    if not avatar:
        avatar = card_pick_random_exact_rarity("epic")
    name = avatar["name"] if avatar else "Entité inconnue"
    element = (avatar.get("element") if avatar else None) or random.choice(list(CARD_ELEMENT_LABELS.keys()))
    img = avatar.get("image_url") if avatar else None
    # Stats du boss scalées par la rareté de l'avatar : facteur = step^cran
    # (multiplicatif, raide aux hauts tiers), sur PV ET ATK. idx robuste hors fourchette.
    avatar_rar = (avatar or {}).get("rarity")
    idx = _avatar_idx(tier, avatar_rar)
    diff = _avatar_difficulty(tier, idx)
    # PV : facteur plein (tanky = DPS check). ATK : adouci (diff^0.6) pour ne pas
    # one-shot l'equipe avant qu'elle puisse DPS le boss.
    boss_atk = int(cfg["atk"] * diff ** 0.6)
    boss_hp = int(cfg["hp"] * diff)
    avatar_cid = avatar["id"] if avatar else None
    # start_at non defini : le timer ne demarre qu'au 1er joueur
    bid = card_boss_create(guild_id, channel_id, name, element, tier, boss_hp, boss_atk,
                           image_url=img, start_at=None, card_id=avatar_cid)
    card_boss_set_status(bid, "recruiting")
    boss = card_boss_get(bid)
    embed = build_boss_embed(bot, boss)
    view = JoinView(bid)
    # Ping du role "fans de cartes" si configure (/cardsetup role)
    from database import guild_card_config_get
    _role_id = (guild_card_config_get(guild_id) or {}).get("ping_role_id")
    content = "🐲 **Un boss est apparu !**"
    allowed = discord.AllowedMentions.none()
    if _role_id:
        content = f"<@&{_role_id}> {content}"
        allowed = discord.AllowedMentions(roles=True)
    msg = await channel.send(content=content, embed=embed, view=view, allowed_mentions=allowed)
    card_boss_set_message(bid, msg.id)
    asyncio.create_task(_run_boss(bot, bid, msg, view))
    return bid


async def resume_active_bosses(bot):
    """Au boot : relance la boucle des boss restés 'recruiting'/'fighting'.
    Sans ça, un pm2 restart laisse le boss orphelin (la task asyncio est morte)."""
    from database import card_boss_list_active
    resumed = 0
    for boss in card_boss_list_active():
        bid = boss["id"]
        ch = bot.get_channel(int(boss["channel_id"])) if boss.get("channel_id") else None
        mid = boss.get("message_id")
        if not ch or not mid:
            card_boss_set_status(bid, "expired")
            continue
        try:
            msg = await ch.fetch_message(int(mid))
        except Exception:
            card_boss_set_status(bid, "expired")  # message supprimé
            continue
        view = JoinView(bid)
        try:
            bot.add_view(view, message_id=int(mid))
        except Exception:
            pass
        asyncio.create_task(_run_boss(bot, bid, msg, view))
        resumed += 1
    if resumed:
        print(f"[boss] {resumed} combat(s) repris au boot")


def add_dummy_participants(bid, n):
    """[Test] Ajoute n combattants factices au boss (stats + element + carte aleatoires)."""
    import random as _r
    rarities = ["common", "rare", "epic", "legendary", "mythic"]
    for i in range(int(n)):
        card = None
        for _ in range(3):
            card = card_pick_random_exact_rarity(_r.choice(rarities))
            if card:
                break
        elem = (card.get("element") if card else None) or _r.choice(list(CARD_ELEMENT_LABELS.keys()))
        hp = _r.randint(80000, 220000)
        atk = _r.randint(30000, 75000)
        uid = f"dummy_{i+1}"
        boss_participant_add(bid, uid, f"Bot {i+1}", elem, hp, atk,
                             card_id=(card["id"] if card else None))
        apt = _r.choice(["berserker", "gardien", "soigneur", "duelliste", "executeur", ""])
        if apt:
            boss_participant_update(bid, uid, aptitude=apt)


def _scale_boss_to_team(bid):
    """Recalcule PV/ATK du boss selon la puissance de BASE de l'equipe presente.
    Le boss ne descend jamais sous son tier de reference (plancher), il ne fait
    que grossir pour les equipes fortes -> anti-powercreep sans risque de trivial."""
    try:
        boss = card_boss_get(bid)
        if not boss:
            return
        tier = boss["tier"]
        cfg = BOSS_TIERS.get(tier, {})
        sc = BOSS_TIER_SCALE.get(tier)
        if not sc or not cfg:
            return
        parts = boss_participants_list(bid)
        if not parts:
            return
        # somme ATK base, moyenne PV base de l'equipe (socle collection, hors carte)
        sum_atk = 0
        sum_hp = 0
        for p in parts:
            uid = p["user_id"]
            if _is_dummy(uid):
                base_atk, base_hp = int(p["atk"]), int(p.get("max_hp") or p["hp"])
            else:
                st = compute_player_combat_stats(uid)
                base_atk, base_hp = st["atk"], st["hp"]
                # Reduit l'impact du bonus de fusion sur le scaling du boss :
                # on retire le mult fusion plein puis on en re-applique une fraction.
                bp = float(st.get("bonus_pct", 0) or 0)
                if bp > 0:
                    full = 1.0 + bp / 100.0
                    red = 1.0 + (bp / 100.0) * _FUSION_BOSS_SCALE_WEIGHT
                    base_atk = base_atk / full * red
                    base_hp = base_hp / full * red
            sum_atk += base_atk
            sum_hp += base_hp
        n = len(parts)
        # Plancher = tier de reference : le boss ne descend jamais sous BOSS_TIERS,
        # il ne fait que grossir pour les equipes fortes (anti-powercreep).
        scaled_hp = int(sc["hp"] * sum_atk)
        scaled_atk = int(sc["atk"] * (sum_hp / n))
        # Facteur de difficulte rareté avatar (= step^cran), recalcule depuis la
        # rareté de l'avatar (card_id, immuable) -> pas de compound a chaque join.
        avatar = card_get(boss["card_id"]) if boss.get("card_id") else None
        idx = _avatar_idx(tier, (avatar or {}).get("rarity"))
        diff = _avatar_difficulty(tier, idx)
        # PV : facteur plein (tanky = DPS check). ATK : adouci (diff^0.6) pour ne pas
        # one-shot l'equipe avant qu'elle puisse DPS.
        new_hp = int(max(cfg.get("hp", scaled_hp), scaled_hp) * diff)
        new_atk = int(max(cfg.get("atk", scaled_atk), scaled_atk) * diff ** 0.6)
        card_boss_set_stats(bid, new_hp, new_atk)
    except Exception as e:
        print(f"[boss] scale err: {e!r}")


def _is_dummy(uid):
    return str(uid).startswith("dummy_")


async def _run_boss(bot, bid, msg, view):
    try:
        _b0 = card_boss_get(bid)
        if not _b0:
            return
        # ── Phase recrutement ── (sautée si on reprend un combat déjà lancé)
        if _b0["status"] == "recruiting":
            # Le timer (start_at) n'est posé qu'au 1er joueur. Sans joueur, on attend
            # jusqu'a _JOIN_EXPIRE puis le boss disparaît.
            join_deadline = _t.time() + _JOIN_EXPIRE
            quick = False
            last_sig = None
            while True:
                await asyncio.sleep(3)
                boss = card_boss_get(bid)
                if not boss or boss["status"] != "recruiting":
                    return
                parts = boss_participants_list(bid)
                # Re-render l'embed si la compo a change (inclut les modifs faites
                # depuis le dashboard : carte / aptitude / element).
                sig = [(p["user_id"], p.get("card_id"), p.get("aptitude"),
                        p.get("element"), p.get("atk"), p.get("max_hp")) for p in parts]
                if last_sig is not None and sig != last_sig:
                    try:
                        await msg.edit(embed=build_boss_embed(bot, card_boss_get(bid)), view=view)
                    except Exception:
                        pass
                last_sig = sig
                if len(parts) >= _QUICK_START_AT:
                    quick = True
                    break
                sa = boss.get("start_at")
                if sa and _t.time() >= sa:
                    break  # timer de 2 min ecoulé
                if not parts and _t.time() >= join_deadline:
                    break  # personne n'a rejoint -> expiration (gere plus bas)
            if quick:
                # compte a rebours rapide (timer visible)
                qstart = _t.time() + _QUICK_SECONDS
                card_boss_set_start(bid, qstart)
                boss = card_boss_get(bid)
                try:
                    await msg.edit(embed=build_boss_embed(bot, boss,
                        phase_text=f"⚡ **{_QUICK_START_AT} joueurs !** Le combat démarre <t:{int(qstart)}:R>."))
                except Exception:
                    pass
                await asyncio.sleep(_QUICK_SECONDS)

            parts = boss_participants_list(bid)
            if not parts:
                card_boss_set_status(bid, "expired")
                for ch in view.children:
                    ch.disabled = True
                try:
                    await msg.edit(content="🕸️ **Personne n'a rejoint le combat.**",
                                   embed=build_boss_embed(bot, card_boss_get(bid),
                                                          phase_text="Aucun participant. Le boss disparaît."),
                                   view=view)
                except Exception:
                    pass
                return

        # ── Scaling anti-powercreep : recalcule les PV/ATK du boss selon l'equipe ──
        _scale_boss_to_team(bid)

        # ── Combat automatique ──
        card_boss_set_status(bid, "fighting")
        for ch in view.children:
            # garde le bouton lien "voir le combat live" cliquable pendant le combat
            if getattr(ch, "style", None) == discord.ButtonStyle.link:
                continue
            ch.disabled = True
        log = ["⚔️ **Le combat commence !**"]
        boss_event_add(bid, "start", {})
        # Genere le champ de bataille (cartes joueurs vs boss) attaché une fois
        bf_path = _build_battlefield(bid)
        try:
            if bf_path:
                import os as _os
                await msg.edit(content="⚔️ **Combat en cours…**",
                               attachments=[discord.File(bf_path, filename="battle.png")],
                               embed=build_boss_embed(bot, card_boss_get(bid), log=log, battle=True),
                               view=view)
            else:
                await msg.edit(content="⚔️ **Combat en cours…**",
                               embed=build_boss_embed(bot, card_boss_get(bid), log=log), view=view)
        except Exception:
            pass

        # Tours ALTERNES : 1 tour = 1 action. L'équipe commence, puis le boss, etc.
        from database import card_boss_heal
        tier = boss["tier"]
        # T4+ : dechainement plus fort. T5 : coup devastateur 2 fois.
        enrage_mult = 1.65 if tier >= 4 else 1.50
        max_smashes = 2 if tier >= 5 else 1
        boss_self_heal = (tier >= 4)   # T4+ : le boss se soigne 1x sous 20% PV
        turn = 0
        actor = "party"
        smash_count = 0
        boss_healed = False
        enrage_announced = False
        def _apt(p):
            return p.get("aptitude") or ""

        def _boss_hit(p, dmg):
            """Applique les degats a un participant (apres mult d'aptitude defensive).
            Retourne (new_hp, ko_bool)."""
            real = max(1, int(dmg * _apt_taken_mult(_apt(p))))
            new_hp = max(0, p["hp"] - real)
            boss_participant_update(bid, p["user_id"], hp=new_hp)
            return new_hp, (new_hp <= 0), real

        def _apply_heals():
            """Chaque Soigneur vivant soigne le membre le plus blessé (+%PV max)."""
            healers = [p for p in boss_participants_list(bid)
                       if _apt(p) == "soigneur" and p["hp"] > 0]
            for _h in healers:
                cur = boss_participants_list(bid)
                # cible = membre vivant le plus bas en % PV (qui n'est pas full)
                cand = [p for p in cur if 0 < p["hp"] < (p.get("max_hp") or p["hp"])]
                if not cand:
                    continue
                tgt = min(cand, key=lambda p: p["hp"] / (p.get("max_hp") or p["hp"]))
                mx = tgt.get("max_hp") or tgt["hp"]
                heal = int(mx * _SOIGNEUR_HEAL)
                boss_participant_update(bid, tgt["user_id"], hp=min(mx, tgt["hp"] + heal))
                boss_participant_update(bid, _h["user_id"], add_heal=heal)
                log.append(f"Tour {turn} · 💚 **{_h['name']}** soigne **{tgt['name']}** (+{_fmt(heal)} PV)")
                boss_event_add(bid, "party_heal", {"healer": str(_h["user_id"]),
                                                   "target": str(tgt["user_id"]), "amount": heal})

        while turn < _MAX_TURNS:
            turn += 1
            await asyncio.sleep(_TURN_DELAY)
            boss = card_boss_get(bid)
            if not boss or boss["status"] != "fighting":
                return
            alive = [p for p in boss_participants_list(bid) if p["hp"] > 0]
            if not alive:
                card_boss_set_status(bid, "wiped")
                break
            if actor == "party":
                boss_enraged = boss["hp"] < boss["max_hp"] * 0.5
                total = 0
                best_eff = 1.0
                for p in alive:
                    apt = _apt(p)
                    m = _apt_matchup(apt, element_matchup(p["element"], boss["element"]))
                    am = _apt_atk_mult(apt, m, boss_enraged)
                    pdmg = max(1, int(p["atk"] * m * am))
                    total += pdmg
                    best_eff = max(best_eff, m)
                    # enregistre la contribution de chaque joueur (pour le butin/MVP)
                    boss_participant_update(bid, p["user_id"], add_damage=pdmg)
                boss_hp = card_boss_apply_damage(bid, total)
                eff = " 🔥" if best_eff > 1 else ""
                log.append(f"Tour {turn} · 🗡️ L'équipe inflige **{_fmt(total)}**{eff}")
                boss_event_add(bid, "party_hit", {"total": total, "crit": best_eff > 1, "turn": turn})
                _apply_heals()
                # T4+ : le boss se soigne une fois quand il tombe sous 20% PV
                if boss_self_heal and not boss_healed and 0 < boss_hp < boss["max_hp"] * 0.20:
                    boss_healed = True
                    heal = int(boss["max_hp"] * 0.10)
                    boss_hp = card_boss_heal(bid, heal)
                    log.append(f"Tour {turn} · 🩹 **Le boss se régénère (+{_fmt(heal)} PV) !**")
                    boss_event_add(bid, "boss_heal", {"amount": heal})
                if boss_hp <= 0:
                    card_boss_set_status(bid, "defeated")
                    break
                actor = "boss"
            elif smash_count < max_smashes and random.random() < 0.25:
                # Coup special : cible 1 joueur, degats x3 (T5 : jusqu'a 2x/combat)
                smash_count += 1
                target = random.choice(alive)
                cm = element_matchup(boss["element"], target["element"])
                dmg = max(1, int(boss["atk"] * cm * 3))
                new_hp, dead, real = _boss_hit(target, dmg)
                ko = " 💀 **KO !**" if dead else ""
                log.append(f"Tour {turn} · 💥 **COUP DÉVASTATEUR !** Le boss cible "
                           f"**{target['name']}** : -**{_fmt(real)}**{ko}")
                boss_event_add(bid, "boss_smash", {"target": str(target["user_id"]),
                                                   "dmg": real, "ko": dead, "turn": turn})
                if all(pp["hp"] <= 0 for pp in boss_participants_list(bid)):
                    card_boss_set_status(bid, "wiped")
                    break
                actor = "party"
            else:
                # Le boss frappe TOUTE l'équipe (AoE). Déchaîné (<50% PV).
                enraged = boss["hp"] < boss["max_hp"] * 0.5
                rage = enrage_mult if enraged else 1.0
                if enraged and not enrage_announced:
                    enrage_announced = True
                    log.append(f"🔥 **Le boss se déchaîne et inflige {enrage_mult:.2f}x"
                               f" plus de dégâts !**".replace(".", ","))
                    boss_event_add(bid, "enrage", {"mult": enrage_mult})
                kos = []
                ko_ids = []
                total_dmg = 0
                for p in alive:
                    cm = element_matchup(boss["element"], p["element"])
                    dmg = max(1, int(boss["atk"] * cm * rage))
                    new_hp, dead, real = _boss_hit(p, dmg)
                    total_dmg += real
                    if dead:
                        kos.append(p["name"]); ko_ids.append(str(p["user_id"]))
                ko_txt = f" · 💀 KO : {', '.join(kos)}" if kos else ""
                log.append(f"Tour {turn} · 👹 Le boss frappe toute l'équipe : "
                           f"**{_fmt(total_dmg)}**{ko_txt}")
                boss_event_add(bid, "boss_aoe", {"total": total_dmg, "enraged": enraged,
                                                 "kos": ko_ids, "targets": [str(p["user_id"]) for p in alive],
                                                 "turn": turn})
                if all(pp["hp"] <= 0 for pp in boss_participants_list(bid)):
                    card_boss_set_status(bid, "wiped")
                    break
                actor = "party"
            try:
                await msg.edit(embed=build_boss_embed(bot, card_boss_get(bid), log=log, battle=bool(bf_path)), view=view)
            except Exception:
                pass

        # ── Fin ──
        boss = card_boss_get(bid)
        if boss["status"] == "fighting":   # cap de tours atteint
            card_boss_set_status(bid, "wiped")
            boss = card_boss_get(bid)
        await _finish(bot, bid, msg, view, log, boss["status"] == "defeated")
    except Exception as e:
        print(f"[boss] run err: {e!r}")


async def _finish(bot, bid, msg, view, log, victory):
    boss = card_boss_get(bid)
    parts = boss_participants_list(bid)
    ch = bot.get_channel(int(boss["channel_id"]))

    boss_event_add(bid, "end", {"victory": bool(victory)})
    # Affiche d'abord le resultat sur l'embed de combat, puis laisse le temps de lire
    log.append("🎉 **Boss vaincu !**" if victory else "💀 **L'équipe est anéantie.**")
    try:
        await msg.edit(content=("🎉 **Victoire !**" if victory else "💀 **Défaite…**"),
                       embed=build_boss_embed(bot, boss, log=log, battle=True), view=view)
    except Exception:
        pass
    await asyncio.sleep(6)

    # Supprime l'embed de combat de base
    try:
        await msg.delete()
    except Exception:
        try:
            for c in view.children:
                c.disabled = True
            await msg.edit(view=view)
        except Exception:
            pass

    if not ch:
        return

    def _is_dummy(uid):
        return str(uid).startswith("dummy_")
    real_parts = [p for p in parts if not _is_dummy(p["user_id"])]
    mentions = " ".join(f"<@{p['user_id']}>" for p in real_parts) or "—"

    if victory:
        from database import (essence_reward_add, card_get, user_item_add, roll_give_user)
        tier = boss["tier"]
        # Rareté de la carte AVATAR affrontée -> determine la recompense "carte"
        avatar_card = card_get(boss["card_id"]) if boss.get("card_id") else None
        avatar_rar = (avatar_card or {}).get("rarity") or _tier_loot_rarity(tier)
        # Tri par degats decroissants -> le 1er = MVP
        winners = sorted([p for p in real_parts if p["damage"] > 0],
                         key=lambda x: -x["damage"])
        # Hook XP guilde : chaque guilde presente (parmi vainqueurs) gagne l'XP du
        # tier UNE fois (pas par membre). Boss = levier principal avec le roll.
        try:
            from database import get_guild_config as _ggc, guild_of_user as _gou, guild_add_xp as _gax
            _bxp = int(_ggc().get("xp", {}).get("boss", {}).get(str(tier), 0))
            if _bxp:
                _seen_g = set()
                for p in winners:
                    gg = _gou(p["user_id"])
                    if gg and gg["id"] not in _seen_g:
                        _seen_g.add(gg["id"]); _gax(gg["id"], _bxp)
        except Exception as e:
            print(f"[boss guild xp] {e}")
        # Quetes de guilde (boss) : 1 par participant vainqueur
        try:
            from database import guild_quest_progress as _gqp
            for p in winners:
                _gqp(p["user_id"], "boss", 1)
        except Exception as e:
            print(f"[boss guild quest] {e}")
        # Essences : base + part de degats, CAPPÉ par tier (T5 = 5000 max, hors bonus roue)
        _ESS_CAP = {1: 800, 2: 1500, 3: 2500, 4: 3500, 5: 5000}
        cap = _ESS_CAP.get(tier, 1000)
        loot_lines = []
        web_rewards = []   # version structuree pour le live dashboard
        for idx, p in enumerate(winners):
            base_ess = min(cap, tier * 100 + p["damage"] // 1000)
            # Bonus loot de guilde (+%) selon le palier du niveau de sa guilde
            _bpct = 0
            try:
                from database import guild_perks_for_user as _gpfu
                _bpct = int((_gpfu(p["user_id"]) or {}).get("boss_pct", 0))
            except Exception:
                _bpct = 0
            if _bpct:
                base_ess = int(base_ess * (1 + _bpct / 100))
            ess = essence_reward_add(p["user_id"], base_ess)
            parts_loot = [f"+{_fmt(ess)} ✨"]
            web_items = [f"✨ +{_fmt(ess)} essences"]   # emojis unicode pour le web
            # 1. Recompense carte selon la rareté de l'avatar
            if avatar_rar == "secret":
                user_item_add(p["user_id"], "golden_roll", 1)
                parts_loot.append(f"{_cemoji(bot, 'goldenroll', '🌈')} **Golden Roll**")
                web_items.append("🌈 Golden Roll")
            elif avatar_rar == "mythic":
                user_item_add(p["user_id"], "mythic_fragment", 1)
                parts_loot.append("🔴 **Fragment Mythic**")
                web_items.append("🔴 Fragment Mythic")
            elif avatar_card:
                user_card_add(p["user_id"], avatar_card["id"])
                parts_loot.append(f"🎴 **{avatar_card['name']}** {RARITY_HINT.get(avatar_rar,'')}")
                web_items.append(f"🎴 {avatar_card['name']} {RARITY_HINT.get(avatar_rar,'')}")
            # 2. Rolls bonus selon le tier (+ bonus loot guilde)
            n_rolls = _boss_roll_reward(tier)
            if _bpct and n_rolls:
                n_rolls = int(round(n_rolls * (1 + _bpct / 100)))
            if n_rolls:
                roll_give_user(p["user_id"], n_rolls)
                parts_loot.append(f"{_cemoji(bot, 'roll', '🎟️')} **+{n_rolls} rolls**")
                web_items.append(f"🎟️ +{n_rolls} rolls")
            crown = "👑 " if idx == 0 else "▫️ "
            loot_lines.append(f"{crown}<@{p['user_id']}> _(dégâts {_fmt(p['damage'])})_\n"
                              f"　→ " + " · ".join(parts_loot))
            web_rewards.append({"name": p["name"], "dmg": int(p["damage"]),
                                "mvp": idx == 0, "items": web_items})
        reward_hdr = {
            "secret": f"{_cemoji(bot, 'goldenroll', '🌈')} Avatar secret → **Golden Roll** pour tous",
            "mythic": "🔴 Avatar mythic → **Fragment Mythic** pour tous",
        }.get(avatar_rar, f"🎴 Carte : **{(avatar_card or {}).get('name','?')}** {RARITY_HINT.get(avatar_rar,'')}")
        # Recompenses pour le live dashboard (header sans markdown/emojis custom)
        web_hdr = {
            "secret": "🌈 Avatar secret → Golden Roll pour tous",
            "mythic": "🔴 Avatar mythic → Fragment Mythic pour tous",
        }.get(avatar_rar, f"🎴 Carte : {(avatar_card or {}).get('name','?')} {RARITY_HINT.get(avatar_rar,'')}")
        boss_event_add(bid, "rewards", {"header": web_hdr, "boss": boss["name"],
                                        "tier": tier, "players": web_rewards})
        embed = discord.Embed(
            title=f"🎉 {boss['name']} vaincu ! (Tier {tier})",
            description=f"Bravo {mentions} !\n{reward_hdr}\n\n**🎁 Butin (par dégâts) :**\n"
                        + ("\n".join(loot_lines) or "—")
                        + "\n\n_Fragments & Golden Rolls : voir_ `/cardinventory`.",
            color=0x4ade80)
        await ch.send(content=mentions, embed=embed,
                       allowed_mentions=discord.AllowedMentions(users=True))
    else:
        embed = discord.Embed(
            title=f"💀 Défaite contre {boss['name']}",
            description=f"L'équipe ({mentions}) a été anéantie. Le boss survit. Pas de butin.",
            color=0xff3d57)
        await ch.send(content=mentions, embed=embed,
                       allowed_mentions=discord.AllowedMentions(users=True))


RARITY_HINT = {"epic": "🟣", "legendary": "🟠", "mythic": "🔴", "secret": "🌈"}
