# Changelog

Toutes les évolutions notables de ce projet sont consignées dans ce fichier.
Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le projet suit
le [versionnage sémantique](https://semver.org/lang/fr/).

## [2.0.0] - 2026-11-21

Refonte de l'interface et identification plus claire des agents. Les formats et emplacements des
fichiers, les conversions et les sauvegardes sont inchangés.

### Ajouté

- Bandeau supérieur avec champ de recherche (Ctrl+F), bouton de thème et bouton « Relire ».
- Thème clair, sombre ou automatique (suit le réglage de Windows), avec barre de titre Windows
  assortie ; texte net sur les écrans haute résolution.
- Panneau « Détails » à droite de la liste : état, commande, arguments, variables d'environnement,
  URL, en-têtes, agents où le serveur est aussi présent et JSON tel qu'il est écrit dans le fichier.
  Les valeurs secrètes (clés, jetons, mots de passe, en-tête Authorization) sont masquées ; la case
  « Afficher les secrets » permet de les voir.
- Menu contextuel (clic droit) et raccourcis clavier : Ctrl+N, Entrée/F2, Espace, Ctrl+D, Ctrl+T,
  Suppr, Ctrl+V, Ctrl+E, Ctrl+F, Échap, F5. Liste dans Aide › Raccourcis clavier.
- Vue d'ensemble : infobulle sur chaque en-tête de colonne indiquant quel logiciel lit ce fichier ;
  clic droit sur une case pour activer, désactiver, copier ou « Ouvrir dans » un agent ; symboles
  ✔ actif, ○ désactivé, ⚠ incomplet.
- Fenêtre d'ajout/modification : bouton « Tester » qui lance le serveur avec les valeurs saisies
  avant l'enregistrement, erreurs affichées dans la fenêtre, Ctrl+Entrée pour enregistrer.
- « Copier vers… » : boutons Tout cocher / Tout décocher.
- « Restaurer une sauvegarde » : aperçu du contenu de chaque sauvegarde avant restauration.
- Encadré « Ce fichier est lu par » pour chaque agent, listant les logiciels concernés, avec une
  précision quand une confusion est possible (VS Code · GitHub Copilot ≠ extension Claude Code ou
  Cline ; Copilot CLI ≠ chat Copilot de VS Code). Infobulle équivalente au survol de la barre
  latérale.
- Aide › « Claude Code ou Claude Desktop : quel fichier ? ».
- Menu Aide › À propos.
- Taille de la fenêtre et thème mémorisés dans `reglages.json`.

### Modifié

- Barre latérale regroupée par rubriques (« Claude (Anthropic) », « Éditeurs de code »,
  « Extensions VS Code », « Agents en ligne de commande », « Autres ») avec pastille d'état
  (vert : serveurs présents ; anneau gris : aucun serveur ou agent non détecté ; rouge : fichier
  illisible) et nombre de serveurs.
- « Claude Code » devient « Claude Code · CLI, VS Code, Desktop » : un seul fichier
  (`%USERPROFILE%\.claude.json`) lu par la commande `claude` dans un terminal, l'extension Claude
  Code de VS Code et l'onglet « Code » de Claude Desktop.
- « Claude Desktop (chat) » devient « Claude Desktop · Chat » : uniquement l'onglet « Chat ».
- Autres libellés : « Codex (OpenAI) », « VS Code · GitHub Copilot », « Cline », « Roo Code »,
  « Kilo Code ».
- Agent › Redémarrer… pour Claude Code explique comment relancer chaque usage (terminal : relancer
  `claude` ou `/mcp` ; VS Code : recharger la fenêtre) et propose de redémarrer Claude Desktop pour
  l'onglet Code.
- Le redémarrage d'une application s'exécute en arrière-plan : l'interface ne se fige plus.
- « Copier vers… » : agents regroupés par rubrique.
- Type de serveur choisi par boutons segmentés dans la fenêtre d'ajout/modification.
- Boutons activés seulement quand l'action est possible.
- L'exécutable devient `Gestionnaire-MCP-v2.0.exe` (configuration PyInstaller
  `Gestionnaire-MCP-v2.0.spec`).

### Corrigé

- La fenêtre « Tester » transmet son journal par une file lue par l'interface : il ne peut plus
  rester vide quand le test tourne dans un thread séparé.

## [1.0.0] - 2026-11-15

Première version publique.

### Ajouté

- Application de bureau Tkinter de gestion des serveurs MCP pour 15 agents de codage :
  Claude Code, Claude Desktop (chat), OpenCode, Codex CLI, Gemini CLI, Qwen Code,
  VS Code (GitHub Copilot), GitHub Copilot CLI, Cursor, Windsurf, Cline, Roo Code, Kilo Code,
  Kiro et LM Studio.
- Détection automatique des agents installés (fichier de configuration, dossier ou commande
  présents) et option pour afficher aussi les agents non détectés.
- Lecture et écriture limitées à la clé des serveurs MCP : le reste du fichier est conservé.
  Prise en charge du JSON, du JSONC (commentaires et virgules finales) et du TOML (Codex CLI).
- Écriture atomique et sauvegarde automatique avant chaque modification (30 versions par agent),
  avec restauration depuis l'application.
- Vue d'ensemble serveurs × agents : activation, désactivation ou copie par double-clic.
- Ajout et modification d'un serveur par formulaire (local : commande, arguments, variables
  d'environnement, dossier de travail ; distant : URL HTTP/SSE et en-têtes) ou par JSON.
- Conversion automatique entre les formats des agents lors des copies et des imports
  (OpenCode `local/remote`, `httpUrl` Gemini/Qwen, `serverUrl` Windsurf, sections TOML Codex,
  passerelle `npx -y mcp-remote` pour les URL dans Claude Desktop…).
- Désactivation native (`enabled` / `disabled`) lorsque l'agent la prend en charge, sinon mise
  de côté du serveur dans les réglages de l'application.
- Import de JSON collé depuis une documentation (formats `mcpServers`, `servers`, `mcp`,
  `mcp_servers`, `context_servers`, serveur seul) et export des serveurs d'un agent en JSON.
- Test d'un serveur : handshake MCP `initialize` + `tools/list` pour un serveur local,
  requête `initialize` en HTTP pour un serveur distant.
- Redémarrage, sous Windows, des applications graphiques prises en charge (Claude Desktop,
  VS Code, Cursor, Windsurf, Kiro, LM Studio).
- Emplacement de fichier personnalisable par agent et liste des emplacements de tous les agents.
- Avertissements : fichiers illisibles, anciens fichiers Claude Desktop ignorés, fichiers
  OpenCode fusionnés, commentaires JSONC qui seront perdus.
- Script `build.bat` et fichier `.spec` PyInstaller pour produire un exécutable Windows unique.
