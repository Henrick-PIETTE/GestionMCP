# Gestionnaire MCP v2.0 - Serveur MCP de vos agents de codage

Application de bureau (Python / Tkinter) qui centralise la gestion des **serveurs MCP** déclarés
dans les fichiers de configuration de vos agents de codage : Claude Code, Claude Desktop, OpenCode,
Codex CLI, Gemini CLI, Qwen Code, VS Code (GitHub Copilot), GitHub Copilot CLI, Cursor, Windsurf,
Cline, Roo Code, Kilo Code, Kiro et LM Studio.

Pour chaque agent, l'application lit et écrit **uniquement la clé des serveurs MCP** de son fichier
de configuration ; tout le reste du fichier (modèles, providers, préférences…) est conservé. Une
sauvegarde est faite avant chaque écriture. Les copies d'un agent à l'autre sont converties
automatiquement au format de l'agent cible.

## Prérequis

- **Windows** (les emplacements de fichiers et le redémarrage automatique des applications sont
  prévus pour Windows ; le code contient aussi des emplacements macOS/Linux pour certains agents).
- Pour l'exécutable : rien d'autre.
- Pour lancer depuis les sources :
  - **Python 3.11 ou plus récent** (le module `tomllib`, requis pour lire la configuration TOML de
    Codex CLI, n'existe qu'à partir de 3.11) ;
  - **Tkinter** (inclus dans l'installateur Python officiel pour Windows).
  - Aucune dépendance externe : seule la bibliothèque standard est utilisée.
- Pour compiler l'exécutable : **PyInstaller** (`pip install pyinstaller`).
- Optionnel : **Node.js** (`npx`), nécessaire côté Claude Desktop lorsqu'un serveur distant (URL)
  y est converti en `npx -y mcp-remote <url>`.

## Installation

### Avec l'exécutable

Téléchargez `Gestionnaire-MCP-v2.0.exe` depuis la page des releases GitHub et lancez-le.

### Depuis les sources

```bat
git clone https://github.com/Henrick-PIETTE/GestionMCP.git
cd GestionMCP
python gestion_mcp.py
```

### Compiler l'exécutable

```bat
pip install pyinstaller
build.bat
```

L'exécutable est produit dans `dist\Gestionnaire-MCP-v2.0.exe` (configuration :
`Gestionnaire-MCP-v2.0.spec`, application fenêtrée sans console).

## Agents pris en charge

| Agent | Fichier (Windows) | Clé | Désactivation |
|---|---|---|---|
| Claude Code · CLI, VS Code, Desktop | `%USERPROFILE%\.claude.json` | `mcpServers` | mis de côté* |
| Claude Desktop · Chat | `%LOCALAPPDATA%\Claude-Data\claude_*\claude_desktop_config.json` | `mcpServers` | mis de côté* |
| OpenCode | `%USERPROFILE%\.config\opencode\opencode.json` | `mcp` | `enabled: false` |
| Codex (OpenAI) | `%USERPROFILE%\.codex\config.toml` | `[mcp_servers.*]` | `enabled = false` |
| Gemini CLI / Qwen Code | `%USERPROFILE%\.gemini\settings.json` / `.qwen\settings.json` | `mcpServers` | mis de côté* |
| VS Code · GitHub Copilot | `%APPDATA%\Code\User\mcp.json` | `servers` | mis de côté* |
| GitHub Copilot CLI | `%USERPROFILE%\.copilot\mcp-config.json` | `mcpServers` | mis de côté* |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` | `mcpServers` | mis de côté* |
| Windsurf | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` | `mcpServers` | `disabled: true` |
| Cline / Roo Code / Kilo Code | `%APPDATA%\Code\User\globalStorage\<extension>\settings\…json` | `mcpServers` | `disabled: true` |
| Kiro | `%USERPROFILE%\.kiro\settings\mcp.json` | `mcpServers` | `disabled: true` |
| LM Studio | `%USERPROFILE%\.lmstudio\mcp.json` | `mcpServers` | mis de côté* |

\* L'agent n'a pas d'option « désactivé » : le serveur est retiré du fichier et gardé dans
`%APPDATA%\GestionMCP\reglages.json` ; « Activer » le remet.

Pour Claude Desktop, l'application cherche aussi `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`
et `%APPDATA%\Claude`, et signale les anciens fichiers ignorés qui contiennent encore des serveurs.

Un emplacement peut être changé par agent (menu **Agent › Utiliser un autre fichier…**).
Le menu **Fichier › Où sont les fichiers de chaque agent ?** affiche la liste complète.

## Utilisation

- **Barre latérale** : agents regroupés par rubrique, avec pastille d'état (vert : serveurs
  présents ; anneau gris : aucun serveur ou agent non détecté ; rouge : fichier illisible) et nombre
  de serveurs. Chaque agent indique quels logiciels lisent son fichier (encadré « Ce fichier est lu
  par »).
