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
    boss_participant_update, compute_player_combat_stats, element_matchup,
    card_pick_random_exact_rarity, card_get, card_get_by_name, currency_add,
    user_card_add, user_card_count_owned, CARD_ELEMENT_LABELS, element_weaknesses,
)
import time as _t

_RECRUIT_SECONDS = 120     # delai de combat apres le 1er joueur
_JOIN_EXPIRE = 900         # si personne ne rejoint, le boss disparaît (15 min)
_QUICK_START_AT = 5        # nb de joueurs qui declenche le demarrage rapide
_QUICK_SECONDS = 30        # delai du demarrage rapide
_TURN_DELAY = 4.8          # secondes entre 2 tours auto
_MAX_TURNS = 60
_BOSS_RATIO = 0.5          # le boss frappe a 50% de son atk

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

# Aptitudes de combat
_APT_LABELS = {"berserker": "Berserker", "support": "Support"}
_APT_EMOJI = {"berserker": "🩸", "support": "💚"}
_BERSERK_MULT = 1.15      # dernier coup du berserker
_SUPPORT_HEAL = 0.20      # % PV max rendu par soin
_SUPPORT_TRIGGER = 0.20   # seuil de PV qui declenche le soin
_SUPPORT_MAX = 2          # nb max de soins par combat


def _apt_badge(apt):
    e = _APT_EMOJI.get(apt)
    return f" {e}" if e else ""


