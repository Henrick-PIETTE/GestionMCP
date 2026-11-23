# -*- coding: utf-8 -*-
"""
Gestionnaire MCP simplifié — un agent de codage = un fichier = un format.

Chaque agent (Claude Code, Claude Desktop, OpenCode, Codex, Gemini CLI, VS Code,
Cursor, Windsurf, Cline…) est décrit par une fiche : emplacement du fichier,
clé qui contient les serveurs, format d'un serveur, façon de le désactiver.
L'application lit/écrit uniquement cette clé : le reste du fichier est conservé.
Les copies d'un agent à l'autre sont converties automatiquement.
"""
from __future__ import annotations

import base64
import copy
import ctypes
import datetime as _dt
import glob
import json
import math
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zlib
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

APP_NAME = "Gestionnaire MCP"
APP_VERSION = "2.0.0"
APP_TITLE = "Gestionnaire MCP v2.0 - Serveur MCP de vos agents de codage"
APP_AUTHOR = "Henrick PIETTE"
APP_URL = "https://github.com/Henrick-PIETTE/GestionMCP"
IS_WIN = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if IS_WIN else 0

HOME = Path.home()
APPDATA = Path(os.environ.get("APPDATA") or HOME)
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or HOME)
DATA_DIR = APPDATA / "GestionMCP"
SETTINGS_FILE = DATA_DIR / "reglages.json"
BACKUP_DIR = DATA_DIR / "sauvegardes"
MAX_BACKUPS = 30
DESKTOP_CFG = "claude_desktop_config.json"


# =========================================================================== #
#  Lecture / écriture de fichiers
# =========================================================================== #
def _skip_string(t: str, i: int) -> int:
    """i pointe sur un guillemet ouvrant ; retourne l'index après le guillemet fermant."""
    j, n = i + 1, len(t)
    while j < n and t[j] != '"':
        j += 2 if t[j] == "\\" else 1
    return j + 1


def strip_jsonc(t: str) -> str:
    """JSONC -> JSON : retire commentaires // et /* */ et virgules finales."""
    out, i, n = [], 0, len(t)
    while i < n:
        c = t[i]
        if c == '"':
            j = _skip_string(t, i)
            out.append(t[i:j])
            i = j
        elif t.startswith("//", i):
            j = t.find("\n", i)
            i = n if j < 0 else j
        elif t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    s, res, i, n = "".join(out), [], 0, 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            j = _skip_string(s, i)
            res.append(s[i:j])
            i = j
            continue
        if c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        res.append(c)
        i += 1
    return "".join(res)


def parse_json_text(text: str):
    """Retourne (objet, avec_commentaires). Lève ValueError avec la ligne en cas d'erreur."""
    try:
        return json.loads(text), False
    except json.JSONDecodeError as e:
        try:
            return json.loads(strip_jsonc(text)), True
        except json.JSONDecodeError:
            raise ValueError(f"JSON invalide (ligne {e.lineno}, colonne {e.colno}) : {e.msg}") from None


def detect_indent(text: str):
    m = re.search(r"^([ \t]+)\S", text, re.M)
    if not m:
        return 2
    s = m.group(1)
    return "\t" if "\t" in s else max(1, min(len(s), 8))


def write_text_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp_gestionmcp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def open_path(p: Path):
    if IS_WIN:
        os.startfile(str(p))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


RESTART_TARGETS = {
    "claude_code": ("Claude Desktop", ("Claude.exe",), ("Claude",),
                    r"\\Claude-Data\\[^\\]+\\claude-code\\"),
    "claude_desktop": ("Claude Desktop", ("Claude.exe",), ("Claude",),
                       r"\\Claude-Data\\[^\\]+\\claude-code\\"),
    "vscode": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "cline": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "roo": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "kilo": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "cursor": ("Cursor", ("Cursor.exe",), ("Cursor",), ""),
    "windsurf": ("Windsurf", ("Windsurf.exe",), ("Windsurf",), ""),
    "kiro": ("Kiro", ("Kiro.exe",), ("Kiro",), ""),
    "lmstudio": ("LM Studio", ("LM Studio.exe", "LMStudio.exe"), ("LM Studio",), ""),
}


def windows_app_state(target: tuple[str, tuple[str, ...], tuple[str, ...], str]) -> dict:
    """Retourne les processus et les raccourcis Windows connus pour une application."""
    _, process_names, app_names, exclude_path = target
    names_json = json.dumps(process_names)
    apps_json = json.dumps(app_names)
    exclude_json = json.dumps(exclude_path)
    script = (
        f"$names = ConvertFrom-Json -InputObject '{names_json}'; "
        f"$appNames = ConvertFrom-Json -InputObject '{apps_json}'; "
        f"$exclude = ConvertFrom-Json -InputObject '{exclude_json}'; "
        "$processes = @(Get-CimInstance Win32_Process | Where-Object { "
        "$names -contains $_.Name -and "
        "(-not $exclude -or ($_.ExecutablePath -and $_.ExecutablePath -notmatch $exclude)) "
        "} | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath); "
        "$apps = @(Get-StartApps | Where-Object { $appNames -contains $_.Name } "
        "| Select-Object Name, AppID); "
        "[pscustomobject]@{ processes = $processes; apps = $apps } "
        "| ConvertTo-Json -Compress -Depth 3"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Impossible de détecter l'application : {detail}")
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Réponse PowerShell invalide : {e}") from e
    if not isinstance(state, dict):
        raise RuntimeError("Les informations de l'application sont illisibles.")
    for key in ("processes", "apps"):
        value = state.get(key)
        state[key] = value if isinstance(value, list) else ([value] if value else [])
    return state


def restart_windows_app(target: tuple[str, tuple[str, ...], tuple[str, ...], str]):
    """Ferme proprement l'application cible puis la relance si Windows la connaît."""
    name = target[0]
    state = windows_app_state(target)
    processes = state["processes"]
    app_id = next((app.get("AppID") for app in state["apps"] if app.get("AppID")), None)
    executable = next((proc.get("ExecutablePath") for proc in processes
                       if proc.get("ExecutablePath")), None)
    if not app_id and not executable:
        raise RuntimeError(f"{name} n'est pas détecté ou ne possède pas de lanceur connu.")

    pids = {int(proc["ProcessId"]) for proc in processes if proc.get("ProcessId")}
    roots = [int(proc["ProcessId"]) for proc in processes
             if proc.get("ProcessId") and int(proc.get("ParentProcessId") or 0) not in pids]
    if processes and not roots:
        roots = list(pids)
    for pid in roots:
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
        )
        if result.returncode:
            state = windows_app_state(target)
            still_running = any(int(proc.get("ProcessId") or 0) in pids for proc in state["processes"])
            if still_running:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"{name} n'a pas pu être fermé proprement : {detail}")

    deadline = time.monotonic() + 12
    while processes and time.monotonic() < deadline:
        time.sleep(0.4)
        processes = windows_app_state(target)["processes"]
    if processes:
        raise RuntimeError(f"{name} est encore ouvert. Fermez ses fenêtres avant de le relancer.")

    if app_id:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                         creationflags=NO_WINDOW)
    elif executable:
        subprocess.Popen([executable], creationflags=NO_WINDOW)
    else:
        raise RuntimeError(f"Aucun lanceur n'a été trouvé pour {name}.")

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if windows_app_state(target)["processes"]:
            return
        time.sleep(0.4)
    raise RuntimeError(f"{name} n'a pas démarré après la demande de lancement.")


# ---------------------------------------------------------------- TOML (Codex)
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOML_HDR = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def toml_key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k, ensure_ascii=False)


def toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_val(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v), ensure_ascii=False)


def toml_replace_mcp(text: str, servers: dict) -> str:
    """Remplace toutes les sections [mcp_servers.*] du texte TOML ; le reste est conservé tel quel."""
    out, skip = [], False
    for ln in text.splitlines():
        m = _TOML_HDR.match(ln)
        if m:
            name = m.group(1).replace('"', "").strip()
            skip = name == "mcp_servers" or name.startswith("mcp_servers.")
        if not skip:
            out.append(ln)
    body = "\n".join(out).rstrip()
    blocks = []
    for n, cfg in servers.items():
        lines = [f"[mcp_servers.{toml_key(n)}]"]
        lines += [f"{toml_key(k)} = {toml_val(v)}" for k, v in cfg.items() if v is not None]
        blocks.append("\n".join(lines))
    parts = ([body] if body else []) + blocks
    return "\n\n".join(parts) + "\n"


def read_root(path: Path, root: tuple, fmt: str = "json") -> tuple[dict, bool]:
    """Lit le dictionnaire de serveurs à l'emplacement `root` du fichier. Retourne (serveurs, avec_commentaires)."""
    if not path.exists():
        return {}, False
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}, False
    if fmt == "toml":
        if tomllib is None:
            raise RuntimeError("Python 3.11 ou plus récent est nécessaire pour lire le TOML.")
        data, lossy = tomllib.loads(text), False
    else:
        data, lossy = parse_json_text(text)
    node = data
    for k in root:
        node = node.get(k) if isinstance(node, dict) else None
    return (node if isinstance(node, dict) else {}), lossy


# =========================================================================== #
#  Format interne commun (« canon »)
# =========================================================================== #
def empty_canon() -> dict:
    return {"type": "stdio", "command": "", "args": [], "env": {}, "cwd": "",
            "url": "", "headers": {}, "enabled": True, "extra": {}}


def canon_summary(c: dict) -> str:
    if c["type"] == "stdio":
        return " ".join([c["command"]] + c["args"])
    return c["url"]


def canon_invalid(c: dict) -> bool:
    return not (c["command"] if c["type"] == "stdio" else c["url"])


KNOWN_KEYS = {"type", "command", "args", "env", "environment", "cwd", "url", "httpUrl", "serverUrl",
              "headers", "http_headers", "enabled", "disabled"}


# =========================================================================== #
#  Fiches des agents
# =========================================================================== #
GROUPS = ["Claude (Anthropic)", "Éditeurs de code", "Extensions VS Code", "Agents en ligne de commande", "Autres"]
GROUP_SUBTITLES = {
    "Éditeurs de code": "Éditeur de code avec agent intégré",
    "Extensions VS Code": "Extension de Visual Studio Code",
    "Agents en ligne de commande": "Agent en ligne de commande (terminal)",
    "Autres": "Application de bureau",
}


class Agent:
    def __init__(self, id, name, short, path_fn, *, root=("mcpServers",), fmt="json",
                 stdio_type=None, http_type=None, sse_type=None, url_keys=None, remote=True,
                 disable="park", command_array=False, env_key="env", headers_key="headers",
                 defaults=None, cwd_ok=False, strict=False, clis=(), dirs=None, warnings=None,
                 note="", reload=None, group="Autres", label=None, subtitle="", used_by=(), hint=""):
        self.id, self.name, self.short = id, name, short
        self.group = group                  # rubrique de la barre latérale (voir GROUPS)
        self.label = label or name          # nom sans ambiguïté affiché partout dans l'interface
        self.subtitle = subtitle or GROUP_SUBTITLES.get(group, "")
        self.used_by = tuple(used_by)       # logiciels qui lisent réellement ce fichier
        self.hint = hint                    # précision affichée sous « Ce fichier est lu par »
        self.default_path = path_fn
        self.root, self.fmt = tuple(root), fmt
        self.stdio_type, self.http_type, self.sse_type = stdio_type, http_type, sse_type
        self.url_keys = url_keys or {"http": "url", "sse": "url"}
        self.remote = remote                # False : l'agent ne lit que des commandes (Claude Desktop)
        self.disable = disable              # "disabled" | "enabled" | "park"
        self.command_array = command_array  # OpenCode : "command": [exe, arg1, …]
        self.env_key, self.headers_key = env_key, headers_key
        self.defaults = defaults or {}
        self.cwd_ok, self.strict = cwd_ok, strict
        self.clis = clis
        self.dirs = dirs or (lambda: [])
        self.warnings = warnings
        self.note = note
        self.reload = reload or f"Relancez {short} pour appliquer."
        self.override: str | None = None

    def path(self) -> Path:
        return Path(self.override) if self.override else Path(self.default_path())

    # ------------------------------------------------------ natif -> canon
    def to_canon(self, native) -> dict:
        n = native if isinstance(native, dict) else {}
        c = empty_canon()
        t = str(n.get("type") or "").lower().replace("-", "").replace("_", "")
        ukey = next((k for k in ("url", "httpUrl", "serverUrl") if isinstance(n.get(k), str) and n.get(k)), None)
        if ukey and not n.get("command"):
            c["url"] = n[ukey]
            if t == "sse":
                c["type"] = "sse"
            elif t in ("http", "streamablehttp", "remote"):
                c["type"] = "http"
            elif ukey == self.url_keys["sse"] and self.url_keys["sse"] != self.url_keys["http"]:
                c["type"] = "sse"
            elif ukey == "url" and not t and c["url"].rstrip("/").endswith("/sse"):
                c["type"] = "sse"
            else:
                c["type"] = "http"
            h = n.get(self.headers_key) or n.get("headers") or n.get("http_headers") or {}
            c["headers"] = {str(k): str(v) for k, v in h.items()} if isinstance(h, dict) else {}
        else:
            cmd = n.get("command", "")
            args = n.get("args") or []
            if isinstance(cmd, list):
                c["command"] = str(cmd[0]) if cmd else ""
                c["args"] = [str(a) for a in cmd[1:]] + [str(a) for a in args]
            else:
                c["command"] = str(cmd or "")
                c["args"] = [str(a) for a in args] if isinstance(args, list) else []
            env = n.get(self.env_key)
            if env is None:
                env = n.get("env") or n.get("environment")
            c["env"] = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
            c["cwd"] = str(n.get("cwd") or "")
        if "disabled" in n:
            c["enabled"] = not bool(n["disabled"])
        if "enabled" in n:
            c["enabled"] = bool(n["enabled"])
        c["extra"] = {k: copy.deepcopy(v) for k, v in n.items()
                      if k not in KNOWN_KEYS and k not in (self.env_key, self.headers_key)}
        return c

    # ------------------------------------------------------ canon -> natif
    def from_canon(self, c: dict, keep_extra: bool) -> dict:
        remote = c["type"] in ("http", "sse")
        o: dict = {}
        if self.command_array:  # OpenCode
            if remote:
                o = {"type": "remote", "url": c["url"]}
                if c["headers"]:
                    o["headers"] = dict(c["headers"])
            else:
                o = {"type": "local", "command": [c["command"]] + list(c["args"])}
                if c["env"]:
                    o[self.env_key] = dict(c["env"])
        elif remote and not self.remote:  # Claude Desktop : passerelle mcp-remote
            args = ["-y", "mcp-remote", c["url"]]
            for k, v in c["headers"].items():
                args += ["--header", f"{k}:{v}"]
            o = {"command": "npx", "args": args}
        elif remote:
            t = self.http_type if c["type"] == "http" else self.sse_type
            if t:
                o["type"] = t
            o[self.url_keys[c["type"]]] = c["url"]
            if c["headers"]:
                o[self.headers_key] = dict(c["headers"])
        else:
            if self.stdio_type:
                o["type"] = self.stdio_type
            o["command"] = c["command"]
            o["args"] = list(c["args"])
            if c["env"]:
                o[self.env_key] = dict(c["env"])
            if c["cwd"] and self.cwd_ok:
                o["cwd"] = c["cwd"]
        for k, v in self.defaults.items():
            o.setdefault(k, copy.deepcopy(v))
        if keep_extra:
            for k, v in c["extra"].items():
                o.setdefault(k, copy.deepcopy(v))
        if self.disable == "disabled":
            o["disabled"] = not c["enabled"]
        elif self.disable == "enabled" and (self.command_array or not c["enabled"]):
            o["enabled"] = bool(c["enabled"])
        return o

    def set_enabled(self, native: dict, en: bool) -> dict:
        n = copy.deepcopy(native)
        if self.disable == "disabled":
            n.pop("enabled", None)
            n["disabled"] = not en
        elif self.disable == "enabled":
            n.pop("disabled", None)
            n["enabled"] = en
        return n

    def conversion_notes(self, name: str, c: dict) -> list[str]:
        notes = []
        if c["type"] != "stdio" and not self.remote:
            notes.append(f"{name} → {self.short} : URL convertie en « npx -y mcp-remote … » (Node.js requis).")
        if c["type"] == "stdio" and c["cwd"] and not self.cwd_ok:
            notes.append(f"{name} → {self.short} : dossier de travail (cwd) non pris en charge, ignoré.")
        return notes

    @property
    def disable_note(self) -> str:
        if self.disable == "park":
            return ("Cet agent n'a pas d'option « désactivé » : désactiver retire le serveur du fichier "
                    "et le garde de côté dans le gestionnaire (réactiver le remet).")
        key = "disabled: true" if self.disable == "disabled" else "enabled: false"
        return f"Désactiver utilise l'option native de l'agent ({key})."