- **Bandeau** : recherche (Ctrl+F), thème clair, sombre ou automatique, et « Relire ».
- **Liste et Détails** : le panneau de droite montre l'état, la commande, les arguments, les
  variables, l'URL, les en-têtes, les autres agents où le serveur est présent et le JSON écrit dans
  le fichier. Les valeurs secrètes sont masquées (case « Afficher les secrets »).
- **Vue d'ensemble** : tableau serveurs × agents (✔ actif, ○ désactivé, ⚠ incomplet). Double-clic
  sur une case = activer/désactiver, ou copier le serveur dans cet agent s'il n'y est pas ; clic
  droit pour les autres actions, dont « Ouvrir dans » l'agent.
- **Ajouter / Modifier** : formulaire (type local ou distant, commande, arguments, variables, ou
  URL) et onglet **JSON** qui montre le format exact écrit pour l'agent. On peut y coller un JSON
  de n'importe quel format : il est converti. Le bouton **Tester** lance le serveur avec les valeurs
  saisies avant d'enregistrer (Ctrl+Entrée).
- **Copier vers…** : copie un ou plusieurs serveurs vers d'autres agents avec conversion
  (OpenCode `local/remote` + `command: [...]` ⇄ `command` + `args` ; URL → `npx mcp-remote` pour
  Claude Desktop ; `httpUrl` pour Gemini ; `serverUrl` pour Windsurf ; TOML pour Codex…).
- **Coller du JSON** : importe un ou plusieurs serveurs copiés depuis une documentation
  (pré-rempli avec le presse-papiers).
- **Fichier › Exporter les serveurs de l'agent…** : enregistre les serveurs de l'agent dans un
  fichier JSON.
- **Agent › Redémarrer…** : ferme puis relance l'application graphique si elle est détectée
  (Claude Desktop, VS Code et ses extensions Cline/Roo/Kilo, Cursor, Windsurf, Kiro, LM Studio),
  en arrière-plan. Enregistre le travail avant de confirmer. Pour Claude Code, l'application
  explique comment relancer chaque usage. Les agents en ligne de commande ne sont pas redémarrés
  automatiquement.
- **Agent › Restaurer une sauvegarde** : 30 dernières versions par agent
  (`%APPDATA%\GestionMCP\sauvegardes\<agent>`), avec aperçu du contenu.

Après une modification, relancez l'agent ou la session manuellement s'il n'est pas redémarré
automatiquement.

### Raccourcis clavier

| Raccourci | Action |
|---|---|
| Ctrl+N | Ajouter un serveur |
| Entrée / F2 | Modifier |
| Espace | Activer / désactiver |
| Ctrl+D | Copier vers… |
| Ctrl+T | Tester |
| Suppr | Supprimer |
| Ctrl+V | Coller du JSON |
| Ctrl+E | Exporter |
| Ctrl+F | Rechercher |
| Échap | Effacer la recherche |
| F5 | Relire |

La liste est aussi dans **Aide › Raccourcis clavier**.

### Claude Code ou Claude Desktop ?

Deux fichiers distincts. `%USERPROFILE%\.claude.json` (« Claude Code · CLI, VS Code, Desktop ») est
lu par la commande `claude` dans un terminal (CMD, PowerShell…), par l'extension Claude Code de
VS Code et par l'onglet « Code » de Claude Desktop. `claude_desktop_config.json`
(« Claude Desktop · Chat ») n'est lu que par l'onglet « Chat » de Claude Desktop. Voir aussi
**Aide › Claude Code ou Claude Desktop : quel fichier ?**.

Les fichiers JSONC (avec commentaires) sont lus ; leurs commentaires sont supprimés à la première
modification, après confirmation.

### Données de l'application

| Élément | Emplacement |
|---|---|
| Réglages (emplacements personnalisés, serveurs mis de côté, thème `theme`, taille `size` et état agrandi `zoomed` de la fenêtre) | `%APPDATA%\GestionMCP\reglages.json` |
| Sauvegardes (30 par agent) | `%APPDATA%\GestionMCP\sauvegardes\<agent>\` |

### Non gérés

Configurations propres à un projet (`.mcp.json`, `opencode.json` de dossier, `.vscode/mcp.json`),
plugins et connecteurs claude.ai, Zed, Continue.

## Tests

Le dépôt ne contient pas de suite de tests automatisés. La version 2.0.0 a été vérifiée par des
scripts lançant l'interface dans un dossier utilisateur isolé (ajout, désactivation, copie vers
Cursor et Codex, import JSON, sauvegardes, fenêtres et thèmes, captures d'écran) ; elle reste à
essayer à la main sur les vrais fichiers de vos agents.

## Licence

© 2026 Henrick PIETTE. Distribué sous licence MIT. Voir le fichier [LICENSE](LICENSE).
