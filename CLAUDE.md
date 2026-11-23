# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Application de bureau Tkinter (Windows en priorité) qui gère les serveurs MCP déclarés dans les
fichiers de configuration **de portée utilisateur** de 15 agents de codage (Claude Code, Claude
Desktop, OpenCode, Codex CLI, Gemini CLI, Qwen Code, VS Code/Copilot, Copilot CLI, Cursor, Windsurf,
Cline, Roo Code, Kilo Code, Kiro, LM Studio). Tout le code est dans **`gestion_mcp.py`**
(un seul module, ~3 100 lignes ; version 2.0 = interface redessinée). Interface, messages, commentaires et documentation sont en
français : garder cette langue.

Contraintes :
- **Bibliothèque standard uniquement** : n'ajoutez aucune dépendance externe (l'exécutable
  PyInstaller n'en embarque aucune, le `.spec` n'a pas de `hiddenimports`).
- **Python ≥ 3.11** pour `tomllib` (lecture du TOML de Codex). Sur une version plus ancienne,
  `tomllib = None` : l'application démarre mais Codex lève une erreur explicite.
- Tout appel `subprocess` passe `creationflags=NO_WINDOW` : l'exécutable est fenêtré
  (`console=False`), sinon une console s'ouvrirait à chaque appel.

## Commandes

```bat
python gestion_mcp.py              :: lancer l'application
python -m py_compile gestion_mcp.py :: vérification rapide de la syntaxe
pip install pyinstaller
build.bat                          :: produit dist\Gestionnaire-MCP-v2.0.exe
```

