# Product

## Register

product

## Users

Admin solo. Une seule personne utilise ce dashboard : le développeur/propriétaire du bot Discord. Sessions principalement longues, le soir ou la nuit, sur ordinateur fixe (écran 24"+). Check rapides occasionnels depuis mobile. L'écran est utilisé indifféremment en ambiance lumineuse de jour et sombre de nuit, donc le thème doit fonctionner dans les deux conditions sans recalibrage mental.

## Product Purpose

Outil de monitoring et d'édition graphique des données SQLite du bot Discord. Remplace l'édition manuelle de la base via CLI. Le dashboard centralise quatre domaines : profils duel (stats combat, TookCoins, V/D, points), boutique de sabres (CRUD complet avec rareté, prix, capacité spéciale), réactions automatiques (mapping user → emoji), et statistiques globales d'XP de messagerie.

Succès : modifier n'importe quelle donnée en moins de trois clics, sans jamais douter de l'état actuel ni avoir à recharger pour vérifier.

## Brand Personality

Trois mots : **moderne, esthétique, zen.**

Voix factuelle, brève, jamais bavarde. Aucune phrase de félicitation système ("Bravo !", "Génial !"), aucun emoji décoratif dans la copie produit (les emojis sont réservés aux contenus métier comme les noms de sabres). Le ton est celui d'un outil de pro qui se respecte, pas celui d'une app grand public qui veut plaire.

## Anti-references

- **SaaS générique bleu/violet/cream.** Bootstrap par défaut, shadcn brut, Tailwind UI sans personnalisation, MUI standard. Tout ce qui sent "stack par défaut".
- **Stripe-light corporate.** La beauté trop polie, gradient bleu vers violet, motion qui se prend trop au sérieux. Inspiration acceptée pour la rigueur typo, refusée pour la palette et l'âme.
- **Gaming / RGB / néon agressif.** Pas de cyber, pas de glow electric, pas de bordures lumineuses qui pulsent. Le sujet (duel de sabres laser) ne dicte pas l'esthétique.
- **Tout ce qui crie "bot Discord coloré".** Pas de palette Discord blurple, pas de réutilisation de leur design language. C'est un outil personnel, pas une extension Discord.
- **Glassmorphism décoratif partout.** Le blur est autorisé uniquement sur modals et overlays (rare et purposeful). Jamais sur les cards de contenu, jamais comme effet de fond systématique.

## Design Principles

1. **Densité confortable.** Tableaux et formulaires assez denses pour voir beaucoup de données d'un coup, mais avec assez d'air pour que l'œil ne fatigue pas en session longue. Air, pas vide. Densité, pas étouffement.

2. **Édition permanente, save explicite.** Les champs éditables ressemblent à des champs éditables tout le temps, pas seulement après un clic. L'utilisateur n'a jamais à deviner ce qui est modifiable. Le save est volontaire et clair.

3. **Mêmes patterns partout.** Une boutique de sabres, un éditeur de profil et une liste de réactions partagent les mêmes composants visuels (cards, boutons, inputs, badges). Aucune surprise visuelle entre les pages.

4. **Mode sombre primaire, mode clair jumeau strict.** Le mode sombre est conçu en premier (usage principal nocturne). Le mode clair est ensuite calibré pour conserver les mêmes hiérarchies, proportions, et ratios de contraste, sans réinventer la maquette.

5. **Le motion sert le sens.** Les transitions soulignent un changement d'état (un sabre devient équipé, un profil est sauvegardé). Aucun mouvement décoratif. `prefers-reduced-motion` coupe tout sauf les changements d'état strictement nécessaires.

## Accessibility & Inclusion

- **WCAG AA minimum** sur les deux thèmes. Tous les textes >= 4.5:1 contre leur fond, tous les éléments interactifs >= 3:1.
- **Pas de besoin daltonien spécifique** mais aucune information ne doit reposer uniquement sur la couleur (toujours doublée d'un libellé, d'une icône ou d'une position).
- **`prefers-reduced-motion`** strictement respecté : pas de parallax, pas de transitions de page, pas de scroll snap pour ces utilisateurs. Les changements d'état restent visibles via opacité instantanée.
- **Tabulation clavier** complète sur tous les formulaires et tableaux (focus visible, ordre logique).