# ----------------------------------------------------------- emplacements
def xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def vscode_user() -> Path:
    if IS_WIN:
        return APPDATA / "Code" / "User"
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support" / "Code" / "User"
    return xdg_config() / "Code" / "User"


def desktop_candidates() -> list[Path]:
    if IS_WIN:
        c = [Path(p) for p in sorted(glob.glob(str(LOCALAPPDATA / "Claude-Data" / "claude_*")))]
        c += [Path(p) / "LocalCache" / "Roaming" / "Claude"
              for p in sorted(glob.glob(str(LOCALAPPDATA / "Packages" / "Claude_*")))]
        c.append(APPDATA / "Claude")
        return c
    if sys.platform == "darwin":
        return [HOME / "Library" / "Application Support" / "Claude"]
    return [xdg_config() / "Claude"]


def claude_desktop_file() -> Path:
    """Fichier réellement lu par Claude Desktop (les dossiers abandonnés portent un marqueur .user-data-left*)."""
    cands = desktop_candidates()
    live = [d for d in cands if d.is_dir() and not any(d.glob(".user-data-left*"))]
    for d in live:
        if (d / DESKTOP_CFG).exists():
            return d / DESKTOP_CFG
    return (live[0] if live else cands[0]) / DESKTOP_CFG


def desktop_warnings(a: Agent) -> list[str]:
    out = []
    for d in desktop_candidates():
        f = d / DESKTOP_CFG
        if f.exists() and norm(f) != norm(a.path()):
            try:
                n = len(read_root(f, ("mcpServers",))[0])
            except Exception:
                n = 0
            if n:
                out.append(f"Ancien fichier ignoré par Claude Desktop, contient {n} serveur(s) : {f}")
    return out


def opencode_warnings(a: Agent) -> list[str]:
    out = []
    p = a.path()
    for name in ("config.json", "opencode.json", "opencode.jsonc"):
        f = p.parent / name
        if f.exists() and norm(f) != norm(p):
            try:
                s = read_root(f, ("mcp",))[0]
            except Exception as e:
                out.append(f"{f.name} est aussi lu par OpenCode mais il est illisible : {e}")
                continue
            if s:
                out.append(f"OpenCode lit aussi {f} et le fusionne avec ce fichier : il contient "
                           f"{', '.join(s)}. Évitez de déclarer les serveurs à deux endroits.")
    return out


def _vsix(ext_id: str, filename: str) -> Path:
    return vscode_user() / "globalStorage" / ext_id / "settings" / filename


CLAUDE_CODE_USERS = (
    "La commande « claude » dans un terminal (CMD, PowerShell, Windows Terminal…)",
    "L'extension Claude Code de VS Code (ou de Cursor, Windsurf…)",
    "L'onglet « Code » de l'application Claude Desktop",
)

AGENTS: list[Agent] = [
    # ------------------------------------------------------------ Claude
    Agent("claude_code", "Claude Code", "Claude Code", lambda: HOME / ".claude.json",
          stdio_type="stdio", http_type="http", sse_type="sse",
          clis=("claude",), dirs=lambda: [HOME / ".claude"],
          group="Claude (Anthropic)", label="Claude Code · CLI, VS Code, Desktop",
          subtitle="Un seul fichier pour la CLI, l'extension VS Code et l'onglet « Code » de Claude Desktop",
          used_by=CLAUDE_CODE_USERS,
          hint="Un serveur ajouté ici est disponible dans ces trois façons d'utiliser Claude Code. "
               "Ce n'est pas le fichier du chat de Claude Desktop : voir « Claude Desktop · Chat ».",
          note="Serveurs de portée utilisateur : disponibles dans tous les projets.",
          reload="Relancez la session Claude Code (ou tapez /mcp)."),
    Agent("claude_desktop", "Claude Desktop · Chat", "Desktop Chat", claude_desktop_file, remote=False,
          dirs=lambda: desktop_candidates() + [LOCALAPPDATA / "AnthropicClaude"],
          warnings=desktop_warnings,
          group="Claude (Anthropic)",
          subtitle="Les conversations de l'application Claude Desktop (onglet « Chat »)",
          used_by=("L'onglet « Chat » de l'application Claude Desktop",),
          hint="Ne concerne pas Claude Code : la CLI, l'extension VS Code et l'onglet « Code » de "
               "Desktop utilisent « Claude Code · CLI, VS Code, Desktop ».",
          note="Il ne lance que des commandes : un serveur distant (URL) y passe par « npx -y mcp-remote ».",
          reload="Redémarrez complètement Claude Desktop (icône de la barre des tâches › Quitter)."),
    # ------------------------------------------------------------ éditeurs
    Agent("vscode", "VS Code — GitHub Copilot", "VS Code", lambda: vscode_user() / "mcp.json",
          root=("servers",), stdio_type="stdio", http_type="http", sse_type="sse",
          clis=("code",), dirs=lambda: [vscode_user()],
          group="Éditeurs de code", label="VS Code · GitHub Copilot",
          used_by=("Le chat GitHub Copilot de VS Code (mode Agent)",),
          hint="Ne concerne ni l'extension Claude Code, ni Cline, Roo Code ou Kilo Code : "
               "chacun a son propre fichier.",
          note="Clé « servers » (et non « mcpServers »).",
          reload="Dans VS Code : « MCP: List Servers » › Redémarrer, ou rechargez la fenêtre."),
    Agent("cursor", "Cursor", "Cursor", lambda: HOME / ".cursor" / "mcp.json",
          clis=("cursor",), dirs=lambda: [HOME / ".cursor"],
          group="Éditeurs de code", used_by=("L'agent intégré de Cursor (chat, Composer)",)),
    Agent("windsurf", "Windsurf", "Windsurf", lambda: HOME / ".codeium" / "windsurf" / "mcp_config.json",
          url_keys={"http": "serverUrl", "sse": "serverUrl"}, disable="disabled",
          clis=("windsurf",), dirs=lambda: [HOME / ".codeium" / "windsurf"],
          group="Éditeurs de code", used_by=("Cascade, l'agent intégré de Windsurf",)),
    Agent("kiro", "Kiro", "Kiro", lambda: HOME / ".kiro" / "settings" / "mcp.json", disable="disabled",
          clis=("kiro",), dirs=lambda: [HOME / ".kiro"],
          group="Éditeurs de code", used_by=("L'agent intégré de Kiro",)),
    # ------------------------------------------------------------ extensions VS Code
    Agent("cline", "Cline (extension VS Code)", "Cline",
          lambda: _vsix("saoudrizwan.claude-dev", "cline_mcp_settings.json"),
          stdio_type="stdio", http_type="streamableHttp", sse_type="sse", disable="disabled",
          dirs=lambda: [vscode_user() / "globalStorage" / "saoudrizwan.claude-dev"],
          group="Extensions VS Code", label="Cline", used_by=("L'extension Cline dans VS Code",)),
    Agent("roo", "Roo Code (extension VS Code)", "Roo Code",
          lambda: _vsix("rooveterinaryinc.roo-cline", "mcp_settings.json"),
          stdio_type="stdio", http_type="streamable-http", sse_type="sse", disable="disabled",
          dirs=lambda: [vscode_user() / "globalStorage" / "rooveterinaryinc.roo-cline"],
          group="Extensions VS Code", label="Roo Code", used_by=("L'extension Roo Code dans VS Code",)),
    Agent("kilo", "Kilo Code (extension VS Code)", "Kilo Code",
          lambda: _vsix("kilocode.kilo-code", "mcp_settings.json"),
          stdio_type="stdio", http_type="streamable-http", sse_type="sse", disable="disabled",
          dirs=lambda: [vscode_user() / "globalStorage" / "kilocode.kilo-code"],
          group="Extensions VS Code", label="Kilo Code", used_by=("L'extension Kilo Code dans VS Code",)),
    # ------------------------------------------------------------ ligne de commande
    Agent("opencode", "OpenCode", "OpenCode", lambda: xdg_config() / "opencode" / "opencode.json",
          root=("mcp",), command_array=True, env_key="environment", disable="enabled", strict=True,
          clis=("opencode",), dirs=lambda: [xdg_config() / "opencode"], warnings=opencode_warnings,
          group="Agents en ligne de commande", used_by=("La commande « opencode » dans un terminal",),
          note="Format OpenCode : \"type\": \"local\" + \"command\": [exe, arguments…], ou \"type\": \"remote\" + \"url\"."),
    Agent("codex", "Codex CLI (OpenAI)", "Codex", lambda: HOME / ".codex" / "config.toml",
          root=("mcp_servers",), fmt="toml", disable="enabled", headers_key="http_headers", cwd_ok=True,
          clis=("codex",), dirs=lambda: [HOME / ".codex"],
          group="Agents en ligne de commande", label="Codex (OpenAI)",
          used_by=("La commande « codex » dans un terminal", "L'extension Codex de VS Code (même fichier que la CLI)"),
          note="Fichier TOML : sections [mcp_servers.<nom>]. Le reste du fichier est conservé."),
    Agent("gemini", "Gemini CLI", "Gemini", lambda: HOME / ".gemini" / "settings.json",
          url_keys={"http": "httpUrl", "sse": "url"}, cwd_ok=True,
          clis=("gemini",), dirs=lambda: [HOME / ".gemini"],
          group="Agents en ligne de commande", used_by=("La commande « gemini » dans un terminal",)),
    Agent("qwen", "Qwen Code", "Qwen", lambda: HOME / ".qwen" / "settings.json",
          url_keys={"http": "httpUrl", "sse": "url"}, cwd_ok=True,
          clis=("qwen",), dirs=lambda: [HOME / ".qwen"],
          group="Agents en ligne de commande", used_by=("La commande « qwen » dans un terminal",)),
    Agent("copilot_cli", "GitHub Copilot CLI", "Copilot CLI", lambda: HOME / ".copilot" / "mcp-config.json",
          stdio_type="local", http_type="http", sse_type="sse", defaults={"tools": ["*"]},
          dirs=lambda: [HOME / ".copilot"],
          group="Agents en ligne de commande", used_by=("La commande « copilot » dans un terminal",),
          hint="Différent du chat Copilot de VS Code, qui a son propre fichier."),
    # ------------------------------------------------------------ autres
    Agent("lmstudio", "LM Studio", "LM Studio", lambda: HOME / ".lmstudio" / "mcp.json",
          dirs=lambda: [HOME / ".lmstudio"], used_by=("Le chat de LM Studio",)),
]
AGENT_BY_ID = {a.id: a for a in AGENTS}
# Lecteur « tous formats » pour le JSON collé depuis une documentation
GENERIC = Agent("generic", "Générique", "JSON", lambda: HOME, stdio_type="stdio", http_type="http", sse_type="sse")


def parse_snippet(obj) -> dict:
    """Accepte {"mcpServers": {...}}, {"servers": {...}}, {"mcp": {...}}, {"nom": {...}} ou un serveur seul."""
    def is_srv(v):
        return isinstance(v, dict) and any(k in v for k in ("command", "url", "httpUrl", "serverUrl"))

    if not isinstance(obj, dict):
        raise ValueError("Le JSON doit être un objet { … }.")
    for k in ("mcpServers", "servers", "mcp", "mcp_servers", "context_servers"):
        v = obj.get(k)
        if isinstance(v, dict) and v and all(is_srv(x) for x in v.values()):
            return v
    if is_srv(obj):
        return {"": obj}
    if obj and all(is_srv(v) for v in obj.values()):
        return obj
    raise ValueError("Format non reconnu. Attendu : {\"mcpServers\": {\"nom\": {…}}}, "
                     "{\"nom\": {\"command\": …}} ou un serveur seul {\"command\": …}.")


# =========================================================================== #
#  Réglages, sauvegardes, accès aux fichiers
# =========================================================================== #
class Settings:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(self.d, dict):
                self.d = {}
        except Exception:
            self.d = {}
        self.d.setdefault("paths", {})
        self.d.setdefault("parked", {})
        self.d.setdefault("show_all", False)

    def save(self):
        write_text_atomic(SETTINGS_FILE, json.dumps(self.d, indent=2, ensure_ascii=False) + "\n")

    def parked(self, aid: str) -> dict:
        return self.d["parked"].setdefault(aid, {})

    def parked_of(self, aid: str) -> dict:
        return self.d["parked"].get(aid) or {}


def backup_file(a: Agent, path: Path):
    if not path.exists():
        return
    d = BACKUP_DIR / a.id
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, d / f"{now_stamp()}__{path.name}")
    for old in sorted(d.iterdir())[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def modify_file(a: Agent, fn) -> bool:
    """Relit le fichier, applique fn(serveurs) puis réécrit seulement si quelque chose a changé."""
    p = a.path()
    text = p.read_text(encoding="utf-8-sig") if p.exists() else ""
    if a.fmt == "toml":
        if tomllib is None:
            raise RuntimeError("Python 3.11 ou plus récent est nécessaire pour le TOML.")
        data = tomllib.loads(text) if text.strip() else {}
        cur = data.get("mcp_servers")
        servers = dict(cur) if isinstance(cur, dict) else {}
        before = json.dumps(servers, default=str)
        fn(servers)
        if json.dumps(servers, default=str) == before:
            return False
        backup_file(a, p)
        write_text_atomic(p, toml_replace_mcp(text, servers))
        return True
    data = parse_json_text(text)[0] if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("Le fichier ne contient pas un objet JSON { … }.")
    node = data
    for k in a.root[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    cur = node.get(a.root[-1])
    servers = cur if isinstance(cur, dict) else {}
    before = json.dumps(servers)
    fn(servers)
    if isinstance(cur, dict) and json.dumps(servers) == before:
        return False
    node[a.root[-1]] = servers
    backup_file(a, p)
    write_text_atomic(p, json.dumps(data, indent=detect_indent(text), ensure_ascii=False) + "\n")
    return True


def replace_key(s: dict, old: str, new: str, val):
    """Remplace s[old] par s[new]=val en gardant la position (supprime un éventuel s[new] existant)."""
    items = [(k, v) for k, v in s.items() if k != new or k == old]
    s.clear()
    for k, v in items:
        s[new if k == old else k] = val if k == old else v


@dataclass
class Srv:
    name: str
    native: dict
    canon: dict
    parked: bool = False


@dataclass
class AgentState:
    servers: list = field(default_factory=list)
    err: str | None = None
    warns: list = field(default_factory=list)
    lossy: bool = False
    detected: bool = False
    exists: bool = False

    def find(self, name: str) -> Srv | None:
        return next((s for s in self.servers if s.name == name), None)


def load_state(a: Agent, settings: Settings) -> AgentState:
    p = a.path()
    st = AgentState(exists=p.exists())
    natives = {}
    try:
        natives, st.lossy = read_root(p, a.root, a.fmt)
    except Exception as e:
        st.err = f"Fichier illisible : {e}"
    for n, v in natives.items():
        if isinstance(v, dict):
            st.servers.append(Srv(n, v, a.to_canon(v)))
        else:
            st.warns.append(f"Entrée « {n} » ignorée : ce n'est pas un objet.")
    for n, v in settings.parked_of(a.id).items():
        if n not in natives:
            c = a.to_canon(v)
            c["enabled"] = False
            st.servers.append(Srv(n, v, c, parked=True))
    if a.warnings:
        try:
            st.warns += a.warnings(a)
        except Exception:
            pass
    st.detected = (st.exists or any(Path(d).exists() for d in a.dirs())
                   or any(shutil.which(c) for c in a.clis))
    return st


# =========================================================================== #
#  Test d'un serveur MCP
# =========================================================================== #
INIT_REQ = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "gestionnaire-mcp", "version": APP_VERSION}}}
TOOLS_REQ = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def kill_tree(proc: subprocess.Popen):
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            proc.kill()
    except Exception:
        pass