Il n'y a ni suite de tests ni linter : la validation de la v1.0.0 a été manuelle.
**Lancer l'application lit et peut modifier les vrais fichiers de configuration des agents de la
machine** (une sauvegarde est faite avant chaque écriture). Pour vérifier une conversion sans
interface ni écriture, importer le module : l'import n'a aucun effet de bord (pas de fenêtre,
`%APPDATA%\GestionMCP` n'est créé que par `Settings()`).

```bat
python -c "import gestion_mcp as g; c = g.AGENT_BY_ID['claude_code'].to_canon({'type':'http','url':'https://x/mcp'}); print(g.AGENT_BY_ID['claude_desktop'].from_canon(c, False))"
```

Pour tester l'interface sans toucher aux vrais fichiers : avant d'importer le module, faire pointer
`USERPROFILE`, `HOME`, `APPDATA`, `LOCALAPPDATA` et `XDG_CONFIG_HOME` vers un dossier temporaire
(`HOME = Path.home()` lit `USERPROFILE` sous Windows), y créer des fichiers d'agents d'exemple, puis
instancier `App()` et piloter ses méthodes (`goto`, `toggle`, `add_canons`, `refresh`…) avec
`app.update()` au lieu de `mainloop()`. La détection des agents utilise aussi `shutil.which`, donc
les CLI réellement installées apparaissent comme détectées.

Changement de version : mettre à jour ensemble `APP_VERSION` et `APP_TITLE` (code), le nom du
`.spec` (`Gestionnaire-MCP-vX.Y.spec`) et son `name=`, `build.bat`, `README.md`, `CHANGELOG.md` (format Keep a Changelog,
versionnage sémantique) et `RELEASE_NOTES.md` (texte de la release GitHub, où l'exécutable est
publié en pièce jointe ; `dist/`, `build/` et `*.exe` sont ignorés par Git).

## Architecture

Principe : *un agent = un fichier = un format*. Le module est découpé en sections (bandeaux
`# ===`) :

### 1. Lecture / écriture bas niveau
- `parse_json_text` essaie `json.loads`, puis repasse par `strip_jsonc` (commentaires `//`, `/* */`,
  virgules finales). Renvoie `(data, lossy)` : `lossy=True` signifie que les commentaires seront
  perdus à la réécriture (l'UI demande confirmation une fois par agent, `App.lossy_ok`).
- `write_text_atomic` : fichier temporaire `*.tmp_gestionmcp` puis `os.replace`, UTF-8, fins de
  ligne LF. Le JSON est réécrit avec `ensure_ascii=False` et l'indentation détectée
  (`detect_indent`) pour limiter le diff.
- TOML : la stdlib ne sait pas écrire de TOML. `toml_replace_mcp` retire du texte toutes les
  sections `[mcp_servers…]` et les régénère **en fin de fichier** (`toml_key`/`toml_val`, tables
  en ligne pour `env`/en-têtes) ; le reste du texte est conservé ligne à ligne, mais les
  commentaires situés dans ces sections sont perdus.

### 2. Format « canon » et fiches `Agent`
- Le canon (`empty_canon`) est la représentation commune d'un serveur : `type`
  (`stdio`/`http`/`sse`), `command`, `args`, `env`, `cwd`, `url`, `headers`, `enabled`, `extra`.
  **Toute conversion passe par lui** : `natif → Agent.to_canon → canon → Agent.from_canon → natif`.
- `to_canon` déduit le transport d'une URL : champ `type`, puis la clé utilisée (`url_keys`), puis
  le suffixe `/sse`. Les clés inconnues vont dans `extra`.
- `from_canon(c, keep_extra)` : `keep_extra=True` pour l'édition dans le même agent
  (`ServerDialog`), `False` pour les copies entre agents (`copy_to`, double-clic de la vue
  d'ensemble), `not agent.strict` pour un JSON collé (OpenCode est `strict` : il refuse les clés
  inconnues).
- Une fiche `Agent` est **purement déclarative**. Ajouter ou corriger un agent = modifier sa fiche
  dans `AGENTS`, pas la logique de conversion. Paramètres : chemin (`path_fn`, `override` choisi
  par l'utilisateur), `root` (chemin de la clé des serveurs), `fmt` (`json` ou `toml`), valeur de
  `type` par transport (`None` = pas de champ `type`), `url_keys`, `command_array` (OpenCode :
  `command: [exe, args…]`), `env_key`/`headers_key`, `remote=False` (Claude Desktop : une URL
  devient `npx -y mcp-remote <url> --header …`), `cwd_ok`, `defaults` (Copilot CLI :
  `tools: ["*"]`), détection (`clis` via `shutil.which`, `dirs`), `warnings`, `note`, `reload`.
  Champs d'affichage (v2) : `group` (rubrique de la barre latérale, l'une de `GROUPS`), `label`
  (nom **sans ambiguïté** affiché partout, ex. « Claude Code · CLI, VS Code, Desktop » vs
  « Claude Desktop · Chat »), `subtitle`, `used_by` (logiciels qui lisent réellement ce fichier,
  affichés dans l'encadré « Ce fichier est lu par » et les infobulles) et `hint` (précision pour
  lever une confusion). `short` sert d'en-tête de colonne dans la vue d'ensemble. L'ordre de
  `AGENTS` donne l'ordre des colonnes. Penser aussi à `RESTART_TARGETS` et au tableau du README.
- Chemin de Claude Desktop : `claude_desktop_file` choisit, parmi `Claude-Data\claude_*`,
  `Packages\Claude_*\…\Roaming\Claude` et `%APPDATA%\Claude`, le premier dossier sans marqueur
  `.user-data-left*` (ce marqueur signale un dossier abandonné par l'application).
  `desktop_warnings` signale les anciens fichiers qui contiennent encore des serveurs.
- `GENERIC` + `parse_snippet` lisent du JSON collé de n'importe quel format (`mcpServers`,
  `servers`, `mcp`, `mcp_servers`, `context_servers`, `{nom: {...}}` ou serveur seul, qui renvoie
  le nom `""`).

### 3. Désactivation : deux sources de vérité
`Agent.disable` vaut :
- `"disabled"` : écrit `disabled: true/false` ;
- `"enabled"` : écrit `enabled: false` (OpenCode écrit toujours `enabled`) ;
- `"park"` : l'agent n'a pas d'option de désactivation. Le serveur est **retiré du fichier** et
  conservé dans `Settings.d["parked"][agent_id]` (`%APPDATA%\GestionMCP\reglages.json`, qui
  contient aussi `paths` pour les emplacements personnalisés et `show_all`).

`load_state` fusionne les serveurs du fichier et ceux mis de côté (`Srv.parked=True`) ; si un nom
existe aux deux endroits, celui du fichier l'emporte. **Toute opération d'ajout, de modification
ou d'activation passe par `App.store_many(agent, ops)`**, qui met à jour le fichier et la liste des
serveurs mis de côté de façon cohérente (`ops` : `name`, `native`, `enabled`, `old`,
`old_parked`). L'export JSON ignore les serveurs mis de côté.

### 4. Écriture des fichiers
Chaîne : `App.store_many` → `App.write` (refuse si le fichier est illisible, confirmation JSONC) →
`modify_file(agent, fn)`. `modify_file` relit le fichier, applique `fn` au **seul** dictionnaire des
serveurs (crée les nœuds intermédiaires de `root` au besoin), n'écrit que si quelque chose a
changé, appelle `backup_file`, puis écrit de façon atomique. N'écrivez jamais un fichier d'agent
autrement. `replace_key` renomme un serveur sans changer sa position.

Sauvegardes : `%APPDATA%\GestionMCP\sauvegardes\<agent_id>\<AAAAMMJJ_HHMMSS_mmm>__<fichier>`.
La rotation (`MAX_BACKUPS = 30`) et l'affichage dans `restore_backup` reposent sur le **tri
alphabétique** de ce nom : garder ce format d'horodatage.

### 5. Test d'un serveur et redémarrage des applications
- `test_stdio` : lance la commande (résolue par `shutil.which`, ce qui trouve `npx.cmd`), envoie
  `initialize` (protocole `2025-06-18`), `notifications/initialized` puis `tools/list` en JSON-RPC
  ligne par ligne ; stdout et stderr sont lus dans des threads ; délais d'attente de 90 s / 30 s ;
  `kill_tree` (`taskkill /T /F`). `test_http` : un seul POST `initialize`, gère les réponses SSE
  (`data:`) ; une erreur 401 est présentée comme normale pour un serveur OAuth.
- Ces tests tournent dans un thread `daemon`. Ils ne communiquent avec Tk que via
  `TextDialog.append`, qui dépose le texte dans une `queue.Queue` vidée par `_drain` (`after(80)`)
  dans le thread de l'interface. Ne jamais toucher un widget ni appeler `after` depuis un autre
  thread (`after` lève `RuntimeError` hors `mainloop`). Pour un autre travail long, utiliser
  `App.run_bg(fn, on_done)` (même principe, file sondée par `_poll_jobs`).
- `RESTART_TARGETS[agent_id] = (nom affiché, noms d'exécutables, noms de l'application dans le menu
  Démarrer pour Get-StartApps, regex des chemins d'exécutables à exclure)`. Claude Code est associé
  à Claude Desktop (onglet Code) et exclut `Claude-Data\…\claude-code\` pour ne pas tuer la CLI.
  `restart_windows_app` interroge PowerShell (`Get-CimInstance Win32_Process`, `Get-StartApps`),
  ferme les processus racines (`taskkill /T`), puis relance via `shell:AppsFolder\<AppID>` ou le
  chemin de l'exécutable. Il est lancé via `App.run_bg` : l'interface ne se fige pas.

### 6. Apparence
- Thème ttk `clam` entièrement restylé dans `App.apply_theme` à partir de `PALETTES`
  (`light`/`dark`) ; mode `auto` = réglage Windows (`windows_prefers_dark`, registre). **Toute
  couleur vient de `self.P`** : pas de couleur codée en dur dans les widgets. Les styles nommés
  (`Card.TFrame` bordé, `Surface.TFrame` sans bordure, `Title.TLabel`, `Accent.TButton`,
  `Danger.TButton`, `Seg.Toolbutton`, `Side.Treeview`…) sont tous définis là.
- Les widgets Tk classiques (`tk.Text`, `tk.Listbox`) ne suivent pas ttk : les créer avec
  `App.make_text` / `App.make_list`, qui les enregistrent dans `App.tk_widgets` pour être recolorés
  au changement de thème. Le panneau Détails (`self.detail`) utilise des tags (`_config_detail_tags`).
- Haute résolution : `enable_dpi_awareness()` est appelé dans `main()` avant `Tk()` ; toute taille
  en pixels passe par `App.px(n)` (facteur `scale` = DPI / 96). Les polices sont en points.
- Icônes (pastilles d'état, logo) générées en PNG sans dépendance (`_png_b64`, `make_dot`,
  `make_logo`) ; garder une référence Python aux `PhotoImage` (`App.icons`). Les symboles de la vue
  d'ensemble sont ✔ / ○ / ⚠ : éviter ⏸ et les caractères qui s'affichent en emoji encadré.
- Barre de titre sombre via `set_titlebar_theme` (DWM, Windows 10/11), à appeler pour chaque
  fenêtre (`Dialog.show` le fait).

### 7. Interface
- `App(tk.Tk)` : bandeau (recherche, thème, relire), barre latérale `Side.Treeview` regroupée par
  `GROUPS` (lignes de rubrique `grp:<nom>` non sélectionnables : `on_agent_select` passe à la ligne
  voisine), zone principale en `grid` (en-tête avec puces, encadré « lu par », chemin,
  avertissements ; barre d'outils ou légende ; liste + panneau Détails ; rappel `reload`).
- Un seul `Treeview` réutilisé pour deux vues : vue d'ensemble (`OVERVIEW`, `show="headings"`,
  grille serveurs × agents) et vue d'un agent (`show="tree headings"`, pastille dans `#0`).
  `App.rows` associe chaque ligne à un nom (vue d'ensemble) ou à un `Srv` (vue d'un agent).
  La recherche (`App.query`, texte d'invite géré par `_search_focus`/`self._ph`) filtre les deux vues.
- `App.states` (`agent_id → AgentState`) est entièrement reconstruit par `refresh(keep)`. Après
  chaque action, appeler `App.done(agent, message, keep=[noms à resélectionner])` : relecture des
  fichiers, puis message dans la barre d'état suivi du texte `agent.reload`. `App.goto(agent, nom)`
  affiche un agent et sélectionne un serveur.
- `update_actions` active/désactive les boutons selon la sélection ; `update_details` remplit le
  panneau Détails (valeurs secrètes masquées par `mask_native`/`mask_text` sauf case « Afficher
  les secrets »). Clic droit : `on_right_click` ; raccourcis : `_bind_keys` (touches de la liste
  liées sur `self.tree`, pas sur la racine, pour ne pas voler Ctrl+V aux champs de saisie).
- Fenêtres secondaires : sous-classes de `Dialog` (thème, centrage, `show(largeur, hauteur)`,
  modale, et restitution de la saisie modale à la fenêtre précédente à la fermeture — nécessaire
  pour « Tester » ouvert depuis `ServerDialog`). `ServerDialog`, `CopyDialog`, `BackupDialog`,
  `TextDialog`, `InfoDialog` (blocs `h`/`p`/`field`/`muted`/`small`/`table`/`link`/`logo`).
  L'appelant fait `self.wait_window(dlg)` puis lit `dlg.result`. `ServerDialog` synchronise le
  formulaire et l'onglet JSON en repassant par le canon à chaque changement d'onglet, et affiche
  ses erreurs dans `err_lbl` plutôt que dans une boîte de message.
- `reglages.json` contient aussi `theme`, `size` et `zoomed` (fenêtre restaurée par
  `_restore_geometry`, enregistrée par `on_close`).

Hors périmètre volontaire : configurations de projet (`.mcp.json`, `opencode.json` de dossier,
`.vscode/mcp.json`), plugins et connecteurs claude.ai, Zed, Continue.
