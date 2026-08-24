"""Verifie l'etat de la migration i18n.

- Cles t()/ti() utilisees dans le code mais absentes des catalogues -> ERREUR
- Cles definies mais jamais utilisees -> info
- Restes de francais dans les fichiers migres -> avertissement

Usage: python scripts/i18n_check.py [chemin ...]
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from services.i18n import get_catalog, DEFAULT_LOCALE  # noqa: E402

# ti(interaction, "key"...) / t("key", locale...) / t("key") en Jinja
RE_KEYS = re.compile(r'\b(?:ti|t)\(\s*(?:[A-Za-z_][\w.]*\s*,\s*)?["\']([a-z0-9_]+\.[a-z0-9_.]+)["\']')
# Mots francais frequents -> detection de restes
RE_FR = re.compile(
    r'["\'][^"\']*\b(?:le|la|les|une|un|des|du|tu|ton|ta|tes|vous|votre|est|sont|pas|aucun|aucune'
    r'|erreur|salon|serveur|membre|carte|cartes|joueur|niveau|pour|avec|dans|sur|par|ce|cette'
    r'|impossible|introuvable|reussi|echec|supprime|cree|modifie|desactive|active)\b[^"\']*["\']',
    re.IGNORECASE)
RE_ACCENT = re.compile(r'[éèêëàâçùûôîïœ]', re.IGNORECASE)

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".claude", "locales", "venv", ".venv"}


def walk(paths):
    for p in paths:
        full = os.path.join(ROOT, p) if not os.path.isabs(p) else p
        if os.path.isfile(full):
            yield full
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith((".py", ".html")):
                    yield os.path.join(dirpath, fn)


def main():
    targets = sys.argv[1:] or ["commandes", "services", "tasks", "web_app", "templates",
                               "bot.py", "web.py", "database.py"]
    catalog = get_catalog(DEFAULT_LOCALE)
    used, missing, fr_files = {}, {}, {}

    for path in walk(targets):
        rel = os.path.relpath(path, ROOT)
        try:
            src = open(path, encoding="utf-8").read()
        except Exception:
            continue
        for m in RE_KEYS.finditer(src):
            key = m.group(1)
            used.setdefault(key, []).append(rel)
            if key not in catalog:
                missing.setdefault(key, []).append(rel)
        # restes FR : compte les lignes suspectes
        n_fr = 0
        for line in src.splitlines():
            st = line.strip()
            if st.startswith("#") or st.startswith("//"):
                continue
            if RE_ACCENT.search(line) or RE_FR.search(line):
                n_fr += 1
        if n_fr:
            fr_files[rel] = n_fr

    print(f"catalogue    : {len(catalog)} cles definies ({DEFAULT_LOCALE})")
    print(f"cles usitees : {len(used)}")
    if missing:
        print(f"\n[ERREUR] {len(missing)} cle(s) utilisee(s) mais ABSENTE(S) du catalogue :")
        for k, files in sorted(missing.items())[:60]:
            print(f"  {k}   <- {files[0]}")
        if len(missing) > 60:
            print(f"  ... +{len(missing) - 60}")
    else:
        print("\n[OK] Toutes les cles utilisees existent.")

    unused = sorted(set(catalog) - set(used))
    if unused:
        print(f"\n[info] {len(unused)} cle(s) definie(s) mais non utilisee(s)")

    if fr_files:
        total = sum(fr_files.values())
        print(f"\n[reste FR] {len(fr_files)} fichier(s), {total} ligne(s) suspecte(s) :")
        for f, n in sorted(fr_files.items(), key=lambda x: -x[1])[:40]:
            print(f"  {n:5d}  {f}")
        if len(fr_files) > 40:
            print(f"  ... +{len(fr_files) - 40} fichiers")
    else:
        print("\n[OK] Aucun reste de francais detecte.")

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