def test_stdio(c: dict, log, timeout_init=90, timeout_tools=30):
    cmd = c["command"]
    args = [shutil.which(cmd) or cmd] + c["args"]
    env = os.environ.copy()
    env.update(c["env"])
    log(f"▶ Lancement : {' '.join(args)}\n")
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd=c["cwd"] or None, creationflags=NO_WINDOW)
    except Exception as e:
        log(f"✖ Impossible de lancer la commande : {e}\n")
        return
    out_q: queue.Queue = queue.Queue()
    err_lines: list[str] = []

    def rd_out():
        for line in iter(proc.stdout.readline, b""):
            out_q.put(line.decode("utf-8", "replace"))
        out_q.put(None)

    def rd_err():
        for line in iter(proc.stderr.readline, b""):
            err_lines.append(line.decode("utf-8", "replace"))

    threading.Thread(target=rd_out, daemon=True).start()
    threading.Thread(target=rd_err, daemon=True).start()

    def send(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_id(i, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = out_q.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None and out_q.empty():
                    return None
                continue
            if line is None:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log(f"  (sortie non JSON) {line[:200]}\n")
                continue
            if msg.get("id") == i:
                return msg
        return None

    try:
        send(INIT_REQ)
        resp = wait_id(1, timeout_init)
        if not resp:
            log("✖ Pas de réponse à « initialize ».\n")
        elif "error" in resp:
            log(f"✖ Erreur initialize : {resp['error']}\n")
        else:
            info = resp.get("result", {}).get("serverInfo", {})
            log(f"✔ Connecté : {info.get('name', '?')} {info.get('version', '')}\n")
            send(INITIALIZED)
            send(TOOLS_REQ)
            tools = wait_id(2, timeout_tools)
            if tools and "result" in tools:
                lst = tools["result"].get("tools", [])
                log(f"✔ {len(lst)} outil(s) :\n")
                for t in lst:
                    desc = (t.get("description") or "").strip().splitlines()
                    log(f"   • {t.get('name')}  —  {desc[0][:100] if desc else ''}\n")
            else:
                log("⚠ Pas de réponse à « tools/list ».\n")
    except Exception as e:
        log(f"✖ Erreur : {e}\n")
    finally:
        kill_tree(proc)
        time.sleep(0.3)
        if err_lines:
            log("\n--- stderr (fin) ---\n" + "".join(err_lines[-25:]))
        log("\n■ Terminé.\n")


def test_http(c: dict, log):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update(c["headers"])
    log(f"▶ POST {c['url']}\n")
    try:
        req = urllib.request.Request(c["url"], data=json.dumps(INIT_REQ).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(200000).decode("utf-8", "replace")
            log(f"✔ HTTP {r.status} ({r.headers.get('Content-Type')})\n")
            msg = None
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line.startswith("{"):
                    try:
                        msg = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass
            if msg and "result" in msg:
                info = msg["result"].get("serverInfo", {})
                log(f"✔ Serveur : {info.get('name', '?')} {info.get('version', '')}\n")
            else:
                log(body[:1500] + "\n")
    except urllib.error.HTTPError as e:
        log(f"✖ HTTP {e.code} : {e.reason}\n")
        if e.code == 401:
            log("  (authentification requise : normal pour un serveur OAuth, l'agent s'en charge)\n")
    except Exception as e:
        log(f"✖ {e}\n")
    log("\n■ Terminé.\n")


# =========================================================================== #
#  Apparence : couleurs, icônes, infobulles
# =========================================================================== #
PALETTES = {
    "light": {
        "bg": "#f4f6fa", "surface": "#ffffff", "side": "#eceff5", "side_hover": "#e1e5ee",
        "border": "#d9dee8", "text": "#1d2433", "muted": "#667085", "faint": "#98a2b3",
        "accent": "#4f46e5", "accent_hover": "#4338ca", "accent_fg": "#ffffff", "accent_soft": "#eceafd",
        "header": "#1e1b4b", "header_fg": "#ffffff", "header_muted": "#b9b6f0", "header_btn": "#312c78",
        "ok": "#16a34a", "off": "#98a2b3", "warn": "#c2410c", "err": "#dc2626",
        "warn_bg": "#fff3e6", "row_alt": "#f8f9fc", "sel": "#dedbfb", "sel_fg": "#1d2433",
        "code_bg": "#f6f7fb", "danger": "#c0262d",
    },
    "dark": {
        "bg": "#15171c", "surface": "#1d2027", "side": "#191c22", "side_hover": "#262b35",
        "border": "#2f3541", "text": "#e6e9ef", "muted": "#9aa3b2", "faint": "#6b7280",
        "accent": "#7c74ff", "accent_hover": "#958eff", "accent_fg": "#ffffff", "accent_soft": "#272551",
        "header": "#100e1c", "header_fg": "#ffffff", "header_muted": "#a29ee6", "header_btn": "#272352",
        "ok": "#4ade80", "off": "#6b7280", "warn": "#fb923c", "err": "#f87171",
        "warn_bg": "#3a2716", "row_alt": "#21242c", "sel": "#383479", "sel_fg": "#ffffff",
        "code_bg": "#171a20", "danger": "#f87171",
    },
}
SECRET_KEY = re.compile(r"key|token|secret|pass|auth|bearer|credential|cookie|session", re.I)
SECRET_IN_TEXT = re.compile(r"(?i)(bearer\s+|(?:token|key|secret|password|pwd)\s*[:=]\s*)([^\s&\"']{4,})")
MASK = "••••••••"
TYPE_LABELS = {"stdio": "Local", "http": "Distant · HTTP", "sse": "Distant · SSE"}
SHORTCUTS = [("Ctrl+N", "Ajouter un serveur"), ("Entrée ou F2", "Modifier le serveur sélectionné"),
             ("Espace", "Activer / désactiver"), ("Ctrl+D", "Copier vers d'autres agents"),
             ("Ctrl+T", "Tester le serveur"), ("Suppr", "Supprimer"),
             ("Ctrl+V", "Coller du JSON (pré-rempli avec le presse-papiers)"), ("Ctrl+E", "Exporter les serveurs"),
             ("Ctrl+F", "Rechercher un serveur"), ("Échap", "Effacer la recherche"), ("F5", "Relire les fichiers")]


def windows_prefers_dark() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def enable_dpi_awareness():
    """Texte net sur les écrans à haute résolution (à appeler avant de créer la fenêtre)."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_titlebar_theme(win, dark: bool):
    """Barre de titre Windows claire ou sombre."""
    if not IS_WIN:
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 11 / 10 ancien)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def _png_b64(w: int, h: int, pixel) -> str:
    """Encode une image RGBA en PNG (base64) sans bibliothèque externe."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(pixel(x, y))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def _rgb(c: str) -> tuple:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def make_dot(color: str, size: int, hollow=False) -> tk.PhotoImage:
    """Pastille d'état (pleine ou en anneau), lissée."""
    rgb, c, r = _rgb(color), (size - 1) / 2, size * 0.28
    t, n = max(1.3, size * 0.10), 4

    def px(x, y):
        cov = 0
        for sy in range(n):
            for sx in range(n):
                d = math.hypot(x + (sx + .5) / n - .5 - c, y + (sy + .5) / n - .5 - c)
                if d <= r and (not hollow or d >= r - t):
                    cov += 1
        return (*rgb, int(255 * cov / (n * n)))
    return tk.PhotoImage(data=_png_b64(size, size, px))


def make_logo(size: int, accent: str = "#4f46e5") -> tk.PhotoImage:
    """Icône de l'application : carré arrondi avec un moyeu et trois connecteurs."""
    a, w = _rgb(accent), (255, 255, 255)
    rad, c, n = size * 0.24, size / 2, 3
    sats = [(c + size * 0.33 * math.cos(math.radians(t)), c + size * 0.33 * math.sin(math.radians(t)))
            for t in (-90, 30, 150)]

    def inside(x, y):
        dx = max(rad - x, 0, x - (size - rad))
        dy = max(rad - y, 0, y - (size - rad))
        return dx * dx + dy * dy <= rad * rad

    def px(x, y):
        cov = white = 0
        for sy in range(n):
            for sx in range(n):
                X, Y = x + (sx + .5) / n, y + (sy + .5) / n
                if not inside(X, Y):
                    continue
                cov += 1
                d = math.hypot(X - c, Y - c)
                if size * 0.13 <= d <= size * 0.21 or any(math.hypot(X - sx_, Y - sy_) <= size * 0.075
                                                          for sx_, sy_ in sats):
                    white += 1
        if not cov:
            return (0, 0, 0, 0)
        k = white / cov
        return (*(int(a[i] * (1 - k) + w[i] * k) for i in range(3)), int(255 * cov / (n * n)))
    return tk.PhotoImage(data=_png_b64(size, size, px))


def mask_text(s: str) -> str:
    return SECRET_IN_TEXT.sub(lambda m: m.group(1) + MASK, s)


def mask_native(obj, key: str = ""):
    """Copie d'un objet de configuration dont les valeurs secrètes sont masquées."""
    if isinstance(obj, dict):
        return {k: mask_native(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_native(v, key) for v in obj]
    if isinstance(obj, str):
        return MASK if key and SECRET_KEY.search(key) and obj else mask_text(obj)
    return obj


class Tooltip:
    def __init__(self, app: "App"):
        self.app, self.win, self.job, self.key = app, None, None, None

    def show_later(self, key, text, x, y):
        if key == self.key:
            return
        self.hide()
        self.key = key
        if text:
            self.job = self.app.after(450, lambda: self._show(text, x, y))

    def _show(self, text, x, y):
        self.job = None
        P, px = self.app.P, self.app.px
        self.win = w = tk.Toplevel(self.app)
        w.wm_overrideredirect(True)
        w.configure(bg=P["border"])
        tk.Label(w, text=text, justify="left", bg=P["surface"], fg=P["text"], font=self.app.fonts["small"],
                 padx=px(10), pady=px(6), wraplength=px(440)).pack(padx=1, pady=1)
        w.wm_geometry(f"+{x + px(14)}+{y + px(18)}")
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass

    def hide(self, _e=None):
        if self.job:
            self.app.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None
        self.key = None

    def attach(self, widget, text):
        widget.bind("<Enter>", lambda e: self.show_later(id(widget), text() if callable(text) else text,
                                                         e.x_root, e.y_root), add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")


# =========================================================================== #
#  Fenêtres de dialogue
# =========================================================================== #
class Dialog(tk.Toplevel):
    """Fenêtre secondaire aux couleurs du thème, centrée sur sa fenêtre parente."""

    def __init__(self, app: "App", title: str, parent=None, modal=True):
        super().__init__(parent or app)
        self.app = app
        self.withdraw()
        self.title(title)
        self.transient(parent or app)
        self.configure(bg=app.P["bg"])
        self._modal = modal
        self._prev_grab = None
        self.bind("<Escape>", lambda e: self.destroy())

    def show(self, width: int, height: int | None = None):
        app = self.app
        self.update_idletasks()
        w = app.px(width)
        h = app.px(height) if height else self.winfo_reqheight()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() - w) // 2
        y = m.winfo_rooty() + max(0, (m.winfo_height() - h) // 3)
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        set_titlebar_theme(self, app.dark)
        self.deiconify()
        if self._modal:
            self._prev_grab = self.grab_current()
            try:
                self.grab_set()
            except tk.TclError:
                pass
        self.lift()
        self.focus_set()

    def destroy(self):
        prev = self._prev_grab
        super().destroy()
        if prev is not None and not isinstance(prev, str):
            try:
                if prev.winfo_exists():
                    prev.grab_set()
            except tk.TclError:
                pass


class TextDialog(Dialog):
    def __init__(self, app, title, text="", editable=True, ok_label="Valider", on_ok=None, hint="",
                 width=780, height=520, parent=None):
        super().__init__(app, title, parent=parent)
        px = app.px
        self.on_ok = on_ok
        self._q: queue.Queue = queue.Queue()
        self.after(80, self._drain)
        frm = ttk.Frame(self, padding=px(16))
        frm.pack(fill="both", expand=True)
        if hint:
            ttk.Label(frm, text=hint, style="Muted.TLabel", justify="left",
                      wraplength=px(width - 40)).pack(anchor="w", pady=(0, px(10)))
        bar = ttk.Frame(frm)
        bar.pack(side="bottom", fill="x", pady=(px(12), 0))
        self.txt, box = app.make_text(frm, code=True, wrap="none", undo=True)
        box.pack(fill="both", expand=True)
        self.txt.insert("1.0", text)
        if not editable:
            self.txt.configure(state="disabled")
        if on_ok:
            ttk.Button(bar, text=ok_label, style="Accent.TButton", command=self._ok).pack(side="right",
                                                                                         padx=(px(8), 0))
        ttk.Button(bar, text="Fermer", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Copier le texte", command=self._copy).pack(side="left")
        self.show(width, height)
        self.txt.focus_set()

    def append(self, s):
        """Peut être appelé depuis n'importe quel thread : le texte passe par une file vidée par l'interface."""
        self._q.put(s)

    def _drain(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        chunks = []
        while not self._q.empty():
            chunks.append(self._q.get_nowait())
        if chunks:
            self.txt.configure(state="normal")
            self.txt.insert("end", "".join(chunks))
            self.txt.see("end")
            self.txt.configure(state="disabled")
        self.after(80, self._drain)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.txt.get("1.0", "end-1c"))
        self.app.say("Texte copié dans le presse-papiers.", "ok")

    def _ok(self):
        if self.on_ok(self.txt.get("1.0", "end-1c")) is not False:
            self.destroy()


class InfoDialog(Dialog):
    """Petite fenêtre d'information : blocs (type, contenu)."""

    def __init__(self, app, title, blocks, width=600):
        super().__init__(app, title)
        px = app.px
        frm = ttk.Frame(self, padding=(px(24), px(20)))
        frm.pack(fill="both", expand=True)
        wrap = px(width - 60)
        for kind, val in blocks:
            if kind == "logo":
                ttk.Label(frm, image=app.icons["logo_big"]).pack(anchor="w", pady=(0, px(10)))
            elif kind == "h":
                ttk.Label(frm, text=val, style="Title2.TLabel").pack(anchor="w", pady=(0, px(6)))
            elif kind == "field":
                ttk.Label(frm, text=val, style="Field.TLabel").pack(anchor="w", pady=(px(12), px(2)))
            elif kind == "p":
                ttk.Label(frm, text=val, wraplength=wrap, justify="left").pack(anchor="w")
            elif kind == "small":
                ttk.Label(frm, text=val, style="Small.TLabel", wraplength=wrap, justify="left").pack(
                    anchor="w", pady=(px(4), 0))
            elif kind == "muted":
                ttk.Label(frm, text=val, style="Muted.TLabel", wraplength=wrap, justify="left").pack(
                    anchor="w", pady=(px(8), 0))
            elif kind == "link":
                lbl = ttk.Label(frm, text=val, style="Link.TLabel", cursor="hand2")
                lbl.pack(anchor="w", pady=(px(4), 0))
                lbl.bind("<Button-1>", lambda e, u=val: webbrowser.open(u))
            elif kind == "table":
                g = ttk.Frame(frm)
                g.pack(anchor="w", fill="x", pady=(px(4), 0))
                for i, (k, v) in enumerate(val):
                    ttk.Label(g, text=k, style="Chip.TLabel").grid(row=i, column=0, sticky="w", pady=px(2))
                    ttk.Label(g, text=v).grid(row=i, column=1, sticky="w", padx=(px(12), 0))
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(px(18), 0))
        ttk.Button(bar, text="Fermer", style="Accent.TButton", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda e: self.destroy())
        self.show(width)


def parse_kv(text: str, sep: str, what: str) -> dict:
    out = {}
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if sep not in ln:
            raise ValueError(f"{what} : la ligne « {ln.strip()} » doit être de la forme NOM{sep}valeur.")
        k, v = ln.split(sep, 1)
        out[k.strip()] = v.strip()
    return out


class ServerDialog(Dialog):
    """Édition d'un serveur : formulaire simple + onglet JSON au format exact de l'agent."""

    def __init__(self, app, agent: Agent, name: str = "", native: dict | None = None):
        editing = native is not None
        super().__init__(app, f"{'Modifier' if editing else 'Nouveau'} serveur MCP — {agent.label}")
        px = app.px
        self.agent = agent
        self.result = None
        canon = agent.to_canon(native) if editing else empty_canon()
        self.extra = canon["extra"]
        self._tab = 0
        self._switching = False

        frm = ttk.Frame(self, padding=(px(22), px(18)))
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Modifier le serveur" if editing else "Nouveau serveur",
                  style="Title2.TLabel").pack(anchor="w")
        ttk.Label(frm, text=f"Sera écrit dans {agent.label}  ·  {agent.path()}", style="Muted.TLabel",
                  wraplength=px(720), justify="left").pack(anchor="w", pady=(px(2), px(14)))

        top = ttk.Frame(frm)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text="NOM DU SERVEUR", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=name)
        self.name_entry = ttk.Entry(top, textvariable=self.name_var)
        self.name_entry.grid(row=1, column=0, sticky="ew", pady=(px(3), 0))
        self.en_var = tk.BooleanVar(value=canon["enabled"])
        ttk.Checkbutton(top, text="Actif", variable=self.en_var).grid(row=1, column=1, padx=(px(18), 0))

        bar = ttk.Frame(frm)
        bar.pack(side="bottom", fill="x", pady=(px(12), 0))
        test_btn = ttk.Button(bar, text="▶  Tester", command=self._test)
        test_btn.pack(side="left")
        app.tip.attach(test_btn, "Lance le serveur avec les valeurs saisies et liste ses outils, sans rien enregistrer.")
        ttk.Button(bar, text="Enregistrer", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(bar, text="Annuler", command=self.destroy).pack(side="right", padx=(0, px(8)))
        self.err_lbl = ttk.Label(frm, style="Error.TLabel", wraplength=px(720), justify="left")
        self.err_lbl.pack(side="bottom", anchor="w", pady=(px(8), 0))

        self.nb = ttk.Notebook(frm)
        self.nb.pack(fill="both", expand=True, pady=(px(16), 0))
        f = ttk.Frame(self.nb, style="Surface.TFrame", padding=px(16))
        self.nb.add(f, text="Formulaire")
        f.columnconfigure(0, weight=1)
        ttk.Label(f, text="TYPE DE SERVEUR", style="CardField.TLabel").grid(row=0, column=0, sticky="w")
        tf = ttk.Frame(f, style="Surface.TFrame")
        tf.grid(row=1, column=0, sticky="w", pady=(px(4), px(12)))
        self.type_var = tk.StringVar(value=canon["type"])
        for v, txt in (("stdio", "Local — commande lancée sur ce PC"), ("http", "Distant — HTTP"),
                       ("sse", "Distant — SSE")):
            ttk.Radiobutton(tf, text=txt, value=v, variable=self.type_var, style="Seg.Toolbutton",
                            command=self._type_changed).pack(side="left", padx=(0, px(6)))

        self.sf = sf = ttk.Frame(f, style="Surface.TFrame")
        sf.grid(row=2, column=0, sticky="nsew")
        sf.columnconfigure(0, weight=1)
        ttk.Label(sf, text="COMMANDE", style="CardField.TLabel").grid(row=0, column=0, sticky="w")
        cf = ttk.Frame(sf, style="Surface.TFrame")
        cf.grid(row=1, column=0, sticky="ew", pady=(px(3), px(10)))
        self.cmd_var = tk.StringVar()
        ttk.Entry(cf, textvariable=self.cmd_var).pack(side="left", fill="x", expand=True)
        ttk.Button(cf, text="Parcourir…", command=self._browse).pack(side="left", padx=(px(6), 0))
        ttk.Label(sf, text="ARGUMENTS — un par ligne", style="CardField.TLabel").grid(row=2, column=0, sticky="w")
        self.args_txt, box = app.make_text(sf, code=True, height=5, undo=True, wrap="none")
        box.grid(row=3, column=0, sticky="nsew", pady=(px(3), px(10)))
        ttk.Label(sf, text="VARIABLES D'ENVIRONNEMENT — NOM=valeur, une par ligne",
                  style="CardField.TLabel").grid(row=4, column=0, sticky="w")
        self.env_txt, box = app.make_text(sf, code=True, height=3, undo=True, wrap="none")
        box.grid(row=5, column=0, sticky="nsew", pady=(px(3), px(10)))
        self.cwd_var = tk.StringVar()
        if agent.cwd_ok:
            ttk.Label(sf, text="DOSSIER DE TRAVAIL — facultatif", style="CardField.TLabel").grid(
                row=6, column=0, sticky="w")
            ttk.Entry(sf, textvariable=self.cwd_var).grid(row=7, column=0, sticky="ew", pady=(px(3), 0))
        sf.rowconfigure(3, weight=1)

        self.rf = rf = ttk.Frame(f, style="Surface.TFrame")
        rf.grid(row=2, column=0, sticky="nsew")
        rf.columnconfigure(0, weight=1)
        ttk.Label(rf, text="URL DU SERVEUR", style="CardField.TLabel").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        ttk.Entry(rf, textvariable=self.url_var).grid(row=1, column=0, sticky="ew", pady=(px(3), px(10)))
        ttk.Label(rf, text="EN-TÊTES HTTP — Nom: valeur, un par ligne", style="CardField.TLabel").grid(
            row=2, column=0, sticky="w")
        self.hdr_txt, box = app.make_text(rf, code=True, height=4, undo=True, wrap="none")
        box.grid(row=3, column=0, sticky="nsew", pady=(px(3), px(10)))
        if not agent.remote:
            ttk.Label(rf, style="CardWarn.TLabel", wraplength=px(660), justify="left",
                      text=f"{agent.label} ne lit que des commandes : l'URL sera enregistrée sous la forme "
                           "« npx -y mcp-remote <url> » (Node.js requis).").grid(row=4, column=0, sticky="w")
        f.rowconfigure(2, weight=1)

        j = ttk.Frame(self.nb, style="Surface.TFrame", padding=px(16))
        self.nb.add(j, text=f"JSON ({agent.short})")
        ttk.Label(j, style="CardMuted.TLabel", wraplength=px(680), justify="left",
                  text="Le serveur tel qu'il sera écrit dans le fichier. Vous pouvez aussi coller ici le JSON "
                       "d'une documentation, dans n'importe quel format (Claude, OpenCode, VS Code…) : "
                       "il sera converti au format de l'agent.").pack(anchor="w", pady=(0, px(8)))
        self.json_txt, box = app.make_text(j, code=True, undo=True, wrap="none", height=14)
        box.pack(fill="both", expand=True)
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)

        self._fill(canon)
        self.bind("<Control-Return>", lambda e: self._ok())
        self.show(800, 700)
        self.name_entry.focus_set()

    # ------------------------------------------------------------------ form
    def _error(self, msg: str):
        self.err_lbl.configure(text=f"⚠  {msg}" if msg else "")

    def _fill(self, c: dict):
        self.type_var.set(c["type"])
        self.en_var.set(c["enabled"])
        self.cmd_var.set(c["command"])
        self.cwd_var.set(c["cwd"])
        self.url_var.set(c["url"])
        for w, val in ((self.args_txt, "\n".join(c["args"])),
                       (self.env_txt, "\n".join(f"{k}={v}" for k, v in c["env"].items())),
                       (self.hdr_txt, "\n".join(f"{k}: {v}" for k, v in c["headers"].items()))):
            w.delete("1.0", "end")
            w.insert("1.0", val)
        self._type_changed()

    def _type_changed(self):
        if self.type_var.get() == "stdio":
            self.rf.grid_remove()
            self.sf.grid()
        else:
            self.sf.grid_remove()
            self.rf.grid()

    def _browse(self):
        p = filedialog.askopenfilename(parent=self, title="Choisir l'exécutable du serveur")
        if p:
            self.cmd_var.set(os.path.normpath(p))

    def _form_canon(self) -> dict:
        c = empty_canon()
        c["type"] = self.type_var.get()
        c["enabled"] = self.en_var.get()
        c["command"] = self.cmd_var.get().strip()
        c["args"] = [ln.strip() for ln in self.args_txt.get("1.0", "end-1c").splitlines() if ln.strip()]
        c["env"] = parse_kv(self.env_txt.get("1.0", "end-1c"), "=", "Variables d'environnement")
        c["cwd"] = self.cwd_var.get().strip()
        c["url"] = self.url_var.get().strip()
        c["headers"] = parse_kv(self.hdr_txt.get("1.0", "end-1c"), ":", "En-têtes")
        c["extra"] = copy.deepcopy(self.extra)
        return c

    def _from_json(self) -> tuple[str, dict]:
        txt = self.json_txt.get("1.0", "end-1c").strip()
        if not txt:
            raise ValueError("Le JSON est vide.")
        servers = parse_snippet(parse_json_text(txt)[0])
        if len(servers) != 1:
            raise ValueError("Ce JSON contient plusieurs serveurs : utilisez « Coller du JSON » "
                             "dans la fenêtre principale.")
        (n, nat), = servers.items()
        c = self.agent.to_canon(nat)
        if "enabled" not in nat and "disabled" not in nat:
            c["enabled"] = self.en_var.get()
        self.extra = c["extra"]
        return n, c

    def _current_canon(self) -> dict:
        if self.nb.index("current") == 1:
            n, c = self._from_json()
            if n:
                self.name_var.set(n)
            return c
        return self._form_canon()

    def _tab_changed(self, _e=None):
        if self._switching:
            return
        new = self.nb.index("current")
        try:
            if new == 1 and self._tab == 0:
                nat = self.agent.from_canon(self._form_canon(), keep_extra=True)
                self.json_txt.delete("1.0", "end")
                self.json_txt.insert("1.0", json.dumps(nat, indent=2, ensure_ascii=False))
            elif new == 0 and self._tab == 1:
                n, c = self._from_json()
                if n:
                    self.name_var.set(n)
                self._fill(c)
        except ValueError as e:
            self._error(str(e))
            self._switching = True
            self.nb.select(self._tab)
            self._switching = False
            return
        self._error("")
        self._tab = new

    def _test(self):
        try:
            c = self._current_canon()
            if canon_invalid(c):
                raise ValueError("Indiquez d'abord la commande ou l'URL du serveur.")
        except ValueError as e:
            self._error(str(e))
            return
        self._error("")
        self.app.run_test(self.name_var.get().strip() or "nouveau serveur", c, parent=self)

    def _ok(self):
        try:
            c = self._current_canon()
            name = self.name_var.get().strip()
            if not name:
                raise ValueError("Donnez un nom au serveur.")
            if c["type"] == "stdio" and not c["command"]:
                raise ValueError("Indiquez la commande à lancer.")
            if c["type"] != "stdio" and not c["url"]:
                raise ValueError("Indiquez l'URL du serveur.")
            native = self.agent.from_canon(c, keep_extra=True)
        except ValueError as e:
            self._error(str(e))
            return
        self.result = (name, native, c["enabled"])
        self.destroy()


class CopyDialog(Dialog):
    def __init__(self, app, names: list[str], agents: list[Agent], states: dict):
        super().__init__(app, "Copier vers d'autres agents")
        px = app.px
        self.result: list[Agent] = []
        frm = ttk.Frame(self, padding=(px(22), px(18)))
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Copier vers d'autres agents", style="Title2.TLabel").pack(anchor="w")
        ttk.Label(frm, text=f"Serveur(s) : {', '.join(names)}", wraplength=px(540),
                  justify="left").pack(anchor="w", pady=(px(4), 0))
        ttk.Label(frm, text="Le format est converti automatiquement pour chaque agent.",
                  style="Muted.TLabel").pack(anchor="w", pady=(px(2), px(12)))
        bar = ttk.Frame(frm)
        bar.pack(side="bottom", fill="x", pady=(px(14), 0))
        ttk.Button(bar, text="Tout cocher", command=lambda: self._all(True)).pack(side="left")
        ttk.Button(bar, text="Tout décocher", command=lambda: self._all(False)).pack(side="left", padx=px(6))
        ttk.Button(bar, text="Copier", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(bar, text="Annuler", command=self.destroy).pack(side="right", padx=(0, px(8)))
        card = ttk.Frame(frm, style="Card.TFrame", padding=(px(16), px(12)))
        card.pack(fill="both", expand=True)
        self.vars = []
        for g in GROUPS:
            ags = [a for a in agents if a.group == g]
            if not ags:
                continue
            ttk.Label(card, text=g.upper(), style="CardField.TLabel").pack(
                anchor="w", pady=(px(10) if self.vars else 0, px(2)))
            for a in ags:
                v = tk.BooleanVar(value=False)
                have = [n for n in names if states[a.id].find(n)]
                row = ttk.Frame(card, style="Surface.TFrame")
                row.pack(fill="x")
                ttk.Checkbutton(row, text=a.label, variable=v, style="Card.TCheckbutton").pack(side="left")
                if have:
                    ttk.Label(row, text=f"déjà présent : {', '.join(have)}", style="CardWarn.TLabel").pack(
                        side="left", padx=px(8))
                self.vars.append((a, v))
        self.bind("<Return>", lambda e: self._ok())
        self.show(600)

    def _all(self, on: bool):
        for _, v in self.vars:
            v.set(on)

    def _ok(self):
        self.result = [a for a, v in self.vars if v.get()]
        self.destroy()


class BackupDialog(Dialog):
    def __init__(self, app, agent: Agent, files: list[Path]):
        super().__init__(app, f"Sauvegardes — {agent.label}")
        px = app.px
        self.agent, self.files = agent, files
        frm = ttk.Frame(self, padding=(px(22), px(18)))
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Restaurer une sauvegarde", style="Title2.TLabel").pack(anchor="w")
        ttk.Label(frm, text=f"Fichier : {agent.path()}", style="Muted.TLabel").pack(anchor="w", pady=(px(2), px(12)))
        bar = ttk.Frame(frm)
        bar.pack(side="bottom", fill="x", pady=(px(14), 0))
        ttk.Button(bar, text="Ouvrir le dossier", command=lambda: open_path(files[0].parent)).pack(side="left")
        ttk.Button(bar, text="Restaurer cette version", style="Accent.TButton", command=self._go).pack(side="right")
        ttk.Button(bar, text="Fermer", command=self.destroy).pack(side="right", padx=(0, px(8)))
        pw = ttk.PanedWindow(frm, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        self.lb = app.make_list(left)
        self.lb.pack(fill="both", expand=True)
        for f in files:
            ts = f.name.split("__")[0]
            try:
                ts = _dt.datetime.strptime(ts[:15], "%Y%m%d_%H%M%S").strftime("%d/%m/%Y  %H:%M:%S")
            except ValueError:
                pass
            self.lb.insert("end", f"  {ts}  ·  {f.stat().st_size} o")
        right = ttk.Frame(pw)
        self.preview, box = app.make_text(right, code=True, wrap="none", state="disabled")
        box.pack(fill="both", expand=True, padx=(px(10), 0))
        pw.add(left, weight=1)
        pw.add(right, weight=2)
        self.lb.bind("<<ListboxSelect>>", self._preview)
        self.lb.selection_set(0)
        self._preview()
        self.show(980, 600)

    def _preview(self, _e=None):
        i = self.lb.curselection()
        if not i:
            return
        try:
            txt = self.files[i[0]].read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            txt = f"(lecture impossible : {e})"
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", txt)
        self.preview.configure(state="disabled")

    def _go(self):
        i = self.lb.curselection()
        if not i:
            return
        if not messagebox.askyesno(APP_NAME, "Remplacer le fichier actuel par cette sauvegarde ?\n"
                                             "(l'état actuel est lui-même sauvegardé)", parent=self):
            return
        a, p = self.agent, self.agent.path()
        try:
            backup_file(a, p)
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.files[i[0]], p)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Restauration impossible :\n\n{e}", parent=self)
            return
        self.destroy()
        self.app.done(a, "sauvegarde restaurée")


# =========================================================================== #
#  Fenêtre principale
# =========================================================================== #
OVERVIEW = "__overview__"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.settings = Settings()
        for a in AGENTS:
            a.override = self.settings.d["paths"].get(a.id)
        self.states: dict[str, AgentState] = {}
        self.current: Agent | None = None
        self.rows: dict[str, object] = {}
        self.lossy_ok: set[str] = set()
        self.tk_widgets: list = []
        self.icons: dict[str, tk.PhotoImage] = {}
        self._ph = False
        self._jobs: list = []
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.style = ttk.Style(self)
        self._init_fonts()
        self.tip = Tooltip(self)
        self.search_var = tk.StringVar()
        self.reveal_var = tk.BooleanVar(value=False)
        self.show_all_var = tk.BooleanVar(value=bool(self.settings.d.get("show_all")))
        self.theme_var = tk.StringVar(value=self.settings.d.get("theme") or "auto")
        self.apply_theme(initial=True)
        self.title(APP_TITLE)
        self._app_icons = (make_logo(64), make_logo(32))
        self.iconphoto(True, *self._app_icons)
        self.minsize(self.px(1000), self.px(620))
        self._build_menu()
        self._build_ui()
        self._bind_keys()
        self._restore_geometry()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        set_titlebar_theme(self, self.dark)
        self.deiconify()
        if self.settings.d.get("zoomed"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass
        self.after(50, self._poll_jobs)

    # ============================================================ apparence
    def px(self, n) -> int:
        return int(round(n * self.scale))

    def _init_fonts(self):
        fams = set(tkfont.families(self))
        ui = "Segoe UI" if "Segoe UI" in fams else tkfont.nametofont("TkDefaultFont").actual("family")
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in fams else ui
        mono = next((m for m in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono") if m in fams), "Courier")
        for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkIconFont",
                  "TkTooltipFont", "TkSmallCaptionFont"):
            try:
                tkfont.nametofont(n).configure(family=ui, size=9 if "Small" in n or "Tooltip" in n else 10)
            except tk.TclError:
                pass
        self.fonts = {"base": (ui, 10), "bold": (ui, 10, "bold"), "small": (ui, 9), "small_bold": (ui, 8, "bold"),
                      "title": (semi, 18), "h2": (semi, 14), "brand": (semi, 13), "mono": (mono, 10),
                      "side": (ui, 10), "side_bold": (semi, 10)}

    def apply_theme(self, initial=False):
        mode = self.theme_var.get()
        self.dark = mode == "dark" or (mode == "auto" and windows_prefers_dark())
        P = self.P = PALETTES["dark" if self.dark else "light"]
        F, px, s = self.fonts, self.px, self.style
        s.theme_use("clam")
        self.configure(bg=P["bg"])
        s.configure(".", background=P["bg"], foreground=P["text"], fieldbackground=P["surface"],
                    bordercolor=P["border"], lightcolor=P["border"], darkcolor=P["border"],
                    troughcolor=P["bg"], selectbackground=P["sel"], selectforeground=P["sel_fg"],
                    insertcolor=P["text"], focuscolor=P["accent"], font=F["base"])
        s.map(".", foreground=[("disabled", P["faint"])])
        for name, bg in (("TFrame", "bg"), ("Plain.TFrame", "bg"), ("Surface.TFrame", "surface"), ("Side.TFrame", "side"), ("Header.TFrame", "header"),
                         ("Status.TFrame", "surface"), ("Info.TFrame", "accent_soft"), ("Warn.TFrame", "warn_bg")):
            s.configure(name, background=P[bg], borderwidth=0)
        s.configure("Card.TFrame", background=P["surface"], borderwidth=1, relief="solid",
                    bordercolor=P["border"], lightcolor=P["border"], darkcolor=P["border"])
        labels = {
            "TLabel": ("bg", "text", "base"), "Muted.TLabel": ("bg", "muted", "base"),
            "Title.TLabel": ("bg", "text", "title"), "Title2.TLabel": ("bg", "text", "h2"),
            "Sub.TLabel": ("bg", "muted", "base"), "Field.TLabel": ("bg", "muted", "small_bold"),
            "Link.TLabel": ("bg", "accent", "base"), "Error.TLabel": ("bg", "err", "base"),
            "Ok.TLabel": ("bg", "ok", "small_bold"), "Off.TLabel": ("bg", "off", "small_bold"),
            "WarnText.TLabel": ("bg", "warn", "small_bold"), "Small.TLabel": ("bg", "muted", "small"),
            "Chip.TLabel": ("side", "muted", "small"), "ChipAccent.TLabel": ("accent_soft", "accent", "small_bold"),
            "ChipWarn.TLabel": ("warn_bg", "warn", "small_bold"),
            "Card.TLabel": ("surface", "text", "base"), "CardHead.TLabel": ("surface", "muted", "small_bold"),
            "CardField.TLabel": ("surface", "muted", "small_bold"), "CardMuted.TLabel": ("surface", "muted", "small"),
            "CardWarn.TLabel": ("surface", "warn", "small"),
            "SideHead.TLabel": ("side", "muted", "small_bold"),
            "Header.TLabel": ("header", "header_fg", "base"), "Brand.TLabel": ("header", "header_fg", "brand"),
            "HeaderMuted.TLabel": ("header", "header_muted", "small"),
            "HeaderChip.TLabel": ("header_btn", "header_muted", "small_bold"),
            "Info.TLabel": ("accent_soft", "text", "base"), "InfoHead.TLabel": ("accent_soft", "accent", "small_bold"),
            "InfoMuted.TLabel": ("accent_soft", "muted", "small"),
            "Warn.TLabel": ("warn_bg", "warn", "base"),
            "Status.TLabel": ("surface", "text", "small"), "StatusOk.TLabel": ("surface", "ok", "small"),
            "StatusErr.TLabel": ("surface", "err", "small"), "StatusMuted.TLabel": ("surface", "muted", "small"),
        }
        for name, (bg, fg, font) in labels.items():
            s.configure(name, background=P[bg], foreground=P[fg], font=F[font])
        for name in ("Chip.TLabel", "ChipAccent.TLabel", "ChipWarn.TLabel", "HeaderChip.TLabel"):
            s.configure(name, padding=(px(8), px(2)))
        s.configure("Warn.TLabel", padding=(px(12), px(8)))

        # Boutons
        s.configure("TButton", background=P["surface"], foreground=P["text"], bordercolor=P["border"],
                    lightcolor=P["surface"], darkcolor=P["surface"], focuscolor=P["surface"],
                    padding=(px(12), px(5)), relief="flat", font=F["base"])
        s.map("TButton", background=[("disabled", P["bg"]), ("pressed", P["side_hover"]), ("active", P["side"])],
              lightcolor=[("pressed", P["side_hover"]), ("active", P["side"])],
              darkcolor=[("pressed", P["side_hover"]), ("active", P["side"])],
              foreground=[("disabled", P["faint"])],
              bordercolor=[("disabled", P["border"]), ("focus", P["accent"]), ("active", P["accent"])])
        s.configure("Accent.TButton", background=P["accent"], foreground=P["accent_fg"], bordercolor=P["accent"],
                    lightcolor=P["accent"], darkcolor=P["accent"], focuscolor=P["accent"], font=F["bold"])
        s.map("Accent.TButton",
              background=[("disabled", P["off"]), ("pressed", P["accent_hover"]), ("active", P["accent_hover"])],
              lightcolor=[("disabled", P["off"]), ("pressed", P["accent_hover"]), ("active", P["accent_hover"])],
              darkcolor=[("disabled", P["off"]), ("pressed", P["accent_hover"]), ("active", P["accent_hover"])],
              bordercolor=[("disabled", P["off"]), ("active", P["accent_hover"])],
              foreground=[("disabled", P["surface"])])
        s.configure("Danger.TButton", foreground=P["danger"])
        s.map("Danger.TButton", foreground=[("disabled", P["faint"])],
              bordercolor=[("disabled", P["border"]), ("focus", P["danger"]), ("active", P["danger"])])
        s.configure("Header.TButton", background=P["header_btn"], foreground=P["header_fg"],
                    bordercolor=P["header_btn"], lightcolor=P["header_btn"], darkcolor=P["header_btn"],
                    focuscolor=P["header_btn"], padding=(px(12), px(4)))
        s.map("Header.TButton", background=[("pressed", P["accent"]), ("active", P["accent_hover"])],
              lightcolor=[("pressed", P["accent"]), ("active", P["accent_hover"])],
              darkcolor=[("pressed", P["accent"]), ("active", P["accent_hover"])],
              bordercolor=[("active", P["accent_hover"])])
        s.configure("Seg.Toolbutton", background=P["surface"], foreground=P["muted"], bordercolor=P["border"],
                    lightcolor=P["surface"], darkcolor=P["surface"], focuscolor=P["surface"],
                    padding=(px(14), px(6)), relief="solid", font=F["base"])
        s.map("Seg.Toolbutton", background=[("selected", P["accent_soft"]), ("active", P["side"])],
              foreground=[("selected", P["accent"])], bordercolor=[("selected", P["accent"])],
              lightcolor=[("selected", P["accent_soft"]), ("active", P["side"])],
              darkcolor=[("selected", P["accent_soft"]), ("active", P["side"])])

        # Champs de saisie
        s.configure("TEntry", fieldbackground=P["surface"], foreground=P["text"], bordercolor=P["border"],
                    lightcolor=P["surface"], darkcolor=P["surface"], insertcolor=P["text"], padding=(px(8), px(5)))
        s.map("TEntry", bordercolor=[("focus", P["accent"])], lightcolor=[("focus", P["accent"])])
        s.configure("Search.TEntry", fieldbackground=P["header_btn"], foreground=P["header_fg"],
                    bordercolor=P["header_btn"], lightcolor=P["header_btn"], darkcolor=P["header_btn"],
                    insertcolor=P["header_fg"], padding=(px(10), px(5)))
        s.map("Search.TEntry", bordercolor=[("focus", P["accent_hover"])], lightcolor=[("focus", P["header_btn"])])
        s.configure("Ph.Search.TEntry", foreground=P["header_muted"])

        # Cases à cocher et boutons radio
        for name, bg in (("TCheckbutton", "bg"), ("Side.TCheckbutton", "side"), ("Card.TCheckbutton", "surface"),
                         ("TRadiobutton", "bg")):
            s.configure(name, background=P[bg], foreground=P["text"], indicatorbackground=P["surface"],
                        indicatorforeground=P["accent_fg"], upperbordercolor=P["faint"],
                        lowerbordercolor=P["faint"], focuscolor=P[bg], indicatormargin=(0, 0, px(6), 0))
            s.map(name, background=[("active", P[bg])],
                  indicatorbackground=[("selected", P["accent"]), ("pressed", P["accent_soft"])],
                  upperbordercolor=[("selected", P["accent"])], lowerbordercolor=[("selected", P["accent"])])
        s.configure("Side.TCheckbutton", foreground=P["muted"], font=F["small"])

        # Listes
        s.configure("Treeview", background=P["surface"], fieldbackground=P["surface"], foreground=P["text"],
                    bordercolor=P["surface"], lightcolor=P["surface"], darkcolor=P["surface"],
                    rowheight=px(30), font=F["base"])
        s.map("Treeview", background=[("selected", P["sel"])], foreground=[("selected", P["sel_fg"])])
        s.configure("Treeview.Heading", background=P["surface"], foreground=P["muted"], font=F["small_bold"],
                    relief="flat", bordercolor=P["border"], lightcolor=P["surface"], darkcolor=P["border"],
                    padding=(px(8), px(7)))
        s.map("Treeview.Heading", background=[("active", P["side"])], lightcolor=[("active", P["side"])])
        s.configure("Side.Treeview", background=P["side"], fieldbackground=P["side"], foreground=P["text"],
                    bordercolor=P["side"], lightcolor=P["side"], darkcolor=P["side"], rowheight=px(32),
                    indent=px(2), font=F["side"])
        s.map("Side.Treeview", background=[("selected", P["accent_soft"])], foreground=[("selected", P["accent"])])
        if initial:
            s.layout("Side.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
            s.layout("Side.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.text", {"sticky": "nswe"})]})])
        s.configure("Side.Treeview.Item", padding=(px(8), 0, 0, 0))

        # Barres de défilement, onglets, séparateurs
        for o in ("Vertical", "Horizontal"):
            s.configure(f"{o}.TScrollbar", background=P["side"], troughcolor=P["surface"], bordercolor=P["surface"],
                        lightcolor=P["side"], darkcolor=P["side"], arrowcolor=P["muted"], gripcount=0,
                        arrowsize=px(13))
            s.map(f"{o}.TScrollbar", background=[("pressed", P["accent_soft"]), ("active", P["side_hover"])],
                  lightcolor=[("active", P["side_hover"])], darkcolor=[("active", P["side_hover"])])
        s.configure("TNotebook", background=P["bg"], bordercolor=P["border"], lightcolor=P["bg"],
                    darkcolor=P["bg"], tabmargins=(0, px(4), 0, 0))
        s.configure("TNotebook.Tab", background=P["bg"], foreground=P["muted"], bordercolor=P["border"],
                    lightcolor=P["bg"], darkcolor=P["bg"], padding=(px(16), px(6)), font=F["base"])
        s.map("TNotebook.Tab", background=[("selected", P["surface"])], foreground=[("selected", P["accent"])],
              lightcolor=[("selected", P["surface"])], expand=[("selected", (0, 0, 0, 0))])
        s.configure("TPanedwindow", background=P["bg"])
        s.configure("Sash", sashthickness=px(8), gripcount=0, background=P["bg"], lightcolor=P["bg"],
                    darkcolor=P["bg"], bordercolor=P["bg"])
        s.configure("TSeparator", background=P["border"])

        # Pastilles d'état
        d = px(16)
        self.icons.update({
            "on": make_dot(P["ok"], d), "off": make_dot(P["off"], d, hollow=True),
            "warn": make_dot(P["warn"], d), "err": make_dot(P["err"], d),
            "idle": make_dot(P["faint"], d, hollow=True), "missing": make_dot(P["faint"], d, hollow=True),
            "overview": make_dot(P["accent"], d), "logo": make_logo(px(30), P["accent"]),
            "logo_big": make_logo(px(56), P["accent"]),
        })
        if not initial:
            for w, role in list(self.tk_widgets):
                try:
                    self._style_tk(w, role)
                except tk.TclError:
                    self.tk_widgets.remove((w, role))
            self._config_detail_tags()
            self.logo_lbl.configure(image=self.icons["logo"])
            set_titlebar_theme(self, self.dark)

    def _style_tk(self, w, role: str):
        P, px = self.P, self.px
        if role == "list":
            w.configure(bg=P["surface"], fg=P["text"], selectbackground=P["sel"], selectforeground=P["sel_fg"],
                        highlightthickness=1, highlightbackground=P["border"], highlightcolor=P["accent"],
                        relief="flat", borderwidth=0, activestyle="none", font=self.fonts["base"])
            return
        detail = role == "detail"
        w.configure(bg=P["surface"] if detail else P["code_bg"], fg=P["text"], insertbackground=P["text"],
                    selectbackground=P["sel"], selectforeground=P["sel_fg"], relief="flat", borderwidth=0,
                    highlightthickness=0 if detail else 1, highlightbackground=P["border"],
                    highlightcolor=P["accent"], padx=px(16 if detail else 10), pady=px(12 if detail else 8),
                    font=self.fonts["base" if detail else "mono"])

    def make_text(self, parent, code=True, **kw):
        box = ttk.Frame(parent, style="Plain.TFrame")
        t = tk.Text(box, **kw)
        ys = ttk.Scrollbar(box, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=ys.set)
        t.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        if kw.get("wrap") == "none":
            xs = ttk.Scrollbar(box, orient="horizontal", command=t.xview)
            t.configure(xscrollcommand=xs.set)
            xs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        role = "code" if code else "detail"
        self._style_tk(t, role)
        self.tk_widgets.append((t, role))
        return t, box

    def make_list(self, parent, **kw):
        lb = tk.Listbox(parent, exportselection=False, **kw)
        self._style_tk(lb, "list")
        self.tk_widgets.append((lb, "list"))
        return lb

    def set_theme(self):
        self.settings.d["theme"] = self.theme_var.get()
        self.settings.save()
        self.apply_theme()
        self.refresh()

    def cycle_theme(self):
        self.theme_var.set("light" if self.dark else "dark")
        self.set_theme()

    # ============================================================ construction
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Coller du JSON dans l'agent…", accelerator="Ctrl+V", command=self.paste_json)
        f.add_command(label="Exporter les serveurs de l'agent…", accelerator="Ctrl+E", command=self.export_json)
        f.add_separator()
        f.add_command(label="Où sont les fichiers de chaque agent ?", command=self.show_locations)
        f.add_command(label="Ouvrir le dossier des sauvegardes", command=lambda: open_path(self._mk(BACKUP_DIR)))
        f.add_separator()
        f.add_command(label="Quitter", command=self.on_close)
        m.add_cascade(label="Fichier", menu=f)
        a = tk.Menu(m, tearoff=0)
        a.add_command(label="Ajouter un serveur…", accelerator="Ctrl+N", command=self.new_server)
        a.add_separator()
        a.add_command(label="Ouvrir le fichier de configuration", command=self.open_file)
        a.add_command(label="Ouvrir le dossier", command=self.open_folder)
        a.add_separator()
        a.add_command(label="Utiliser un autre fichier…", command=self.change_path)
        a.add_command(label="Revenir à l'emplacement par défaut", command=self.reset_path)
        a.add_separator()
        a.add_command(label="Redémarrer l'application…", command=self.restart_agent)
        a.add_command(label="Restaurer une sauvegarde…", command=self.restore_backup)
        m.add_cascade(label="Agent", menu=a)
        v = tk.Menu(m, tearoff=0)
        for val, txt in (("auto", "Thème automatique (comme Windows)"), ("light", "Thème clair"),
                         ("dark", "Thème sombre")):
            v.add_radiobutton(label=txt, value=val, variable=self.theme_var, command=self.set_theme)
        v.add_separator()
        v.add_checkbutton(label="Afficher les agents non détectés", variable=self.show_all_var,
                          command=self.toggle_show_all)
        v.add_command(label="Relire les fichiers", accelerator="F5", command=self.refresh)
        m.add_cascade(label="Affichage", menu=v)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Claude Code ou Claude Desktop : quel fichier ?", command=self.explain_claude)
        h.add_command(label="Raccourcis clavier", command=self.show_shortcuts)
        h.add_separator()
        h.add_command(label=f"À propos de {APP_NAME}", command=self.show_about)
        m.add_cascade(label="Aide", menu=h)
        self.config(menu=m)

    @staticmethod
    def _mk(p: Path) -> Path:
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _build_ui(self):
        px = self.px
        # ---------------------------------------------------------- bandeau
        hdr = ttk.Frame(self, style="Header.TFrame", padding=(px(16), px(10)))
        hdr.pack(side="top", fill="x")
        self.logo_lbl = ttk.Label(hdr, image=self.icons["logo"], style="Header.TLabel")
        self.logo_lbl.pack(side="left")
        ttk.Label(hdr, text=APP_NAME, style="Brand.TLabel").pack(side="left", padx=(px(10), 0))
        ttk.Label(hdr, text=f"v{APP_VERSION.rsplit('.', 1)[0]}", style="HeaderChip.TLabel").pack(side="left",
                                                                                               padx=px(8))
        ttk.Label(hdr, text="Les serveurs MCP de tous vos agents de codage, au même endroit",
                  style="HeaderMuted.TLabel").pack(side="left", padx=px(6))
        b = ttk.Button(hdr, text="⟳  Relire", style="Header.TButton", command=self.refresh)
        b.pack(side="right")
        self.tip.attach(b, "Relire tous les fichiers de configuration (F5)")
        b = ttk.Button(hdr, text="◐", width=3, style="Header.TButton", command=self.cycle_theme)
        b.pack(side="right", padx=(0, px(6)))
        self.tip.attach(b, "Passer du thème clair au thème sombre (menu Affichage pour le mode automatique)")
        self.search = ttk.Entry(hdr, textvariable=self.search_var, width=30, style="Search.TEntry")
        self.search.pack(side="right", padx=(0, px(10)))
        self.search.bind("<FocusIn>", lambda e: self._search_focus(True))
        self.search.bind("<FocusOut>", lambda e: self._search_focus(False))
        self.search.bind("<Escape>", lambda e: self.clear_search())
        self.search.bind("<Down>", lambda e: (self.tree.focus_set(), "break")[1])
        self.search_var.trace_add("write", lambda *_: None if self._ph else self._show(self._kept()))
        self._search_focus(False)

        # ---------------------------------------------------------- barre d'état
        sb = ttk.Frame(self, style="Status.TFrame", padding=(px(14), px(5)))
        sb.pack(side="bottom", fill="x")
        self.status = ttk.Label(sb, style="Status.TLabel")
        self.status.pack(side="left")
        ttk.Label(sb, text=f"Réglages et sauvegardes : {DATA_DIR}", style="StatusMuted.TLabel").pack(side="right")
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        # ---------------------------------------------------------- barre latérale
        side = ttk.Frame(body, style="Side.TFrame", padding=(px(8), px(14), px(4), px(10)))
        body.add(side, weight=0)
        ttk.Label(side, text="AGENTS DE CODAGE", style="SideHead.TLabel").pack(anchor="w", padx=px(10),
                                                                              pady=(0, px(6)))
        self.agent_tree = ttk.Treeview(side, style="Side.Treeview", show="tree", columns=("n",),
                                       selectmode="browse")
        self.agent_tree.column("#0", width=px(268), minwidth=px(180), stretch=True)
        self.agent_tree.column("n", width=px(38), anchor="e", stretch=False)
        self.agent_tree.pack(fill="both", expand=True)
        self.agent_tree.bind("<<TreeviewSelect>>", self.on_agent_select)
        self.agent_tree.bind("<Motion>", self._side_motion)
        self.agent_tree.bind("<Leave>", self.tip.hide)
        ttk.Checkbutton(side, text="Afficher les agents non détectés", variable=self.show_all_var,
                        command=self.toggle_show_all, style="Side.TCheckbutton").pack(anchor="w", padx=px(10),
                                                                                       pady=(px(8), 0))

        # ---------------------------------------------------------- zone principale
        main = ttk.Frame(body, padding=(px(22), px(16), px(22), px(10)))
        body.add(main, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        head = ttk.Frame(main)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        top = ttk.Frame(head)
        top.grid(row=0, column=0, sticky="ew")
        self.title_lbl = ttk.Label(top, style="Title.TLabel")
        self.title_lbl.pack(side="left")
        self.chips = ttk.Frame(top)
        self.chips.pack(side="left", padx=(px(14), 0))
        self.head_btns = ttk.Frame(top)
        self.head_btns.pack(side="right")
        ttk.Button(self.head_btns, text="Ouvrir le fichier", command=self.open_file).pack(side="left")
        ttk.Button(self.head_btns, text="Dossier", command=self.open_folder).pack(side="left", padx=(px(6), 0))
        self.restart_btn = ttk.Button(self.head_btns, text="↻  Redémarrer…", command=self.restart_agent)
        self.restart_btn.pack(side="left", padx=(px(6), 0))
        self.tip.attach(self.restart_btn, lambda: self._restart_tip())
        self.sub_lbl = ttk.Label(head, style="Sub.TLabel", justify="left")
        self.sub_lbl.grid(row=1, column=0, sticky="w", pady=(px(2), 0))
        self.used_card = ttk.Frame(head, style="Info.TFrame", padding=(px(14), px(10)))
        self.used_card.grid(row=2, column=0, sticky="ew", pady=(px(12), 0))
        ttk.Label(self.used_card, text="CE FICHIER EST LU PAR", style="InfoHead.TLabel").pack(anchor="w")
        self.used_lbl = ttk.Label(self.used_card, style="Info.TLabel", justify="left")
        self.used_lbl.pack(anchor="w", pady=(px(4), 0))
        self.hint_lbl = ttk.Label(self.used_card, style="InfoMuted.TLabel", justify="left")
        self.hint_lbl.pack(anchor="w", pady=(px(6), 0))
        self.path_lbl = ttk.Label(head, style="Link.TLabel", cursor="hand2")
        self.path_lbl.grid(row=3, column=0, sticky="w", pady=(px(10), 0))
        self.path_lbl.bind("<Button-1>", lambda e: self.open_folder())
        self.tip.attach(self.path_lbl, "Cliquer pour ouvrir le dossier")
        self.warn_lbl = ttk.Label(head, style="Warn.TLabel", justify="left")
        self.warn_lbl.grid(row=4, column=0, sticky="ew", pady=(px(10), 0))

        # Barre d'outils (vue d'un agent) / légende (vue d'ensemble)
        self.toolbar = tb = ttk.Frame(main)
        tb.grid(row=1, column=0, sticky="ew", pady=(px(14), 0))
        self.btn = {}
        spec = [("add", "+  Ajouter", "Accent.TButton", self.new_server, "Ajouter un serveur à cet agent (Ctrl+N)"),
                ("paste", "⇩  Coller du JSON", "TButton", self.paste_json,
                 "Importer une configuration copiée depuis une documentation, quel que soit son format (Ctrl+V)"),
                None,
                ("edit", "✎  Modifier", "TButton", self.edit_server, "Modifier le serveur sélectionné (Entrée)"),
                ("toggle", "⏯  Activer / Désactiver", "TButton", self.toggle_selected, "Espace"),
                ("copy", "⧉  Copier vers…", "TButton", self.copy_to,
                 "Copier vers d'autres agents, avec conversion du format (Ctrl+D)"),
                ("test", "▶  Tester", "TButton", self.test_server, "Lancer le serveur et lister ses outils (Ctrl+T)"),
                ("delete", "✕  Supprimer", "Danger.TButton", self.delete_servers, "Supprimer (Suppr)")]
        for item in spec:
            if item is None:
                ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=px(10), pady=px(4))
                continue
            key, txt, style, cmd, tip = item
            b = ttk.Button(tb, text=txt, style=style, command=cmd)
            b.pack(side="left", padx=(0, px(6)))
            self.tip.attach(b, tip)
            self.btn[key] = b
        self.legend = lg = ttk.Frame(main)
        lg.grid(row=1, column=0, sticky="ew", pady=(px(14), 0))
        for txt, style in (("✔ actif", "Ok.TLabel"), ("○ désactivé", "Off.TLabel"), ("⚠ incomplet", "WarnText.TLabel")):
            ttk.Label(lg, text=txt, style=style).pack(side="left", padx=(0, px(14)))
        ttk.Label(lg, text="Case vide : absent.   Double-clic sur une case : activer / désactiver, ou copier le "
                           "serveur dans cet agent.   Clic droit : plus d'actions.",
                  style="Small.TLabel").pack(side="left")

        # Liste des serveurs + détails
        content = ttk.PanedWindow(main, orient="horizontal")
        content.grid(row=2, column=0, sticky="nsew", pady=(px(10), 0))
        tf = ttk.Frame(content, style="Card.TFrame", padding=1)
        self.tree = ttk.Treeview(tf, show="headings", selectmode="extended")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_tree_select())
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<Motion>", self._tree_motion)
        self.tree.bind("<Leave>", self.tip.hide)

        df = ttk.Frame(content, style="Card.TFrame", padding=1)
        dh = ttk.Frame(df, style="Status.TFrame", padding=(px(16), px(10), px(10), 0))
        dh.pack(fill="x")
        ttk.Label(dh, text="DÉTAILS", style="CardHead.TLabel").pack(side="left")
        ttk.Checkbutton(dh, text="Afficher les secrets", variable=self.reveal_var, command=self.update_details,
                        style="Card.TCheckbutton").pack(side="right")
        self.detail = tk.Text(df, wrap="word", state="disabled", cursor="arrow", width=40, height=10)
        self.detail.pack(fill="both", expand=True)
        self._style_tk(self.detail, "detail")
        self.tk_widgets.append((self.detail, "detail"))
        self._config_detail_tags()
        content.add(tf, weight=3)
        content.add(df, weight=2)

        self.reload_lbl = ttk.Label(main, style="Small.TLabel", justify="left")
        self.reload_lbl.grid(row=3, column=0, sticky="ew", pady=(px(8), 0))

        def wrap(e):
            w = max(px(300), e.width - px(110))
            for lbl in (self.sub_lbl, self.used_lbl, self.hint_lbl, self.warn_lbl, self.reload_lbl):
                lbl.configure(wraplength=w)
        main.bind("<Configure>", wrap)

    def _config_detail_tags(self):
        P, F, px, t = self.P, self.fonts, self.px, self.detail
        t.tag_configure("h", font=F["h2"], foreground=P["text"], spacing3=px(4))
        t.tag_configure("lbl", font=F["small_bold"], foreground=P["muted"], spacing1=px(14), spacing3=px(3))
        t.tag_configure("val", font=F["base"], foreground=P["text"])
        t.tag_configure("code", font=F["mono"], foreground=P["text"], background=P["code_bg"],
                        lmargin1=px(2), lmargin2=px(2))
        t.tag_configure("muted", foreground=P["muted"], font=F["base"])
        t.tag_configure("small", foreground=P["muted"], font=F["small"])
        for k in ("ok", "off", "warn", "err", "accent"):
            t.tag_configure(k, foreground=P[k], font=F["bold"])

    def _bind_keys(self):
        def k(fn):
            return lambda e: (fn(), "break")[1]
        for seq, fn in (("<Control-n>", self.new_server), ("<Control-N>", self.new_server),
                        ("<F5>", self.refresh), ("<Control-f>", self.focus_search), ("<Control-F>", self.focus_search),
                        ("<Control-e>", self.export_json), ("<Control-E>", self.export_json)):
            self.bind(seq, k(fn))
        for seq, fn in (("<Return>", self.edit_server), ("<F2>", self.edit_server), ("<Delete>", self.delete_servers),
                        ("<space>", self.toggle_selected), ("<Control-d>", self.copy_to),
                        ("<Control-D>", self.copy_to), ("<Control-t>", self.test_server),
                        ("<Control-T>", self.test_server), ("<Control-v>", self.paste_from_clipboard),
                        ("<Control-V>", self.paste_from_clipboard), ("<Escape>", self.clear_search)):
            self.tree.bind(seq, k(fn))

    def _restore_geometry(self):
        m = re.match(r"^(\d+)x(\d+)$", str(self.settings.d.get("size") or ""))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = (int(m[1]), int(m[2])) if m else (self.px(1360), self.px(840))
        w, h = min(w, sw), min(h, sh - self.px(60))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - self.px(20))}")

    def on_close(self):
        try:
            zoomed = self.state() == "zoomed"
            self.settings.d["zoomed"] = zoomed
            if not zoomed:
                self.settings.d["size"] = f"{self.winfo_width()}x{self.winfo_height()}"
            self.settings.save()
        except (OSError, tk.TclError):
            pass
        self.destroy()

    # ============================================================ messages
    def say(self, msg: str, kind: str = ""):
        style = {"ok": "StatusOk.TLabel", "err": "StatusErr.TLabel"}.get(kind, "Status.TLabel")
        self.status.configure(text=msg, style=style)

    # ============================================================ recherche
    def _search_focus(self, inside: bool):
        if inside and self._ph:
            self._ph = False
            self.search.delete(0, "end")
            self.search.configure(style="Search.TEntry")
        elif not inside and not self.search.get():
            self._ph = True
            self.search.configure(style="Ph.Search.TEntry")
            self.search.insert(0, "⌕  Rechercher un serveur…")

    def query(self) -> str:
        return "" if self._ph else self.search_var.get().strip().lower()

    def focus_search(self):
        self.search.focus_set()
        self.search.select_range(0, "end")

    def clear_search(self):
        if self.query():
            self.search_var.set("")
        self.tree.focus_set()

    def matches(self, name: str, canons: list[dict]) -> bool:
        q = self.query()
        return not q or q in name.lower() or any(q in canon_summary(c).lower() for c in canons)

    # ============================================================ données
    def visible_agents(self) -> list[Agent]:
        show_all = self.show_all_var.get()
        return [a for a in AGENTS if show_all or self.states[a.id].detected or a is self.current]

    def _kept(self) -> list[str]:
        out = []
        for i in self.tree.selection():
            r = self.rows.get(i)
            out.append(r.name if isinstance(r, Srv) else r)
        return [n for n in out if n]

    def refresh(self, keep: list[str] | None = None):
        if keep is None:
            keep = self._kept() if self.rows else []
        self.states = {a.id: load_state(a, self.settings) for a in AGENTS}
        self._fill_agents()
        self._show(keep)

    def _fill_agents(self):
        t = self.agent_tree
        t.delete(*t.get_children())
        total = len({s.name for st in self.states.values() for s in st.servers})
        t.insert("", "end", iid=OVERVIEW, text="  Vue d'ensemble", image=self.icons["overview"],
                 values=(total,), tags=("overview",))
        visible = self.visible_agents()
        for g in GROUPS:
            ags = [a for a in visible if a.group == g]
            if not ags:
                continue
            gid = "grp:" + g
            t.insert("", "end", iid=gid, text=g.upper(), open=True, tags=("group",))
            for a in ags:
                st = self.states[a.id]
                n = len(st.servers)
                if st.err:
                    img, tag = "err", "err"
                elif not st.detected:
                    img, tag = "missing", "missing"
                else:
                    img, tag = ("on" if n else "idle"), ""
                t.insert(gid, "end", iid=a.id, text="  " + a.label, image=self.icons[img],
                         values=(n if (st.exists or n) else "",), tags=(tag,))
        P, F = self.P, self.fonts
        t.tag_configure("group", foreground=P["faint"], font=F["small_bold"])
        t.tag_configure("overview", font=F["side_bold"])
        t.tag_configure("missing", foreground=P["faint"])
        t.tag_configure("err", foreground=P["err"])
        sel = self.current.id if self.current and t.exists(self.current.id) else OVERVIEW
        t.selection_set(sel)
        t.see(sel)

    def on_agent_select(self, _e=None):
        sel = self.agent_tree.selection()
        if not sel:
            return
        if sel[0].startswith("grp:"):
            # Les rubriques ne se sélectionnent pas : on passe à la ligne voisine dans le sens du déplacement.
            t = self.agent_tree
            order = [OVERVIEW]
            for g in t.get_children()[1:]:
                order += [g, *t.get_children(g)]
            gi = order.index(sel[0])
            prev = self.current.id if self.current else OVERVIEW
            pi = order.index(prev) if prev in order else 0
            target = order[gi + 1] if pi < gi and gi + 1 < len(order) else order[gi - 1]
            t.selection_set(target)
            t.focus(target)
            return
        new = None if sel[0] == OVERVIEW else AGENT_BY_ID[sel[0]]
        if new is self.current and self.rows:
            return
        self.current = new
        self._show()

    def toggle_show_all(self):
        self.settings.d["show_all"] = self.show_all_var.get()
        self.settings.save()
        self._fill_agents()
        if self.current is None:
            self._show(self._kept())

    def _set_columns(self, cols: list[tuple], show: str):
        self.tree.delete(*self.tree.get_children())
        self.tree.configure(columns=[c[0] for c in cols], displaycolumns=[c[0] for c in cols], show=show)
        self.tree.column("#0", width=self.px(40), minwidth=self.px(40), stretch=False, anchor="center")
        self.tree.heading("#0", text="")
        for cid, title, width, stretch, anchor in cols:
            self.tree.heading(cid, text=title, anchor=anchor)
            self.tree.column(cid, width=self.px(width), minwidth=self.px(50), stretch=stretch, anchor=anchor)
        P = self.P
        self.tree.tag_configure("alt", background=P["row_alt"])
        self.tree.tag_configure("off", foreground=P["muted"])
        self.tree.tag_configure("warn", foreground=P["err"])

    def _set_chips(self, chips: list[tuple[str, str]]):
        for w in self.chips.winfo_children():
            w.destroy()
        for txt, style in chips:
            ttk.Label(self.chips, text=txt, style=style).pack(side="left", padx=(0, self.px(6)))

    def _show(self, keep: list[str] | None = None):
        self.rows = {}
        self.tip.hide()
        if self.current is None:
            self._show_overview(keep or [])
        else:
            self._show_agent(keep or [])
        self.on_tree_select()

    def _show_overview(self, keep: list[str]):
        agents = self.visible_agents()
        self.title_lbl.configure(text="Vue d'ensemble")
        self.sub_lbl.configure(text="Tous vos serveurs MCP, agent par agent. Survolez le nom d'une colonne pour "
                                    "savoir quel logiciel lit ce fichier.")
        detected = sum(1 for a in AGENTS if self.states[a.id].detected)
        self._set_chips([(f"{len(agents)} agents affichés", "ChipAccent.TLabel"),
                         (f"{detected} détectés sur ce PC", "Chip.TLabel")])
        self.used_card.grid_remove()
        self.path_lbl.grid_remove()
        self.head_btns.pack_forget()
        self.toolbar.grid_remove()
        self.legend.grid()
        errs = [f"{a.label} : {self.states[a.id].err}" for a in agents if self.states[a.id].err]
        self._set_warn(errs)
        cols = [("nom", "SERVEUR MCP", 200, True, "w")]
        cols += [(a.id, a.short.upper(), 96, False, "center") for a in agents]
        self._set_columns(cols, "headings")
        names: list[str] = []
        for a in agents:
            for s in self.states[a.id].servers:
                if s.name not in names:
                    names.append(s.name)
        shown = [n for n in sorted(names, key=str.lower)
                 if self.matches(n, [s.canon for a in agents if (s := self.states[a.id].find(n))])]
        for i, n in enumerate(shown):
            vals = [n]
            for a in agents:
                s = self.states[a.id].find(n)
                vals.append("" if not s else "⚠" if canon_invalid(s.canon) else "✔" if s.canon["enabled"] else "○")
            iid = f"r{i}"
            self.tree.insert("", "end", iid=iid, values=vals, tags=("alt",) if i % 2 else ())
            self.rows[iid] = n
            if n in keep:
                self.tree.selection_add(iid)
        self.reload_lbl.configure(text="Après une modification, relancez l'agent concerné pour qu'il relise son "
                                       "fichier (sélectionnez-le à gauche, puis « Redémarrer… »).")
        q = f" correspondant à « {self.query()} »" if self.query() else ""
        self.say(f"{len(shown)} serveur(s) MCP{q} sur {len(agents)} agent(s).")

    def _show_agent(self, keep: list[str]):
        a = self.current
        st = self.states[a.id]
        p = a.path()
        self.title_lbl.configure(text=a.name)
        self.sub_lbl.configure(text=a.subtitle)
        n = len(st.servers)
        chips = [(f"{n} serveur{'s' if n > 1 else ''}", "ChipAccent.TLabel"), (a.fmt.upper(), "Chip.TLabel"),
                 ("Désactivation native" if a.disable != "park" else "Désactivation : mise de côté", "Chip.TLabel")]
        if not st.detected:
            chips.append(("Non détecté sur ce PC", "ChipWarn.TLabel"))
        if a.override:
            chips.append(("Emplacement personnalisé", "ChipWarn.TLabel"))
        self._set_chips(chips)
        self.used_lbl.configure(text="\n".join(f"•  {u}" for u in a.used_by) or "—")
        self.hint_lbl.configure(text="  ".join(x for x in (a.hint, a.note, a.disable_note) if x))
        self.used_card.grid()
        self.path_lbl.configure(text=f"Fichier : {p}" + ("" if st.exists else "   — sera créé au premier ajout"))
        self.path_lbl.grid()
        self.head_btns.pack(side="right")
        if a.id in RESTART_TARGETS and IS_WIN:
            self.restart_btn.pack(side="left", padx=(self.px(6), 0))
        else:
            self.restart_btn.pack_forget()
        warns = ([st.err + " — corrigez-le (bouton « Ouvrir le fichier ») puis relisez (F5)."] if st.err else [])
        warns += st.warns
        if st.lossy:
            warns.append("Ce fichier contient des commentaires : ils seront supprimés à la prochaine modification.")
        self._set_warn(warns)
        self.legend.grid_remove()
        self.toolbar.grid()
        self._set_columns([("nom", "NOM", 220, False, "w"), ("etat", "ÉTAT", 190, False, "w"),
                           ("type", "TYPE", 120, False, "w"), ("cible", "COMMANDE / URL", 420, True, "w")],
                          "tree headings")
        shown = [s for s in st.servers if self.matches(s.name, [s.canon])]
        for i, s in enumerate(shown):
            c = s.canon
            if canon_invalid(c):
                img, etat, tag = "warn", "Incomplet / mal formé", "warn"
            elif c["enabled"]:
                img, etat, tag = "on", "Actif", ""
            else:
                img, etat, tag = "off", "Désactivé (gardé à part)" if s.parked else "Désactivé", "off"
            iid = f"s{i}"
            tags = tuple(x for x in (tag, "alt" if i % 2 else "") if x)
            self.tree.insert("", "end", iid=iid, image=self.icons[img],
                             values=(s.name, etat, TYPE_LABELS[c["type"]], canon_summary(c)), tags=tags)
            self.rows[iid] = s
            if s.name in keep:
                self.tree.selection_add(iid)
        self.reload_lbl.configure(text=f"↻  Après une modification : {a.reload}")
        q = f" ({len(shown)} affiché(s) pour « {self.query()} »)" if self.query() else ""
        self.say(f"{a.label} : {n} serveur(s){q}.")

    def _set_warn(self, warns: list[str]):
        if warns:
            self.warn_lbl.configure(text="\n".join("⚠  " + w for w in warns))
            self.warn_lbl.grid()
        else:
            self.warn_lbl.grid_remove()

    def on_tree_select(self):
        self.update_actions()
        self.update_details()

    def update_actions(self):
        n = len(self.selected_servers()) if self.current else 0
        for key, ok in (("edit", n == 1), ("test", n == 1), ("toggle", n >= 1), ("copy", n >= 1),
                        ("delete", n >= 1)):
            self.btn[key].state(["!disabled"] if ok else ["disabled"])

    # ============================================================ détails
    def _secret(self, key: str, val: str) -> str:
        if self.reveal_var.get():
            return val
        return MASK if SECRET_KEY.search(key) and val else mask_text(val)

    def update_details(self):
        t = self.detail
        t.configure(state="normal")
        t.delete("1.0", "end")
        w = lambda txt, *tags: t.insert("end", txt, tags)  # noqa: E731
        if self.current is None:
            names = [self.rows[i] for i in self.tree.selection() if isinstance(self.rows.get(i), str)]
            if names:
                self._details_overview(w, names[0])
            else:
                w("Vue d'ensemble\n", "h")
                w("Sélectionnez une ligne pour voir dans quels agents ce serveur est déclaré, et sous quelle forme.\n\n",
                  "muted")
                w("Une colonne = un fichier de configuration. Survolez son titre pour savoir quel logiciel le lit.",
                  "small")
        else:
            a, sel = self.current, self.selected_servers()
            if len(sel) == 1:
                self._details_server(w, a, sel[0])
            elif sel:
                w(f"{len(sel)} serveurs sélectionnés\n", "h")
                w("\n".join(f"•  {s.name}" for s in sel) + "\n\n", "val")
                w("Vous pouvez les activer / désactiver, les copier vers d'autres agents ou les supprimer ensemble.",
                  "muted")
            elif not self.states[a.id].servers:
                w("Aucun serveur MCP\n", "h")
                w(f"{a.label} n'a encore aucun serveur.\n\n", "muted")
                w("•  « + Ajouter » pour saisir un serveur avec un formulaire ;\n"
                  "•  « Coller du JSON » pour importer la configuration d'une documentation ;\n"
                  "•  ou, depuis un autre agent, « Copier vers… ».", "val")
            else:
                w(a.label + "\n", "h")
                w("Sélectionnez un serveur pour voir son détail.\n\n", "muted")
                w("Astuces : double-clic pour modifier, Espace pour activer / désactiver, clic droit pour plus "
                  "d'actions.", "small")
        t.configure(state="disabled")

    def _details_server(self, w, a: Agent, s: Srv):
        c = s.canon
        w(s.name + "\n", "h")
        if canon_invalid(c):
            w("●  Incomplet ou mal formé", "warn")
        elif c["enabled"]:
            w("●  Actif", "ok")
        else:
            w("●  Désactivé" + (" — gardé à part par le gestionnaire" if s.parked else ""), "off")
        w(f"   ·   {TYPE_LABELS[c['type']]}\n", "muted")

        def section(title, lines):
            w(title.upper() + "\n", "lbl")
            for ln in lines:
                w(f" {ln} \n", "code")
        if c["type"] == "stdio":
            section("Commande", [c["command"] or "—"])
            if c["args"]:
                section("Arguments", [mask_text(x) if not self.reveal_var.get() else x for x in c["args"]])
            if c["env"]:
                section("Variables d'environnement", [f"{k} = {self._secret(k, v)}" for k, v in c["env"].items()])
            if c["cwd"]:
                section("Dossier de travail", [c["cwd"]])
        else:
            section("URL", [self._secret("", c["url"])])
            if c["headers"]:
                section("En-têtes", [f"{k}: {self._secret(k, v)}" for k, v in c["headers"].items()])
        others = [(b, x) for b in AGENTS if b is not a and (x := self.states[b.id].find(s.name))]
        w("PRÉSENT AUSSI DANS\n", "lbl")
        if others:
            for b, x in others:
                sym, tag = (("✔", "ok") if x.canon["enabled"] else ("○", "off"))
                w(f"{sym}  ", tag)
                w(b.label + "\n", "val")
        else:
            w("Aucun autre agent. « Copier vers… » pour l'y ajouter.\n", "muted")
        native = s.native if self.reveal_var.get() else mask_native(s.native)
        section(f"Tel qu'écrit dans le fichier ({a.short})",
                json.dumps(native, indent=2, ensure_ascii=False).splitlines())

    def _details_overview(self, w, name: str):
        w(name + "\n", "h")
        w("DÉCLARÉ DANS\n", "lbl")
        absent = []
        for a in self.visible_agents():
            s = self.states[a.id].find(name)
            if not s:
                absent.append(a.label)
                continue
            if canon_invalid(s.canon):
                w("⚠  ", "warn")
            elif s.canon["enabled"]:
                w("✔  ", "ok")
            else:
                w("○  ", "off")
            w(a.label + "\n", "val")
            w(f"     {mask_text(canon_summary(s.canon)) if not self.reveal_var.get() else canon_summary(s.canon)}\n",
              "small")
        if absent:
            w("ABSENT DE\n", "lbl")
            w(", ".join(absent) + "\n\n", "muted")
            w("Double-clic sur une case vide pour y copier ce serveur (format converti automatiquement).", "small")

    # ============================================================ infobulles
    def _agent_tip(self, a: Agent) -> str:
        users = "\n".join(f"•  {u}" for u in a.used_by)
        return f"{a.label}\n\nCe fichier est lu par :\n{users}\n\n{a.path()}"

    def _side_motion(self, e):
        row = self.agent_tree.identify_row(e.y)
        a = AGENT_BY_ID.get(row)
        self.tip.show_later(("side", row), self._agent_tip(a) if a else "", e.x_root, e.y_root)

    def _tree_motion(self, e):
        if self.current is None and self.tree.identify_region(e.x, e.y) == "heading":
            col = self.tree.identify_column(e.x)
            cols = self.tree["columns"]
            i = int(col[1:]) - 1 if col.startswith("#") and col[1:].isdigit() else -1
            a = AGENT_BY_ID.get(cols[i]) if 0 <= i < len(cols) else None
            self.tip.show_later(("head", col), self._agent_tip(a) if a else "", e.x_root, e.y_root)
        else:
            self.tip.hide()

    def _restart_tip(self) -> str:
        a = self.current
        if not a or a.id not in RESTART_TARGETS:
            return ""
        return f"Fermer puis relancer {RESTART_TARGETS[a.id][0]} pour qu'il relise ce fichier."

    # ============================================================ sélection
    def selected_servers(self) -> list[Srv]:
        return [self.rows[i] for i in self.tree.selection() if isinstance(self.rows.get(i), Srv)]

    def need_agent(self) -> Agent | None:
        if self.current is None:
            messagebox.showinfo(APP_NAME, "Choisissez d'abord un agent dans la liste de gauche.")
        return self.current

    def need_selection(self, single=False) -> list[Srv]:
        if not self.need_agent():
            return []
        s = self.selected_servers()
        if not s:
            messagebox.showinfo(APP_NAME, "Sélectionnez un serveur dans la liste.")
        elif single and len(s) > 1:
            messagebox.showinfo(APP_NAME, "Sélectionnez un seul serveur.")
            return []
        return s

    def goto(self, a: Agent, name: str | None = None):
        """Affiche l'agent a (et sélectionne le serveur name)."""
        self.current = a
        if not self.agent_tree.exists(a.id):
            self._fill_agents()
        self.agent_tree.selection_set(a.id)
        self.agent_tree.see(a.id)
        self._show([name] if name else [])
        sel = self.tree.selection()
        if sel:
            self.tree.see(sel[0])
        self.tree.focus_set()

    # ============================================================ écriture
    def write(self, a: Agent, fn) -> bool:
        st = self.states[a.id]
        if st.err and a.path().exists():
            messagebox.showerror(APP_NAME, f"{a.path()}\n\n{st.err}\n\nCorrigez le fichier avant de le modifier ici.")
            return False
        if st.lossy and a.id not in self.lossy_ok:
            if not messagebox.askyesno(APP_NAME, f"{a.path()} contient des commentaires (JSONC).\n"
                                                 "Ils seront supprimés à l'enregistrement (une sauvegarde est faite).\n\n"
                                                 "Continuer ?"):
                return False
            self.lossy_ok.add(a.id)
        try:
            modify_file(a, fn)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Impossible d'écrire {a.path()} :\n\n{e}")
            return False
        return True

    def store_many(self, a: Agent, ops: list[dict]) -> bool:
        """ops : {name, native, enabled, old (nom actuel ou None), old_parked}."""
        def fn(s):
            for op in ops:
                old, name = op.get("old"), op["name"]
                if a.disable == "park" and not op["enabled"]:
                    if old is not None and not op.get("old_parked"):
                        s.pop(old, None)
                    s.pop(name, None)
                elif old is not None and not op.get("old_parked") and old in s:
                    replace_key(s, old, name, op["native"])
                else:
                    s[name] = op["native"]

        if not self.write(a, fn):
            return False
        parked = self.settings.parked(a.id)
        for op in ops:
            if op.get("old_parked"):
                parked.pop(op["old"], None)
            if a.disable == "park" and not op["enabled"]:
                parked[op["name"]] = op["native"]
            else:
                parked.pop(op["name"], None)
        if not parked:
            self.settings.d["parked"].pop(a.id, None)
        self.settings.save()
        return True

    def add_canons(self, t: Agent, items: list[tuple[str, dict]], keep_extra: bool) -> list[str] | None:
        """Ajoute des serveurs (format canon) à l'agent t. Retourne les notes de conversion, ou None si annulé."""
        st = self.states[t.id]
        conflicts = [n for n, _ in items if st.find(n)]
        if conflicts:
            r = messagebox.askyesnocancel(
                APP_NAME, f"{t.label} contient déjà : {', '.join(conflicts)}.\n\n"
                          "Oui = remplacer   ·   Non = garder l'existant   ·   Annuler = ne rien faire")
            if r is None:
                return None
            if not r:
                items = [(n, c) for n, c in items if n not in conflicts]
        ops, notes = [], []
        for n, c in items:
            ex = st.find(n)
            ops.append({"name": n, "native": t.from_canon(c, keep_extra), "enabled": c["enabled"],
                        "old": ex.name if ex else None, "old_parked": ex.parked if ex else False})
            notes += t.conversion_notes(n, c)
        if ops and not self.store_many(t, ops):
            return None
        return notes

    def done(self, a: Agent, what: str, keep: list[str] | None = None):
        self.refresh(keep)
        self.say(f"✔  {a.label} : {what}. {a.reload}", "ok")

    # ============================================================ actions
    def new_server(self):
        a = self.need_agent()
        if not a:
            return
        dlg = ServerDialog(self, a)
        self.wait_window(dlg)
        if not dlg.result:
            return
        name, native, en = dlg.result
        ex = self.states[a.id].find(name)
        if ex and not messagebox.askyesno(APP_NAME, f"« {name} » existe déjà dans {a.label}. Le remplacer ?"):
            return
        if self.store_many(a, [{"name": name, "native": native, "enabled": en,
                                "old": ex.name if ex else None, "old_parked": ex.parked if ex else False}]):
            self.done(a, f"« {name} » ajouté", keep=[name])

    def edit_server(self):
        if self.current is None:
            sel = self.tree.selection()
            if sel and isinstance(self.rows.get(sel[0]), str):
                name = self.rows[sel[0]]
                a = next((b for b in self.visible_agents() if self.states[b.id].find(name)), None)
                if a:
                    self.goto(a, name)
            return
        sel = self.need_selection(single=True)
        if not sel:
            return
        a, s = self.current, sel[0]
        dlg = ServerDialog(self, a, s.name, s.native)
        self.wait_window(dlg)
        if not dlg.result:
            return
        name, native, en = dlg.result
        if name != s.name and self.states[a.id].find(name):
            if not messagebox.askyesno(APP_NAME, f"« {name} » existe déjà dans {a.label}. Le remplacer ?"):
                return
        if self.store_many(a, [{"name": name, "native": native, "enabled": en,
                                "old": s.name, "old_parked": s.parked}]):
            self.done(a, f"« {name} » enregistré", keep=[name])

    def toggle(self, a: Agent, srvs: list[Srv]) -> bool:
        ops = []
        for s in srvs:
            en = not s.canon["enabled"]
            nat = s.native if a.disable == "park" else a.set_enabled(s.native, en)
            ops.append({"name": s.name, "native": nat, "enabled": en, "old": s.name, "old_parked": s.parked})
        return self.store_many(a, ops)

    def toggle_selected(self):
        if self.current is None:
            return
        sel = self.need_selection()
        if sel and self.toggle(self.current, sel):
            self.done(self.current, "activation modifiée", keep=[s.name for s in sel])

    def delete_servers(self):
        if self.current is None:
            return
        sel = self.need_selection()
        if not sel:
            return
        a = self.current
        if not messagebox.askyesno(APP_NAME, f"Supprimer de {a.label} :\n\n" + "\n".join(f"  • {s.name}" for s in sel)
                                   + "\n\n(une sauvegarde du fichier est faite avant)", icon="warning"):
            return
        in_file = [s.name for s in sel if not s.parked]
        if in_file and not self.write(a, lambda d: [d.pop(n, None) for n in in_file]):
            return
        parked = self.settings.parked(a.id)
        for s in sel:
            if s.parked:
                parked.pop(s.name, None)
        if not parked:
            self.settings.d["parked"].pop(a.id, None)
        self.settings.save()
        self.done(a, f"{len(sel)} serveur(s) supprimé(s)", keep=[])

    def copy_to(self):
        if self.current is None:
            return
        sel = self.need_selection()
        if not sel:
            return
        a = self.current
        targets = [t for t in AGENTS if t is not a and (self.show_all_var.get() or self.states[t.id].detected)]
        dlg = CopyDialog(self, [s.name for s in sel], targets, self.states)
        self.wait_window(dlg)
        if not dlg.result:
            return
        done, notes = [], []
        for t in dlg.result:
            r = self.add_canons(t, [(s.name, copy.deepcopy(s.canon)) for s in sel], keep_extra=False)
            if r is not None:
                done.append(t)
                notes += r
        self.refresh()
        if done:
            msg = "Copié vers : " + ", ".join(t.label for t in done)
            if notes:
                msg += "\n\nConversions :\n" + "\n".join("• " + n for n in notes)
            msg += "\n\nPensez à relancer : " + ", ".join(t.short for t in done) + "."
            messagebox.showinfo(APP_NAME, msg)
            self.say("✔  " + msg.splitlines()[0], "ok")

    def overview_cell(self, name: str, t: Agent):
        """Double-clic / clic droit sur une case de la vue d'ensemble."""
        s = self.states[t.id].find(name)
        if s:
            if self.toggle(t, [s]):
                self.done(t, f"« {name} » {'désactivé' if s.canon['enabled'] else 'activé'}", keep=[name])
            return
        srcs = [(a, self.states[a.id].find(name)) for a in AGENTS if self.states[a.id].find(name)]
        srcs.sort(key=lambda x: (not x[1].canon["enabled"], canon_invalid(x[1].canon)))
        src_a, src = srcs[0]
        if not messagebox.askyesno(APP_NAME, f"Copier « {name} » de {src_a.label} vers {t.label} ?\n\n"
                                             "(format converti automatiquement)"):
            return
        c = copy.deepcopy(src.canon)
        c["enabled"] = True
        notes = self.add_canons(t, [(name, c)], keep_extra=False)
        if notes is not None:
            self.done(t, f"« {name} » copié depuis {src_a.short}" + (" — " + " ".join(notes) if notes else ""),
                      keep=[name])

    def _overview_agent_at(self, x: int) -> Agent | None:
        col = self.tree.identify_column(x)
        if not col or col in ("#0", "#1"):
            return None
        cols = self.tree["columns"]
        i = int(col[1:]) - 1
        return AGENT_BY_ID.get(cols[i]) if 0 <= i < len(cols) else None

    def on_double(self, event):
        if self.tree.identify_region(event.x, event.y) == "heading":
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if self.current is not None:
            self.edit_server()
            return "break"
        t = self._overview_agent_at(event.x)
        if t:
            self.overview_cell(self.rows[row], t)
        else:
            self.edit_server()
        return "break"

    def on_right_click(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus(row)
        m = tk.Menu(self, tearoff=0)
        if self.current is not None:
            one = "normal" if len(self.selected_servers()) == 1 else "disabled"
            m.add_command(label="Modifier…", accelerator="Entrée", command=self.edit_server, state=one)
            m.add_command(label="Activer / Désactiver", accelerator="Espace", command=self.toggle_selected)
            m.add_command(label="Copier vers d'autres agents…", accelerator="Ctrl+D", command=self.copy_to)
            m.add_command(label="Tester", accelerator="Ctrl+T", command=self.test_server, state=one)
            m.add_separator()
            m.add_command(label="Supprimer", accelerator="Suppr", command=self.delete_servers)
        else:
            name = self.rows[row]
            t = self._overview_agent_at(e.x)
            if t:
                s = self.states[t.id].find(name)
                if s:
                    verb = "Désactiver" if s.canon["enabled"] else "Activer"
                    m.add_command(label=f"{verb} dans {t.label}", command=lambda: self.overview_cell(name, t))
                else:
                    m.add_command(label=f"Copier dans {t.label}…", command=lambda: self.overview_cell(name, t))
                m.add_separator()
            for a in self.visible_agents():
                if self.states[a.id].find(name):
                    m.add_command(label=f"Ouvrir dans {a.label}", command=lambda a=a: self.goto(a, name))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def run_test(self, name: str, c: dict, parent=None):
        dlg = TextDialog(self, f"Test de « {name} »", "", editable=False, width=840, height=560, parent=parent)
        fn = test_stdio if c["type"] == "stdio" else test_http
        threading.Thread(target=fn, args=(c, dlg.append), daemon=True).start()

    def test_server(self):
        sel = self.need_selection(single=True)
        if sel:
            self.run_test(sel[0].name, sel[0].canon)

    def paste_from_clipboard(self):
        if self.current is None:
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            clip = ""
        self.paste_json(clip if clip.strip().startswith("{") else "")

    def paste_json(self, prefill: str = ""):
        a = self.need_agent()
        if not a:
            return

        def ok(txt):
            try:
                servers = parse_snippet(parse_json_text(txt.strip())[0])
            except ValueError as e:
                messagebox.showerror(APP_NAME, str(e), parent=dlg)
                return False
            items = []
            for n, nat in servers.items():
                if not n:
                    n = simpledialog.askstring(APP_NAME, "Nom de ce serveur :", parent=dlg)
                    if not n:
                        return False
                c = GENERIC.to_canon(nat)
                if "enabled" not in nat and "disabled" not in nat:
                    c["enabled"] = True
                items.append((n.strip(), c))
            notes = self.add_canons(a, items, keep_extra=not a.strict)
            if notes is None:
                return False
            self.after(10, lambda: self.done(a, f"{len(items)} serveur(s) importé(s)"
                                             + (" — " + " ".join(notes) if notes else ""),
                                             keep=[n for n, _ in items]))
            return True

        dlg = TextDialog(self, f"Coller du JSON dans {a.label}", prefill, ok_label="Ajouter", on_ok=ok,
                         height=520,
                         hint="Collez la configuration copiée d'une documentation ou d'un autre agent, par ex. :\n"
                              '  {"mcpServers": {"nom": {"command": "npx", "args": ["-y", "paquet"]}}}\n'
                              "Tous les formats sont acceptés (Claude, OpenCode, VS Code, Cursor…) : "
                              f"ils sont convertis au format de {a.label}.")

    def export_json(self):
        a = self.need_agent()
        if not a:
            return
        st = self.states[a.id]
        p = filedialog.asksaveasfilename(title="Exporter", defaultextension=".json",
                                         initialfile=f"mcp_{a.id}.json", filetypes=[("JSON", "*.json")])
        if not p:
            return
        data = {a.root[-1]: {s.name: s.native for s in st.servers if not s.parked}}
        write_text_atomic(Path(p), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        self.say(f"✔  Exporté : {p}", "ok")

    # ============================================================ fichiers
    def open_file(self):
        a = self.need_agent()
        if not a:
            return
        p = a.path()
        if p.exists():
            open_path(p)
        else:
            messagebox.showinfo(APP_NAME, f"Le fichier n'existe pas encore :\n{p}\n\nIl sera créé au premier ajout.")

    def open_folder(self):
        a = self.need_agent()
        if not a:
            return
        d = a.path().parent
        if d.exists():
            open_path(d)
        else:
            messagebox.showinfo(APP_NAME, f"Le dossier n'existe pas encore :\n{d}")

    def change_path(self):
        a = self.need_agent()
        if not a:
            return
        cur = a.path()
        p = filedialog.asksaveasfilename(title=f"Fichier de configuration de {a.label}",
                                         initialdir=str(cur.parent) if cur.parent.exists() else str(HOME),
                                         initialfile=cur.name, confirmoverwrite=False,
                                         filetypes=[("Configuration", "*.json *.jsonc *.toml"), ("Tous", "*.*")])
        if not p:
            return
        a.override = os.path.normpath(p)
        self.settings.d["paths"][a.id] = a.override
        self.settings.save()
        self.refresh()

    def reset_path(self):
        a = self.need_agent()
        if not a:
            return
        a.override = None
        self.settings.d["paths"].pop(a.id, None)
        self.settings.save()
        self.refresh()

    # ============================================================ tâches de fond
    def run_bg(self, fn, on_done):
        """Exécute fn() dans un thread ; on_done(résultat, exception) est appelé dans le thread de l'interface."""
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                q.put((fn(), None))
            except Exception as e:  # remonté à l'interface
                q.put((None, e))
        threading.Thread(target=worker, daemon=True).start()
        self._jobs.append((q, on_done))

    def _poll_jobs(self):
        for job in list(self._jobs):
            q, cb = job
            try:
                res, err = q.get_nowait()
            except queue.Empty:
                continue
            self._jobs.remove(job)
            cb(res, err)
        self.after(150, self._poll_jobs)

    def restart_agent(self):
        a = self.need_agent()
        if not a:
            return
        target = RESTART_TARGETS.get(a.id)
        if not target:
            messagebox.showinfo(APP_NAME, f"Le redémarrage automatique de {a.label} n'est pas disponible "
                                          f"(application en ligne de commande).\n\n{a.reload}")
            return
        if not IS_WIN:
            messagebox.showinfo(APP_NAME, "Le redémarrage automatique est actuellement disponible sous Windows seulement.")
            return
        if a.id == "claude_code":
            msg = ("Claude Code relit ce fichier au démarrage de chaque session :\n\n"
                   "•  Terminal : quittez puis relancez « claude » (ou tapez /mcp).\n"
                   "•  VS Code : rechargez la fenêtre (Ctrl+Maj+P › « Developer: Reload Window »).\n"
                   "•  Onglet « Code » de Claude Desktop : il faut redémarrer Claude Desktop.\n\n"
                   "Redémarrer Claude Desktop maintenant ? Toutes ses fenêtres seront fermées.")
        else:
            msg = (f"Fermer puis relancer {target[0]} ?\n\n"
                   "Toutes ses fenêtres seront fermées. Enregistrez votre travail avant de continuer.")
        if not messagebox.askyesno(APP_NAME, msg):
            return
        self.say(f"Redémarrage de {target[0]} en cours…")
        self.configure(cursor="watch")

        def finished(_res, err):
            self.configure(cursor="")
            if err:
                self.say(f"✖  {err}", "err")
                messagebox.showerror(APP_NAME, str(err))
            else:
                self.say(f"✔  {target[0]} a été redémarré.", "ok")
        self.run_bg(lambda: restart_windows_app(target), finished)

    def restore_backup(self):
        a = self.need_agent()
        if not a:
            return
        d = BACKUP_DIR / a.id
        files = sorted(d.glob("*"), reverse=True) if d.is_dir() else []
        if not files:
            messagebox.showinfo(APP_NAME, f"Aucune sauvegarde pour {a.label}.")
            return
        BackupDialog(self, a, files)

    # ============================================================ aide
    def show_locations(self):
        lines = ["Fichier de configuration MCP de chaque agent (portée utilisateur)", ""]
        for g in GROUPS:
            ags = [a for a in AGENTS if a.group == g]
            if not ags:
                continue
            lines += [g.upper(), ""]
            for a in ags:
                st = self.states[a.id]
                etat = ("fichier présent" if st.exists else "agent détecté, pas encore de fichier" if st.detected
                        else "non détecté")
                lines.append(f"  {a.label}  —  {etat}")
                lines.append(f"      {a.path()}")
                lines.append(f"      clé : {'.'.join(a.root)}   ·   format : {a.fmt.upper()}")
                for u in a.used_by:
                    lines.append(f"      lu par : {u}")
                lines.append("")
        lines.append("Les configurations propres à un projet (.mcp.json, opencode.json du dossier, "
                     ".vscode/mcp.json…) ne sont pas gérées ici.")
        TextDialog(self, "Où sont les fichiers ?", "\n".join(lines), editable=False, width=900, height=640)

    def explain_claude(self):
        cc, cd = AGENT_BY_ID["claude_code"], AGENT_BY_ID["claude_desktop"]
        InfoDialog(self, "Claude Code ou Claude Desktop ?", [
            ("h", "Claude Code ou Claude Desktop : quel fichier ?"),
            ("p", "Anthropic propose plusieurs façons d'utiliser Claude. Pour les serveurs MCP, il n'y a que "
                  "deux fichiers à connaître."),
            ("field", cc.label.upper()),
            ("p", "Lu par :\n" + "\n".join(f"•  {u}" for u in cc.used_by)
             + "\nUn serveur ajouté ici est donc disponible dans ces trois façons d'utiliser Claude Code."),
            ("small", f"Fichier : {cc.path()}"),
            ("field", cd.label.upper()),
            ("p", "Lu uniquement par l'onglet « Chat » de l'application Claude Desktop (les conversations). "
                  "Il ne lance que des commandes : un serveur distant (URL) y passe par « npx -y mcp-remote »."),
            ("small", f"Fichier : {cd.path()}"),
            ("muted", "Pour qu'un serveur soit disponible partout : ajoutez-le dans « Claude Code », puis "
                      "« Copier vers… » › « Claude Desktop · Chat »."),
            ("muted", "Non gérés ici : les serveurs propres à un projet (.mcp.json), les plugins et les "
                      "connecteurs claude.ai."),
        ], width=700)

    def show_shortcuts(self):
        InfoDialog(self, "Raccourcis clavier", [("h", "Raccourcis clavier"), ("table", SHORTCUTS),
                                                ("muted", "Clic droit sur un serveur pour afficher toutes les actions.")],
                   width=520)

    def show_about(self):
        InfoDialog(self, f"À propos de {APP_NAME}", [
            ("logo", None), ("h", f"{APP_NAME} {APP_VERSION}"),
            ("p", "Gère en un seul endroit les serveurs MCP de 15 agents de codage : Claude Code, Claude Desktop, "
                  "OpenCode, Codex, Gemini CLI, Qwen Code, VS Code (Copilot), Copilot CLI, Cursor, Windsurf, "
                  "Cline, Roo Code, Kilo Code, Kiro et LM Studio."),
            ("muted", f"Réglages et sauvegardes : {DATA_DIR}"),
            ("muted", f"© 2026 {APP_AUTHOR} · Distribué sous licence MIT."), ("link", APP_URL),
        ], width=560)


def main():
    enable_dpi_awareness()
    App().mainloop()


if __name__ == "__main__":
    main()
