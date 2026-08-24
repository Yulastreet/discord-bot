"""Smoke test post-migration i18n : compile tout, importe les modules critiques,
charge chaque commande, verifie que le catalogue resout, parse tous les templates.

Usage: python scripts/i18n_smoke.py
"""
import os
import sys
import glob
import py_compile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

fails = []


def step(label, fn):
    try:
        fn()
        print(f"[OK]   {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        fails.append(label)


def compile_all():
    bad = []
    for pat in ("*.py", "commandes/*.py", "services/*.py", "tasks/*.py",
                "web_app/routes/*.py", "duel/*.py", "cards/*.py", "scripts/*.py"):
        for f in glob.glob(pat):
            try:
                py_compile.compile(f, doraise=True)
            except py_compile.PyCompileError as e:
                bad.append(f"{f}: {e.msg.splitlines()[-1] if e.msg else e}")
    if bad:
        raise RuntimeError(f"{len(bad)} fichier(s) ne compilent pas:\n  " + "\n  ".join(bad[:15]))


def import_core():
    import database  # noqa: F401
    import services.i18n  # noqa: F401


def import_web():
    import web  # noqa: F401


def catalog_ok():
    from services.i18n import get_catalog, available_locales
    langs = available_locales()
    cat = get_catalog("en")
    if not cat:
        raise RuntimeError("catalogue anglais vide")
    print(f"       locales={langs}  cles_en={len(cat)}")


def keys_resolve():
    """Toute cle utilisee dans le code doit exister au catalogue."""
    import re
    from services.i18n import get_catalog
    cat = get_catalog("en")
    rx = re.compile(r'\b(?:ti|t)\(\s*(?:[A-Za-z_][\w.]*\s*,\s*)?["\']([a-z0-9_]+\.[a-z0-9_.]+)["\']')
    missing = {}
    for pat in ("*.py", "commandes/*.py", "services/*.py", "tasks/*.py",
                "web_app/routes/*.py", "duel/*.py", "cards/*.py", "templates/*.html"):
        for f in glob.glob(pat):
            try:
                src = open(f, encoding="utf-8").read()
            except Exception:
                continue
            for m in rx.finditer(src):
                if m.group(1) not in cat:
                    missing.setdefault(m.group(1), f)
    if missing:
        lines = [f"{k}  <- {v}" for k, v in sorted(missing.items())[:20]]
        raise RuntimeError(f"{len(missing)} cle(s) manquante(s):\n  " + "\n  ".join(lines))


def templates_parse():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("templates"))
    env.globals["t"] = lambda k, **kw: k
    env.globals["current_locale"] = lambda: "en"
    env.filters["bg_display_name"] = lambda v: v
    bad = []
    for f in sorted(os.path.basename(p) for p in glob.glob("templates/*.html")):
        try:
            env.get_template(f)
        except Exception as e:
            bad.append(f"{f}: {type(e).__name__}: {e}")
    if bad:
        raise RuntimeError(f"{len(bad)} template(s) cassé(s):\n  " + "\n  ".join(bad[:15]))


def commands_load():
    """Charge l'arbre de commandes hors gateway : detecte doublons de noms et
    parametres invalides (Discord refuse les noms non conformes)."""
    import discord
    from discord.ext import commands as dcommands
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = dcommands.Bot(command_prefix="!", intents=intents)
    loaded, errors = [], []
    import importlib
    for mod_name, fn_name in [
        ("commandes.moderation", "setup_moderation_commands"),
        ("commandes.moderation_pro", "setup_mod_commands"),
        ("commandes.tickets", "setup_ticket_commands"),
        ("commandes.giveaway", "setup_giveaway_commands"),
        ("commandes.rolereaction", "setup_rolereaction_commands"),
    ]:
        try:
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name, None)
            if fn is None:
                continue
            try:
                fn(bot)
            except TypeError:
                fn(bot, {})
            loaded.append(mod_name)
        except Exception as e:
            errors.append(f"{mod_name}: {type(e).__name__}: {e}")
    names = [c.name for c in bot.tree.get_commands()]
    dupes = {n for n in names if names.count(n) > 1}
    print(f"       modules charges={len(loaded)} commandes={len(names)}")
    if dupes:
        errors.append(f"noms de commandes en double: {sorted(dupes)}")
    if errors:
        raise RuntimeError("\n  ".join(errors))


step("compilation de tous les .py", compile_all)
step("import database + services.i18n", import_core)
step("import web (dashboard)", import_web)
step("catalogue i18n charge", catalog_ok)
step("toutes les cles i18n resolvent", keys_resolve)
step("tous les templates Jinja parsent", templates_parse)
step("chargement de commandes Discord", commands_load)

print()
if fails:
    print(f"=== {len(fails)} ETAPE(S) EN ECHEC: {', '.join(fails)} ===")
    sys.exit(1)
print("=== SMOKE TEST OK ===")
