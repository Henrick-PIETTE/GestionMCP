# Gestionnaire MCP v2.0.0

Nouvelle version du **Gestionnaire MCP**, l'application de bureau Windows qui permet de gérer en un
seul endroit les serveurs MCP de vos agents de codage : interface entièrement redessinée et agents
beaucoup plus faciles à identifier.

## Nouveautés

- **Claude Code clarifié** : « Claude Code » devient « Claude Code · CLI, VS Code, Desktop ». Un seul
  fichier (`%USERPROFILE%\.claude.json`) est lu par la commande `claude` dans un terminal, l'extension
  Claude Code de VS Code et l'onglet « Code » de Claude Desktop. « Claude Desktop (chat) » devient
  « Claude Desktop · Chat » (onglet « Chat » uniquement). Chaque agent affiche un encadré « Ce fichier
  est lu par », et une aide est disponible dans Aide › « Claude Code ou Claude Desktop : quel
  fichier ? ».
- **Interface redessinée** : bandeau avec recherche (Ctrl+F), barre latérale regroupée par rubriques
  avec pastille d'état et nombre de serveurs, thème clair, sombre ou automatique, texte net sur les
  écrans haute résolution.
- **Panneau « Détails »** : commande, arguments, variables, URL, en-têtes, agents où le serveur est
  présent et JSON écrit dans le fichier. Les valeurs secrètes sont masquées (case « Afficher les
  secrets »).
- **Clavier et clic droit** : menu contextuel et raccourcis (Ctrl+N, F2, Espace, Ctrl+D, Ctrl+T,
  Ctrl+V, Ctrl+E, F5…), liste dans Aide › Raccourcis clavier.
- **Vue d'ensemble** : infobulle par colonne indiquant quel logiciel lit le fichier, clic droit sur
  une case, symboles ✔ actif, ○ désactivé, ⚠ incomplet.
- **Test avant enregistrement** dans la fenêtre d'ajout/modification, avec erreurs affichées dans la
  fenêtre.
- **Sauvegardes** : aperçu du contenu avant restauration.
- **Redémarrage en arrière-plan** : l'interface ne se fige plus. Pour Claude Code, l'application
  explique comment relancer chaque usage.
- Taille de la fenêtre et thème mémorisés.

Les formats et emplacements des fichiers, les conversions et les sauvegardes (30 par agent) sont
inchangés.

## Installation

Téléchargez `Gestionnaire-MCP-v2.0.exe` ci-dessous et lancez-le. Aucune installation n'est
nécessaire.

Depuis les sources : Python 3.11 ou plus récent avec Tkinter, puis `python gestion_mcp.py`.

## Limites connues

- Les configurations propres à un projet (`.mcp.json`, `opencode.json` de dossier,
  `.vscode/mcp.json`), les plugins et connecteurs claude.ai, Zed et Continue ne sont pas gérés.
- Le redémarrage automatique des applications n'est disponible que sous Windows ; les agents en
  ligne de commande doivent être relancés manuellement.
- Les commentaires d'un fichier JSONC sont supprimés à la première modification (après
  confirmation, avec sauvegarde).

Le détail des fonctionnalités se trouve dans [CHANGELOG.md](https://github.com/Henrick-PIETTE/GestionMCP/blob/main/CHANGELOG.md).