def _build_battlefield(bid):
    """Compose le champ de bataille sur le fond bossfightbg.png : cartes des
    joueurs en haut, boss en bas, VS au milieu. Retourne le chemin local ou None."""
    import os
    from PIL import Image, ImageDraw, ImageFont
    from services.card_render import _ROOT, _load_base
    try:
        boss = card_boss_get(bid)
        parts = boss_participants_list(bid)
        bg_path = os.path.join(_ROOT, "assets", "cardrelated", "bossfightbg.png")
        if os.path.exists(bg_path):
            canvas = Image.open(bg_path).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (1672, 941), (24, 16, 32, 255))
        W, H = canvas.size
        d = ImageDraw.Draw(canvas)

        def _font(sz):
            for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"):
                if os.path.exists(p):
                    try: return ImageFont.truetype(p, sz)
                    except Exception: pass
            return ImageFont.load_default()

        def _label(cx, y, text, fsz=26):
            f = _font(fsz)
            tw = d.textlength(text, font=f)
            d.text((cx - tw / 2 + 2, y + 2), text, font=f, fill=(0, 0, 0, 220))
            d.text((cx - tw / 2, y), text, font=f, fill=(255, 255, 255, 255))

        # Cartes joueurs (haut, max 5)
        pw, ph = 210, 315
        gap = 24
        n = min(5, len(parts))
        if n:
            total_w = n * pw + (n - 1) * gap
            x0 = (W - total_w) // 2
            for i, p in enumerate(parts[:5]):
                img = _load_base(int(p["card_id"]), None) if p.get("card_id") else None
                x = x0 + i * (pw + gap)
                if img is not None:
                    canvas.paste(img.resize((pw, ph), Image.LANCZOS), (x, 40), img.resize((pw, ph)).convert("RGBA"))
                _label(x + pw / 2, 40 + ph + 6, (p["name"] or "")[:16], 22)

        # VS au centre
        _label(W / 2, H / 2 - 50, "⚔ VS", 70)

        # Boss (bas centre, plus grand)
        bw, bh = 300, 450
        bx = (W - bw) // 2
        by = H - bh - 30
        bimg = None
        if boss.get("image_url") and str(boss["image_url"]).startswith("http"):
            try:
                import io, urllib.request
                req = urllib.request.Request(boss["image_url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    bimg = Image.open(io.BytesIO(r.read())).convert("RGBA")
            except Exception:
                bimg = None
        if bimg is not None:
            rb = bimg.resize((bw, bh), Image.LANCZOS)
            canvas.paste(rb, (bx, by), rb)
        _label(W / 2, by - 36, (boss.get("name") or "Boss")[:24], 30)

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


def _small_bar(bot, cur, mx, segments=8):
    """Mini barre PV des membres : uniquement lifebarfull / lifebarempty."""
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    full = _cemoji(bot, "lifebarfull", "🟥")
    empty = _cemoji(bot, "lifebarempty", "⬛")
    return full * filled + empty * (segments - filled)


def _bar(bot, cur, mx, enraged=False, segments=15):
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    pct = int(round(100 * cur / mx)) if mx > 0 else 0
    # Emojis perso : pleine / déchainée (sections restantes) / vide (sections perdues)
    full = _cemoji(bot, "lifebardechaine" if enraged else "lifebarfull", "🟥")
    empty = _cemoji(bot, "lifebarempty", "⬛")
    return full * filled + empty * (segments - filled) + f"  **{pct}%**"


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
    embed.add_field(name=f"❤️ PV du boss : {_fmt(boss['hp'])} / {_fmt(boss['max_hp'])}",
                    value=_bar(bot, boss['hp'], boss['max_hp'], enraged=_enraged),
                    inline=False)
    # Info (recrutement / résultat) JUSTE SOUS les PV
    info = ""
    if phase_text:
        info = phase_text
    elif boss["status"] == "recruiting":
        if boss.get("start_at"):
            info = (f"🐲 **Recrutement !** Le combat démarre <t:{int(boss['start_at'])}:R>\n"
                    f"(ou {_QUICK_SECONDS} s si **{_QUICK_START_AT}** joueurs).\n"
                    f"🛡️ **Rejoindre** puis ⚙️ **Paramètres de combat**.")
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
        lines = []
        fighting = boss["status"] == "fighting"
        for p in parts[:12]:
            ko = " 💀" if p["hp"] <= 0 else ""
            lines.append(f"{_elem(bot, p['element'])} **{p['name']}**{_apt_badge(p.get('aptitude'))} "
                         f"❤️ {_fmt(max(0,p['hp']))} · 🗡️ {_fmt(p['atk'])}{ko}")
            if fighting:
                lines.append(_small_bar(bot, p["hp"], p.get("max_hp") or p["hp"]))
        embed.add_field(name=f"🛡️ Équipe ({len(parts)})", value="\n".join(lines), inline=False)
    if log:
        embed.add_field(name="📜 Combat", value="\n".join(log[-4:]), inline=False)
    # Image : battlefield pendant le combat, sinon carte du boss durant le recrutement
    if battle:
        embed.set_image(url="attachment://battle.png")
    elif boss["status"] == "recruiting" and boss.get("image_url") and str(boss["image_url"]).startswith("http"):
        embed.set_image(url=boss["image_url"])
    return embed


class JoinView(discord.ui.View):
    def __init__(self, boss_id):
        super().__init__(timeout=None)
        self.boss_id = boss_id

    @discord.ui.button(label="Rejoindre", style=discord.ButtonStyle.success, emoji="🛡️")
    async def join(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True)
            return
        uid = interaction.user.id
        if boss_participant_get(self.boss_id, uid):
            await interaction.response.send_message("Tu es déjà dans l'équipe.", ephemeral=True)
            return
        stats = compute_player_combat_stats(uid)
        dcard = _default_card(uid)
        delem = (dcard.get("element") if dcard else None) or "eclat"
        boss_participant_add(self.boss_id, uid, interaction.user.display_name,
                             delem, stats["hp"], stats["atk"],
                             card_id=(dcard["id"] if dcard else None))
        # 1er joueur -> demarre le timer de 2 min
        if not boss.get("start_at"):
            card_boss_set_start(self.boss_id, _t.time() + _RECRUIT_SECONDS)
        await interaction.response.send_message(
            "🛡️ Tu as rejoint ! Élément par défaut = ta carte vedette.\n"
            "Ouvre **⚙️ Paramètres de combat** pour choisir ta carte et ton aptitude.", ephemeral=True)
        try:
            await interaction.message.edit(embed=build_boss_embed(interaction.client, boss), view=self)
        except Exception:
            pass

    @discord.ui.button(label="Paramètres de combat", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def settings(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True)
            return
        p = boss_participant_get(self.boss_id, interaction.user.id)
        if not p:
            await interaction.response.send_message("Rejoins d'abord (🛡️).", ephemeral=True)
            return
        cur_apt = _APT_LABELS.get(p.get("aptitude"), "aucune")
        await interaction.response.send_message(
            "⚙️ **Paramètres de combat**\n"
            f"🎴 Carte : utilise le bouton ci-dessous.\n"
            f"Aptitude actuelle : **{cur_apt}**\n\n"
            "**🩸 Berserker** — à sa mort, reste à 1 PV, inflige x1.15 puis meurt.\n"
            "**💚 Support** — sous 20% PV, se soigne de 20% (2 fois max).",
            view=_SettingsView(self.boss_id), ephemeral=True)


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


class _SettingsView(discord.ui.View):
    def __init__(self, boss_id):
        super().__init__(timeout=300)
        self.boss_id = boss_id

    @discord.ui.select(placeholder="Choisir une aptitude…", min_values=1, max_values=1,
                       options=[
                           discord.SelectOption(label="Berserker", value="berserker", emoji="🩸",
                                                description="À la mort : 1 PV, dernier coup x1.15, puis meurt"),
                           discord.SelectOption(label="Support", value="support", emoji="💚",
                                                description="Sous 20% PV : soin de 20% (2 fois max)"),
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
        lbl = _APT_LABELS.get(val, "aucune")
        await interaction.response.send_message(f"✅ Aptitude : **{lbl}**.", ephemeral=True)
        await _refresh_boss_msg(interaction.client, self.boss_id)

    @discord.ui.button(label="Choisir ma carte", style=discord.ButtonStyle.primary, emoji="🎴")
    async def choose_card(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True); return
        if not boss_participant_get(self.boss_id, interaction.user.id):
            await interaction.response.send_message("Rejoins d'abord (🛡️).", ephemeral=True); return
        await interaction.response.send_modal(_ChooseCardModal(self.boss_id))


class _ChooseCardModal(discord.ui.Modal, title="Choisir ma carte de combat"):
    nom = discord.ui.TextInput(label="Nom de la carte (que tu possèdes)",
                                placeholder="ex: Goku", required=True, max_length=100)

    def __init__(self, boss_id):
        super().__init__()
        self.boss_id = boss_id

    async def on_submit(self, interaction):
        card = card_get_by_name(str(self.nom.value).strip())
        uid = interaction.user.id
        if not card:
            await interaction.response.send_message("Carte introuvable.", ephemeral=True); return
        if user_card_count_owned(uid, card["id"]) <= 0:
            await interaction.response.send_message(f"Tu ne possèdes pas **{card['name']}**.", ephemeral=True); return
        elem = card.get("element") or "eclat"
        boss_participant_update(self.boss_id, uid, element=elem, card_id=card["id"])
        await interaction.response.send_message(
            f"🎴 Tu combattras avec **{card['name']}** "
            f"({_elem(interaction.client, elem)} {CARD_ELEMENT_LABELS.get(elem,'?')}).", ephemeral=True)
        await _refresh_boss_msg(interaction.client, self.boss_id)


async def spawn_boss(bot, guild_id, channel_id, tier=1):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return None
    tier = max(1, min(5, int(tier)))
    cfg = BOSS_TIERS[tier]
    # Avatar du boss : rareté aléatoire dans la fourchette du tier
    rng = list(_TIER_RANGE.get(tier, ["epic"]))
    random.shuffle(rng)
    avatar = None
    for _r in rng:
        avatar = card_pick_random_exact_rarity(_r)
        if avatar:
            break
    if not avatar:
        avatar = card_pick_random_exact_rarity("epic")
    name = avatar["name"] if avatar else "Entité inconnue"
    element = (avatar.get("element") if avatar else None) or random.choice(list(CARD_ELEMENT_LABELS.keys()))
    img = avatar.get("image_url") if avatar else None
    # start_at non defini : le timer ne demarre qu'au 1er joueur
    bid = card_boss_create(guild_id, channel_id, name, element, tier, cfg["hp"], cfg["atk"],
                           image_url=img, start_at=None)
    card_boss_set_status(bid, "recruiting")
    boss = card_boss_get(bid)
    embed = build_boss_embed(bot, boss)
    view = JoinView(bid)
    msg = await channel.send(content="🐲 **Un boss est apparu !**", embed=embed, view=view)
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
        boss_participant_add(bid, f"dummy_{i+1}", f"Bot {i+1}", elem, hp, atk,
                             card_id=(card["id"] if card else None))


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
            while True:
                await asyncio.sleep(3)
                boss = card_boss_get(bid)
                if not boss or boss["status"] != "recruiting":
                    return
                parts = boss_participants_list(bid)
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

        # ── Combat automatique ──
        card_boss_set_status(bid, "fighting")
        for ch in view.children:
            ch.disabled = True
        log = ["⚔️ **Le combat commence !**"]
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
        turn = 0
        actor = "party"
        smash_used = False
        enrage_announced = False
        # Etat des aptitudes (en memoire pour la duree du combat)
        heals_used = {}   # uid -> nb de soins support utilisés
        berserk_armed = {p["user_id"] for p in boss_participants_list(bid)
                         if (p.get("aptitude") or "") == "berserker"}
        berserk_dying = set()  # uid maintenus à 1 PV jusqu'à leur dernier coup

        def _boss_hit(p, dmg):
            """Applique des degats a un participant en gerant le sursaut Berserker.
            Retourne (new_hp, ko_bool, berserk_saved_bool)."""
            uid = p["user_id"]
            new_hp = max(0, p["hp"] - dmg)
            saved = False
            if new_hp <= 0 and uid in berserk_armed:
                new_hp = 1
                berserk_armed.discard(uid)
                berserk_dying.add(uid)
                saved = True
            boss_participant_update(bid, uid, hp=new_hp)
            return new_hp, (new_hp <= 0), saved

        def _apply_supports():
            """Soigne les supports sous le seuil (max _SUPPORT_MAX fois)."""
            for p in boss_participants_list(bid):
                if (p.get("aptitude") or "") != "support" or p["hp"] <= 0:
                    continue
                uid = p["user_id"]
                mx = p.get("max_hp") or p["hp"]
                if p["hp"] < mx * _SUPPORT_TRIGGER and heals_used.get(uid, 0) < _SUPPORT_MAX:
                    heal = int(mx * _SUPPORT_HEAL)
                    boss_participant_update(bid, uid, hp=min(mx, p["hp"] + heal))
                    heals_used[uid] = heals_used.get(uid, 0) + 1
                    log.append(f"Tour {turn} · 💚 **{p['name']}** se soigne (+{_fmt(heal)} PV)")

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
                total = 0
                best_eff = 1.0
                for p in alive:
                    m = element_matchup(p["element"], boss["element"])
                    mult = _BERSERK_MULT if p["user_id"] in berserk_dying else 1.0
                    total += max(1, int(p["atk"] * m * mult))
                    best_eff = max(best_eff, m)
                boss_hp = card_boss_apply_damage(bid, total)
                eff = " 🔥" if best_eff > 1 else ""
                log.append(f"Tour {turn} · 🗡️ L'équipe inflige **{_fmt(total)}**{eff}")
                # Les berserkers ayant frappé une dernière fois s'effondrent
                if berserk_dying:
                    names = {p["user_id"]: p["name"] for p in alive}
                    for uid in list(berserk_dying):
                        boss_participant_update(bid, uid, hp=0)
                        berserk_dying.discard(uid)
                        log.append(f"Tour {turn} · 🩸 **{names.get(uid,'?')}** s'effondre après un dernier coup.")
                if boss_hp <= 0:
                    card_boss_set_status(bid, "defeated")
                    break
                actor = "boss"
            elif not smash_used and random.random() < 0.25:
                # Coup special : 1 fois par combat, cible 1 joueur, degats x3
                smash_used = True
                target = random.choice(alive)
                cm = element_matchup(boss["element"], target["element"])
                dmg = max(1, int(boss["atk"] * cm * 3))
                new_hp, dead, saved = _boss_hit(target, dmg)
                ko = " 🩸 **survit à 1 PV !**" if saved else (" 💀 **KO !**" if dead else "")
                log.append(f"Tour {turn} · 💥 **COUP DÉVASTATEUR !** Le boss cible "
                           f"**{target['name']}** : -**{_fmt(dmg)}**{ko}")
                _apply_supports()
                if all(pp["hp"] <= 0 for pp in boss_participants_list(bid)):
                    card_boss_set_status(bid, "wiped")
                    break
                actor = "party"
            else:
                # Le boss frappe TOUTE l'équipe (AoE). Déchaîné (<50% PV) = x1.5
                enraged = boss["hp"] < boss["max_hp"] * 0.5
                rage = 1.25 if enraged else 1.0
                if enraged and not enrage_announced:
                    enrage_announced = True
                    log.append("🔥 **Le boss se déchaîne et inflige 1,25x plus de dégâts !**")
                kos, saves = [], []
                for p in alive:
                    cm = element_matchup(boss["element"], p["element"])
                    dmg = max(1, int(boss["atk"] * cm * rage))
                    new_hp, dead, saved = _boss_hit(p, dmg)
                    if saved:
                        saves.append(p["name"])
                    elif dead:
                        kos.append(p["name"])
                ko_txt = f" · 💀 KO : {', '.join(kos)}" if kos else ""
                save_txt = f" · 🩸 {', '.join(saves)} survit à 1 PV" if saves else ""
                log.append(f"Tour {turn} · 👹 Le boss frappe toute l'équipe : "
                           f"~**{_fmt(int(boss['atk'] * rage))}**{ko_txt}{save_txt}")
                _apply_supports()
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
        tier = boss["tier"]
        rar = _tier_loot_rarity(tier)
        loot_lines = []
        for p in real_parts:
            if p["damage"] <= 0:
                continue
            ess = tier * 150 + p["damage"] // 200
            currency_add(p["user_id"], ess)
            card = card_pick_random_exact_rarity(rar)
            extra = ""
            if card:
                user_card_add(p["user_id"], card["id"])
                extra = f" + **{card['name']}** {RARITY_HINT.get(rar,'')}"
            loot_lines.append(f"<@{p['user_id']}> — +{ess} ✨{extra} _(dégâts {_fmt(p['damage'])})_")
        embed = discord.Embed(
            title=f"🎉 {boss['name']} vaincu !",
            description=f"Bravo {mentions} !\n\n**🎁 Butin :**\n" + ("\n".join(loot_lines) or "—"),
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
