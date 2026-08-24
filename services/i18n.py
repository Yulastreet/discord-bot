"""Systeme i18n partage bot + dashboard.

Les traductions vivent dans locales/<lang>/<namespace>.json. Chaque fichier est
un dict plat ou imbrique ; les cles sont prefixees par le nom du fichier :
locales/en/moderation.json {"kick": {"no_perm": "..."}} -> cle "moderation.kick.no_perm".

Anglais = langue par defaut et fallback. Ajouter une langue = creer locales/<lang>/
avec les memes cles ; toute cle manquante retombe sur l'anglais.
"""
import json
import os
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCALES_DIR = os.path.join(_ROOT, "locales")

DEFAULT_LOCALE = "en"

_cache: dict[str, dict[str, str]] = {}
_lock = threading.Lock()


def _flatten(d, prefix=""):
    """Aplatit un dict imbrique en cles pointees."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _load_locale(lang):
    """Charge et aplatit tous les namespaces JSON d'une langue."""
    catalog = {}
    ldir = os.path.join(_LOCALES_DIR, lang)
    if not os.path.isdir(ldir):
        return catalog
    for fname in sorted(os.listdir(ldir)):
        if not fname.endswith(".json"):
            continue
        ns = fname[:-5]
        try:
            with open(os.path.join(ldir, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[i18n] load err {lang}/{fname}: {e!r}", flush=True)
            continue
        catalog.update(_flatten(data, ns + "."))
    return catalog


def get_catalog(lang):
    lang = (lang or DEFAULT_LOCALE).split("-")[0].lower()
    with _lock:
        if lang not in _cache:
            _cache[lang] = _load_locale(lang)
        return _cache[lang]


def available_locales():
    """Langues presentes sur disque."""
    if not os.path.isdir(_LOCALES_DIR):
        return [DEFAULT_LOCALE]
    return sorted(d for d in os.listdir(_LOCALES_DIR)
                  if os.path.isdir(os.path.join(_LOCALES_DIR, d)))


def reload_locales():
    """Vide le cache (hot reload apres edition d'un JSON)."""
    with _lock:
        _cache.clear()


def t(key, locale=DEFAULT_LOCALE, **kwargs):
    """Traduit `key` dans `locale`, avec fallback anglais puis la cle elle-meme.

    Les placeholders utilisent la syntaxe str.format : "Hello {name}".
    Un placeholder manquant ne leve pas : on renvoie la chaine brute.
    """
    cat = get_catalog(locale)
    s = cat.get(key)
    if s is None and (locale or DEFAULT_LOCALE) != DEFAULT_LOCALE:
        s = get_catalog(DEFAULT_LOCALE).get(key)
    if s is None:
        print(f"[i18n] MISSING key={key!r} locale={locale!r}", flush=True)
        return key
    if not kwargs:
        return s
    try:
        return s.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return s


# ===== Resolution de langue cote bot =====

def guild_locale(guild_id):
    """Override de langue configure pour un serveur (None si non defini)."""
    if guild_id is None:
        return None
    try:
        from database import guild_setting_get
        val = guild_setting_get(guild_id, "locale", None)
        return val or None
    except Exception:
        return None


def locale_of(interaction):
    """Langue a utiliser pour une interaction : override serveur > client Discord > EN."""
    gid = getattr(getattr(interaction, "guild", None), "id", None)
    forced = guild_locale(gid)
    if forced:
        return forced
    loc = getattr(interaction, "locale", None)
    if loc:
        # discord.Locale -> 'fr', 'en-US', ...
        code = str(getattr(loc, "value", loc)).split("-")[0].lower()
        if code in available_locales():
            return code
    return DEFAULT_LOCALE


def ti(interaction, key, **kwargs):
    """Raccourci : traduit pour une interaction Discord."""
    return t(key, locale_of(interaction), **kwargs)

def universe_label(value, locale=DEFAULT_LOCALE):
    """Display label for a card `universe` value.

    The values stored in DB are historical French strings ("Jeu Video",
    "Film/Serie"); they are kept as-is so existing collections do not split in
    two. Only the displayed label is translated. Unknown values pass through.
    """
    if not value:
        return value
    return t(f"data.universe.{value}", locale) if f"data.universe.{value}" in get_catalog(
        locale or DEFAULT_LOCALE) or f"data.universe.{value}" in get_catalog(DEFAULT_LOCALE) else value
