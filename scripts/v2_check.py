"""Verifie la migration vers les Components V2.

- restes de discord.Embed / embed= / embeds=            -> ERREUR
- couleur d'accentuation posee sur un Container         -> ERREUR (choix produit: pas de barre)
- custom_id disparus par rapport a un commit de ref     -> ERREUR (casse les anciens messages)
- content= combine avec view= sur un envoi V2           -> AVERTISSEMENT (interdit par Discord)

Usage: python scripts/v2_check.py [ref_git]
"""
import ast
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
REF = sys.argv[1] if len(sys.argv) > 1 else "v1-embeds-i18n-en"

TARGETS = ("commandes/*.py", "services/*.py", "tasks/*.py", "duel/*.py", "cards/*.py", "*.py")

def files():
    seen = set()
    for pat in TARGETS:
        for f in glob.glob(pat):
            n = f.replace("\\", "/")
            if n.startswith("scripts/") or n in seen:
                continue
            seen.add(n)
            yield n

errors, warns = [], []

# ---- 1. restes d'embeds ----
RE_EMBED_CTOR = re.compile(r'\bdiscord\.Embed\s*\(')
RE_EMBED_KW   = re.compile(r'\bembeds?\s*=')
for f in files():
    src = open(f, encoding="utf-8").read()
    for i, line in enumerate(src.split("\n"), 1):
        st = line.strip()
        if st.startswith("#"):
            continue
        if RE_EMBED_CTOR.search(line):
            errors.append(f"{f}:{i} discord.Embed( subsiste")
        elif RE_EMBED_KW.search(line) and "embed" not in f:
            # embed=None / embeds=[] sont toleres : c'est le motif qui vide
            # l'embed d'un ancien message V1 remplace par un panel V2.
            if not re.search(r'embeds?\s*=\s*(None|\[\s*\])', line):
                errors.append(f"{f}:{i} argument embed= subsiste -> {st[:70]}")

# ---- 2. couleur d'accentuation ----
RE_ACCENT = re.compile(r'accent_colou?r\s*=\s*(?!None)')
for f in files():
    for i, line in enumerate(open(f, encoding="utf-8").read().split("\n"), 1):
        if RE_ACCENT.search(line):
            errors.append(f"{f}:{i} accent_colour pose (la barre coloree n'est pas voulue)")

# ---- 3. custom_id preserves ----
RE_CID = re.compile(r'custom_id\s*=\s*["\']([^"\']+)["\']')
try:
    tree = subprocess.run(["git", "ls-tree", "-r", "--name-only", REF],
                          capture_output=True, text=True, encoding="utf-8").stdout.split("\n")
except Exception:
    tree = []
for f in [x for x in tree if x.endswith(".py")]:
    old = subprocess.run(["git", "show", f"{REF}:{f}"],
                         capture_output=True, text=True, encoding="utf-8").stdout
    before = set(RE_CID.findall(old))
    if not before:
        continue
    try:
        after = set(RE_CID.findall(open(f, encoding="utf-8").read()))
    except FileNotFoundError:
        errors.append(f"{f} supprime alors qu'il portait des custom_id")
        continue
    lost = before - after
    if lost:
        errors.append(f"{f} custom_id DISPARUS: {sorted(lost)}")

# ---- 4. content= avec view= (interdit sur un message V2) ----
for f in files():
    src = open(f, encoding="utf-8").read()
    for m in re.finditer(r'\.(?:send|send_message|edit|edit_message|followup\.send)\s*\(([^;]{0,400}?)\)', src, re.S):
        seg = m.group(1)
        if "view=" in seg and re.search(r'\bcontent\s*=', seg) and "content=None" not in seg:
            line = src[:m.start()].count("\n") + 1
            warns.append(f"{f}:{line} content= et view= sur le meme envoi (interdit si V2)")

print(f"reference custom_id : {REF}")
print(f"fichiers analyses   : {len(list(files()))}")
print()
if errors:
    print(f"[ERREUR] {len(errors)} probleme(s) bloquant(s):")
    for e in errors[:40]:
        print("  ", e)
    if len(errors) > 40:
        print(f"   ... +{len(errors)-40}")
else:
    print("[OK] aucun embed restant, aucune couleur d'accentuation, tous les custom_id preserves")
if warns:
    print(f"\n[AVERTISSEMENT] {len(warns)} envoi(s) a verifier a la main:")
    for w in warns[:20]:
        print("  ", w)
sys.exit(1 if errors else 0)
