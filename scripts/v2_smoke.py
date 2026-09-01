"""Smoke test Components V2.

Importe les modules, instancie les vues persistantes, serialise tout ce qui peut
l'etre et verifie qu'aucun container ne porte de couleur d'accentuation.

Usage: python scripts/v2_smoke.py
"""
import glob
import importlib
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import discord  # noqa: E402
from discord import ui  # noqa: E402

fails = []


def step(label, fn):
    try:
        fn()
        print(f"[OK]   {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        fails.append(label)


MODULES = []
for pat in ("commandes/*.py", "duel/*.py", "services/*.py", "tasks/*.py"):
    for f in sorted(glob.glob(pat)):
        name = f[:-3].replace("\\", "/").replace("/", ".")
        if name.endswith(".__init__"):
            continue
        MODULES.append(name)

_loaded = {}


def import_all():
    bad = []
    for m in MODULES:
        try:
            _loaded[m] = importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {type(e).__name__}: {e}")
    if bad:
        raise RuntimeError(f"{len(bad)} module(s) n'importent pas:\n  " + "\n  ".join(bad[:10]))


def helper_ok():
    from services.ui_v2 import Panel
    p = Panel("T", "D").field("A", "B", inline=True).footer("f")
    d = p.container().to_component_dict()
    assert d["type"] == 17, "le container n'est pas de type 17"
    assert d.get("accent_color") is None, "une couleur d'accentuation est posee"


def layout_views():
    """Instancie toute LayoutView instanciable sans argument et la serialise."""
    seen, built, errs = set(), 0, []
    for m, mod in _loaded.items():
        for nm, obj in vars(mod).items():
            if not (inspect.isclass(obj) and issubclass(obj, ui.LayoutView) and obj is not ui.LayoutView):
                continue
            key = f"{m}.{nm}"
            if key in seen:
                continue
            seen.add(key)
            try:
                sig = inspect.signature(obj.__init__)
                required = [p for n, p in list(sig.parameters.items())[1:]
                            if p.default is inspect.Parameter.empty
                            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
                if required:
                    continue  # a besoin d'un contexte metier, hors perimetre
                v = obj()
                payload = v.to_components()
                json.dumps(payload)          # doit etre serialisable
                for c in payload:
                    if c.get("type") == 17 and c.get("accent_color") is not None:
                        errs.append(f"{key}: accent_color={c['accent_color']}")
                built += 1
            except Exception as e:
                errs.append(f"{key}: {type(e).__name__}: {e}")
    print(f"       LayoutView instanciees={built} (sur {len(seen)} trouvees)")
    if errs:
        raise RuntimeError(f"{len(errs)} probleme(s):\n  " + "\n  ".join(errs[:12]))


def no_embeds_left():
    import re
    rx = re.compile(r'\bdiscord\.Embed\s*\(')
    bad = []
    for pat in ("commandes/*.py", "duel/*.py", "services/*.py", "tasks/*.py", "*.py"):
        for f in glob.glob(pat):
            if f.replace("\\", "/").startswith("scripts/"):
                continue
            for i, line in enumerate(open(f, encoding="utf-8").read().split("\n"), 1):
                if rx.search(line) and not line.strip().startswith("#"):
                    bad.append(f"{f}:{i}")
    if bad:
        raise RuntimeError(f"{len(bad)} discord.Embed( restant(s): {bad[:8]}")


step("import de tous les modules", import_all)
step("helper ui_v2 (container sans accent)", helper_ok)
step("instanciation + serialisation des LayoutView", layout_views)
step("plus aucun discord.Embed", no_embeds_left)

def imports_resolve():
    """Resout TOUS les `from X import Y` du projet, y compris ceux ecrits a
    l'interieur d'une fonction (invisibles a l'import du module, ils explosent
    seulement quand le chemin de code est emprunte). Attrape les builders
    renommes lors de la migration (ex: make_giveaway_embed -> make_giveaway_panel)."""
    import ast
    bad = []
    for pat in ("commandes/*.py", "duel/*.py", "services/*.py", "tasks/*.py", "*.py"):
        for f in glob.glob(pat):
            fn = f.replace("\\", "/")
            if fn.startswith("scripts/"):
                continue
            try:
                tree = ast.parse(open(f, encoding="utf-8").read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if node.level:                       # import relatif: on saute
                    continue
                root = node.module.split(".")[0]
                if root not in ("commandes", "duel", "services", "tasks", "cards", "database"):
                    continue
                try:
                    mod = importlib.import_module(node.module)
                except Exception as e:
                    bad.append(f"{fn}:{node.lineno} module {node.module} illisible ({type(e).__name__})")
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if not hasattr(mod, alias.name):
                        bad.append(f"{fn}:{node.lineno} `from {node.module} import {alias.name}` -> INTROUVABLE")
    if bad:
        raise RuntimeError(f"{len(bad)} import(s) casse(s):\n  " + "\n  ".join(bad[:15]))


step("tous les imports resolvent (y compris locaux)", imports_resolve)

print()
if fails:
    print(f"=== {len(fails)} ETAPE(S) EN ECHEC: {', '.join(fails)} ===")
    sys.exit(1)
print("=== SMOKE V2 OK ===")
