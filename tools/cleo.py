#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""cleo — dependency manager for the Claude ecosystem.

Manages packages containing Claude Code artifacts (rules/skills/agents/commands/hooks)
and MCP server configurations. Each package is a git repo tagged with semver.

Subcommands:
  install  [--dry-run] [--offline]
  require  <vendor/pkg> [--constraint <c>] [--local|--user] [--repo <url>] [--dry-run]
  update   [<vendor/pkg> ...] [--dry-run] [--offline] [--force]
  list     [--json] [--verbose]
  check    Validate cleo.json + report drift
  init     Scaffold a starter cleo.json

Manifest: cleo.json (committed)
Lock:     cleo.lock (committed)
Cache:    ~/.claude/cleo/packages/<vendor>/<name>/<version>/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Force UTF-8 on stdout/stderr so non-ASCII status chars (→, …, —) don't crash
# the cp1252 console on Windows. Must run before any print().
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.checks import discover_items, parse_frontmatter  # noqa: E402
from lib.semver import resolve_version, resolve_commit, parse_version, matches_constraint  # noqa: E402
from lib.security import (  # noqa: E402
    SecurityViolation,
    validate_dest_item_name,
    validate_git_ref,
    validate_hook_size,
    validate_item_source,
    validate_manifest_file_not_symlink,
    validate_package_has_artifacts,
    validate_package_manifest,
    validate_package_ref,
    HOOK_SIZE_MAX_BYTES,
)
from lib import publish as publish_mod  # noqa: E402
from lib.resolver import (  # noqa: E402
    DependencyCycle,
    ResolvedPackage,
    VersionConflict,
    resolve_all,
)

LOCK_VERSION = 1
MANIFEST_FILE = "cleo.json"
LOCK_FILE = "cleo.lock"

BUCKET_PROJECT = "project"
BUCKET_LOCAL = "local"
BUCKET_USER = "user"
ALL_BUCKETS = (BUCKET_PROJECT, BUCKET_LOCAL, BUCKET_USER)

LOCAL_TYPES = {"rule", "skill", "agent", "command"}
USER_TYPES = {"rule", "skill", "agent", "command"}

VALID_PKG_TYPES = ("bundle", "mcp-server", "mixed")
VALID_INSTALL_MODES = ("copy", "symlink")

DEST_BY_TYPE = {
    "rule":    (Path(".claude/rules"),    ".md"),
    "agent":   (Path(".claude/agents"),   ".md"),
    "command": (Path(".claude/commands"), ".md"),
    "skill":   (Path(".claude/skills"),   None),
    "hook":    (Path(".claude/hooks"),    ".sh"),
}

GITIGNORE_MARKER_START = "# cleo local — managed, do not edit"
GITIGNORE_MARKER_END = "# /cleo local"
GITIGNORE_LOCAL_PATHS = (
    ".claude/rules/local/",
    ".claude/skills/local/",
    ".claude/agents/local/",
    ".claude/commands/local/",
    "cleo.local.lock",
)

MCP_SERVERS_KEY = "mcpServers"
HOOKS_KEY = "hooks"


# ---- Color output -------------------------------------------------------


def _use_color() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty() or os.environ.get("CLEO_FORCE_COLOR") == "1"


_PAL = {
    "orange": "\033[38;5;208m",
    "green":  "\033[38;5;42m",
    "red":    "\033[38;5;196m",
    "cyan":   "\033[38;5;39m",
    "dim":    "\033[2m",
    "bold":   "\033[1m",
    "reset":  "\033[0m",
}
TAG = "[cleo]"


def _wrap(s: str, *styles: str) -> str:
    if not _use_color():
        return s
    prefix = "".join(_PAL[k] for k in styles if k in _PAL)
    return f"{prefix}{s}{_PAL['reset']}"


def info(msg: str) -> None:
    print(f"{_wrap(TAG, 'cyan')} {msg}")


def ok(msg: str) -> None:
    print(_wrap(f"{TAG} {msg}", "green"))


def warn(msg: str) -> None:
    print(_wrap(f"{TAG} warn: {msg}", "orange"))


def err(msg: str) -> None:
    print(_wrap(f"{TAG} error: {msg}", "red"), file=sys.stderr)


# ---- User home ----------------------------------------------------------


def _user_home() -> Path:
    override = os.environ.get("CLEO_USER_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home()


def _cache_root() -> Path:
    return _user_home() / ".claude" / "cleo" / "packages"


# ---- Hashing ------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> str:
    entries = []
    for child in sorted(root.rglob("*")):
        if child.is_file():
            rel = child.relative_to(root).as_posix()
            entries.append(f"{rel}\0{sha256_file(child)}")
    h = hashlib.sha256()
    h.update("\n".join(entries).encode("utf-8"))
    return h.hexdigest()


def sha256_artifact(path: Path) -> str:
    return sha256_tree(path) if path.is_dir() else sha256_file(path)


# ---- Manifest (cleo.json) -----------------------------------------------


def _manifest_path(project: Path) -> Path:
    return project / MANIFEST_FILE


def load_manifest(project: Path) -> dict:
    p = _manifest_path(project)
    if not p.exists():
        raise SystemExit(
            f"{TAG} No {MANIFEST_FILE} found in {project}.\n"
            f"     Run: cleo require <vendor/package> --repo <url>"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{TAG} {MANIFEST_FILE} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{TAG} {MANIFEST_FILE} must be a JSON object.")
    return data


def save_manifest(project: Path, data: dict) -> None:
    p = _manifest_path(project)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def scaffold_manifest(project: Path) -> dict:
    project.mkdir(parents=True, exist_ok=True)
    data = {
        "name": project.name,
        "repositories": [],
        "require": {},
        "require-local": {},
        "require-user": {},
    }
    save_manifest(project, data)
    return data


def _bucket_key(bucket: str) -> str:
    return {"project": "require", "local": "require-local", "user": "require-user"}[bucket]


def manifest_add_package(project: Path, name: str, constraint: str, bucket: str, repo_url: Optional[str]) -> None:
    data = load_manifest(project) if _manifest_path(project).exists() else scaffold_manifest(project)
    if repo_url:
        repos = data.setdefault("repositories", [])
        if not any(r.get("url") == repo_url for r in repos):
            repos.append({"type": "git", "url": repo_url})
    key = _bucket_key(bucket)
    data.setdefault(key, {})[name] = constraint
    save_manifest(project, data)


def _adopt_one(project: Path, d, *, dry_run: bool, quiet: bool) -> None:
    """Register one discovered skill dir into cleo.json + cleo.lock.

    Strategy:
    - If d.git_remote is set, register as a regular git-sourced package
      with constraint "*"; commit is left empty (next `cleo update` resolves).
    - Otherwise register as a `file://` source against the discovery's path
      (or its symlink target if it's a symlink).
    """
    # Synthesize a package name from the skill dir name.
    # validate_package_ref requires [a-z0-9._-]; lowercase + char-filter
    # to satisfy that gate. Falls back to a generic name if nothing survives.
    safe = d.skill_name.replace("/", "-").replace(" ", "-").lower()
    safe = "".join(c for c in safe if c.isalnum() or c in "._-")
    safe = safe.lstrip("-.")
    if not safe:
        safe = "skill"
    pkg_name = f"adopted/{safe}"

    src_path = d.symlink_target if d.is_symlink and d.symlink_target else d.path

    if d.git_remote:
        try:
            validate_git_ref(d.git_remote)
            url = d.git_remote
            constraint = "*"
            lock_pkg = LockPackage(
                name=pkg_name, pkg_type="bundle", url=url,
                version="0.0.0+adopted", commit="",
                bucket=BUCKET_USER, install_mode="symlink" if d.is_symlink else "copy",
                items=[LockItem(type="skill", name=d.skill_name, path=str(d.path), sha="")],
            )
        except SecurityViolation as exc:
            warn(f"adopt {pkg_name}: invalid git remote ({exc}); falling back to local path")
            url = f"file://{src_path}"
            constraint = "*"
            lock_pkg = LockPackage(
                name=pkg_name, pkg_type="bundle", url=url,
                version="0.0.0+local", commit="0" * 40,
                bucket=BUCKET_USER, install_mode="symlink" if d.is_symlink else "copy",
                items=[LockItem(type="skill", name=d.skill_name, path=str(d.path), sha="")],
            )
    else:
        url = f"file://{src_path}"
        constraint = "*"
        lock_pkg = LockPackage(
            name=pkg_name, pkg_type="bundle", url=url,
            version="0.0.0+local", commit="0" * 40,
            bucket=BUCKET_USER, install_mode="symlink" if d.is_symlink else "copy",
            items=[LockItem(type="skill", name=d.skill_name, path=str(d.path), sha="")],
        )

    if not quiet:
        ok(f"adopt {pkg_name} (from {url}){' (dry-run)' if dry_run else ''}")

    if dry_run:
        return

    manifest_add_package(project, pkg_name, constraint, BUCKET_USER, url)
    lock = load_lock(project)
    lock[pkg_name] = lock_pkg
    save_lock(project, lock)


GITHUB_BASE = "https://github.com"


def _github_url(name: str) -> str:
    """Convention: vendor/name → https://github.com/vendor/name (Go-style)."""
    return f"{GITHUB_BASE}/{name}"


def _resolve_url(manifest: dict, name: str, explicit_repo: Optional[str]) -> str:
    """Resolve a package URL. Priority:
    1. Explicit --repo flag
    2. Matching entry in cleo.json `repositories`
    3. GitHub convention: https://github.com/<vendor>/<name>
    """
    if explicit_repo:
        return explicit_repo
    for r in manifest.get("repositories", []):
        if isinstance(r, dict) and r.get("type") == "git" and r.get("url"):
            url = r["url"]
            if url.rstrip("/").endswith("/" + name.split("/")[-1]) or name in url:
                return url
    # GitHub convention fallback — vendor/name → https://github.com/vendor/name
    return _github_url(name)


# ---- Lock (cleo.lock) ---------------------------------------------------


@dataclass
class LockItem:
    type: str
    name: str
    path: str
    sha: str


@dataclass
class LockPackage:
    name: str
    pkg_type: str  # bundle | mcp-server | mixed
    url: str
    version: str
    commit: str
    bucket: str
    items: list[LockItem] = field(default_factory=list)
    mcp_server_key: Optional[str] = None
    install_mode: str = "copy"  # "copy" | "symlink"
    required_by: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.install_mode not in VALID_INSTALL_MODES:
            raise ValueError(
                f"install_mode must be one of {VALID_INSTALL_MODES}, got {self.install_mode!r}"
            )

    def to_dict(self) -> dict:
        d: dict = {
            "type": self.pkg_type,
            "url": self.url,
            "version": self.version,
            "commit": self.commit,
            "bucket": self.bucket,
            "items": [{"type": i.type, "name": i.name, "path": i.path, "sha": i.sha} for i in self.items],
        }
        if self.mcp_server_key:
            d["mcp_server_key"] = self.mcp_server_key
        if self.install_mode != "copy":
            d["install_mode"] = self.install_mode
        if self.required_by:
            d["required_by"] = self.required_by
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "LockPackage":
        return cls(
            name=name,
            pkg_type=d.get("type", "bundle"),
            url=d.get("url", ""),
            version=d.get("version", ""),
            commit=d.get("commit", ""),
            bucket=d.get("bucket", BUCKET_PROJECT),
            mcp_server_key=d.get("mcp_server_key"),
            install_mode=d.get("install_mode", "copy"),
            required_by=d.get("required_by", []),
            items=[
                LockItem(type=i["type"], name=i["name"], path=i["path"], sha=i.get("sha", ""))
                for i in d.get("items", [])
            ],
        )


def _lock_path(project: Path) -> Path:
    return project / LOCK_FILE


def load_lock(project: Path) -> dict[str, LockPackage]:
    p = _lock_path(project)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{TAG} {LOCK_FILE} is not valid JSON: {exc}") from exc
    if data.get("version") != LOCK_VERSION:
        raise SystemExit(f"{TAG} Lock version mismatch: expected {LOCK_VERSION}, got {data.get('version')}.")
    packages = {}
    for name, pkg_data in data.get("packages", {}).items():
        packages[name] = LockPackage.from_dict(name, pkg_data)
    return packages


def save_lock(project: Path, packages: dict[str, LockPackage]) -> None:
    p = _lock_path(project)
    payload = {
        "version": LOCK_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packages": {name: pkg.to_dict() for name, pkg in sorted(packages.items())},
    }
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ---- Git / package fetch ------------------------------------------------


def _pkg_cache_dir(name: str, version: str) -> Path:
    vendor, pkg = name.split("/", 1)
    return _cache_root() / vendor / pkg / version


def _rmtree_force(path: Path) -> None:
    """rmtree that handles Windows read-only files (common in .git/objects)."""
    def _on_rm_error(func, target, exc_info):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass
    # Python 3.12+ uses `onexc`; older uses `onerror`. Try newer first.
    try:
        shutil.rmtree(path, onexc=_on_rm_error)  # type: ignore[call-arg]
    except TypeError:
        shutil.rmtree(path, onerror=_on_rm_error)


def _cache_head_commit(cache_dir: Path) -> Optional[str]:
    """Return the HEAD commit SHA of the cached repo, or None on failure."""
    if not (cache_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(cache_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (FileNotFoundError, OSError):
        return None


def _clone_or_fetch(url: str, cache_dir: Path, tag: str, *, expected_commit: Optional[str] = None) -> bool:
    """Ensure cache_dir contains the package at the given tag. Returns True on success.

    If expected_commit is supplied and the cached HEAD differs, the cache is
    discarded and re-cloned. Guards against tag mutation and cross-test
    contamination.
    """
    try:
        validate_git_ref(url)
        validate_git_ref(tag)
    except SecurityViolation as exc:
        err(str(exc))
        return False
    git_dir = cache_dir / ".git"
    if git_dir.exists():
        if expected_commit:
            head = _cache_head_commit(cache_dir)
            if head and head == expected_commit:
                return True
            # Cache content does not match the expected commit — discard.
            _rmtree_force(cache_dir)
        else:
            return True
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", tag, "--", url, str(cache_dir)],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _clone_or_fetch_subdir(
    url: str,
    cache_dir: Path,
    ref: str,
    subpath: str,
    *,
    expected_commit: Optional[str] = None,
) -> bool:
    """Clone only `subpath` from `url` at `ref` into `cache_dir`.

    Uses `git sparse-checkout` to materialize a single directory. After clone,
    the named subpath's contents are promoted to cache_dir root (so the rest
    of cleo treats it like a normal single-skill package).

    Synthesizes a minimal `cleo.json` if absent so manifest validation passes.
    """
    try:
        validate_git_ref(url)
        validate_git_ref(ref)
    except SecurityViolation as exc:
        err(str(exc))
        return False
    # Block path traversal in subpath.
    if ".." in subpath.split("/") or subpath.startswith("/"):
        err(f"invalid subpath: {subpath!r}")
        return False

    if cache_dir.exists():
        _rmtree_force(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        r1 = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", ref, "--no-checkout",
             "--filter=blob:none", "--", url, str(cache_dir)],
            capture_output=True, text=True,
        )
        if r1.returncode != 0:
            return False
        r2 = subprocess.run(
            ["git", "-C", str(cache_dir), "sparse-checkout", "set", "--no-cone", "--", subpath],
            capture_output=True, text=True,
        )
        if r2.returncode != 0:
            return False
        r3 = subprocess.run(
            ["git", "-C", str(cache_dir), "checkout"],
            capture_output=True, text=True,
        )
        if r3.returncode != 0:
            return False
    except (FileNotFoundError, OSError):
        return False

    # Promote subpath contents to cache_dir root.
    src_dir = cache_dir / subpath
    if not src_dir.exists():
        err(f"subpath {subpath!r} not found in repo")
        return False
    for child in src_dir.iterdir():
        target = cache_dir / child.name
        if target.exists():
            continue  # collision (unlikely) — skip rather than overwrite
        shutil.move(str(child), str(target))
    # Remove the now-empty subpath tree.
    parts = Path(subpath).parts
    if parts:
        top = cache_dir / parts[0]
        if top.exists():
            shutil.rmtree(top, ignore_errors=True)

    # Synthesize cleo.json if absent.
    cleo_json = cache_dir / "cleo.json"
    if not cleo_json.exists():
        owner_repo = url.rstrip("/").removesuffix(".git").rsplit("/", 2)[-2:]
        synth_name = "/".join(owner_repo) if len(owner_repo) == 2 else "subdir/pkg"
        cleo_json.write_text(
            json.dumps({
                "name": synth_name,
                "type": "bundle",
                "description": f"Subdir install: {subpath}",
            }, indent=2) + "\n",
            encoding="utf-8",
        )
    return True


def _read_package_manifest(cache_dir: Path) -> dict | None:
    """Read the package's own cleo.json. Returns None if absent.

    Raises SecurityViolation on malformed JSON or symlinked manifest —
    silent fallback is unsafe because a corrupted manifest could mask a
    tampered package, and a symlinked manifest could redirect to host
    files outside the cache.
    """
    p = cache_dir / "cleo.json"
    validate_manifest_file_not_symlink(p)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SecurityViolation(
            f"package cleo.json is not valid JSON: {exc}"
        ) from exc
    return data


# ---- Settings.json helpers ----------------------------------------------


def _settings_path(project: Path, bucket: str) -> Path:
    if bucket == BUCKET_USER:
        return _user_home() / ".claude" / "settings.json"
    return project / ".claude" / "settings.json"


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _expand_env_vars(value: str) -> str:
    """Replace ${VAR} placeholders with env values where available."""
    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return re.sub(r"\$\{([^}]+)\}", _replace, value)


def _collect_missing_vars(obj, seen: Optional[set] = None) -> list[str]:
    if seen is None:
        seen = set()
    missing = []
    if isinstance(obj, str):
        for m in re.finditer(r"\$\{([^}]+)\}", obj):
            var = m.group(1)
            if var not in os.environ and var not in seen:
                seen.add(var)
                missing.append(var)
    elif isinstance(obj, dict):
        for v in obj.values():
            missing.extend(_collect_missing_vars(v, seen))
    elif isinstance(obj, list):
        for item in obj:
            missing.extend(_collect_missing_vars(item, seen))
    return missing


def _expand_mcp_config(config: dict, provided: dict[str, str]) -> dict:
    """Deep-copy config, expanding ${VAR} with env + provided values."""
    env = {**os.environ, **provided}

    def _sub(v):
        if isinstance(v, str):
            return re.sub(r"\$\{([^}]+)\}", lambda m: env.get(m.group(1), m.group(0)), v)
        if isinstance(v, dict):
            return {k: _sub(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_sub(i) for i in v]
        return v

    return _sub(config)


def install_mcp_server(
    project: Path, bucket: str, pkg_name: str, cache_dir: Path,
    *, dry_run: bool = False, quiet: bool = False
) -> Optional[str]:
    """Install MCP server from mcp.json into settings.json. Returns server key or None."""
    mcp_path = cache_dir / "mcp.json"
    try:
        validate_manifest_file_not_symlink(mcp_path)
    except SecurityViolation as exc:
        err(f"{pkg_name}: {exc}")
        return None
    if not mcp_path.exists():
        return None
    try:
        mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception:
        warn(f"{pkg_name}: failed to parse mcp.json")
        return None

    server_key = "cleo-" + pkg_name.replace("/", "-")
    missing_vars = _collect_missing_vars(mcp_config)

    provided: dict[str, str] = {}
    if missing_vars and not dry_run:
        for var in missing_vars:
            val = input(f"{TAG} {pkg_name} requires env var {var} (leave blank to configure later): ").strip()
            if val:
                provided[var] = val

    expanded = _expand_mcp_config(mcp_config, provided)

    if not dry_run:
        settings_path = _settings_path(project, bucket)
        data = _load_settings(settings_path)
        data.setdefault(MCP_SERVERS_KEY, {})[server_key] = expanded
        _save_settings(settings_path, data)

    if not quiet:
        ok(f"MCP server '{server_key}' → {'settings.json (dry-run)' if dry_run else 'settings.json'}")

    return server_key


def install_hooks(
    project: Path, bucket: str, pkg_name: str, cache_dir: Path,
    *, dry_run: bool = False, quiet: bool = False
) -> list[str]:
    """Install hook scripts from hooks/ and register in settings.json. Returns hook names."""
    hooks_dir = cache_dir / "hooks"
    if not hooks_dir.exists():
        return []

    hook_scripts = list(hooks_dir.glob("*.sh"))
    if not hook_scripts:
        return []

    # Pre-flight: reject the WHOLE package if any hook is oversized OR
    # symlinks outside the cache. Done before any copy so a single bad hook
    # doesn't leave half the package installed on disk.
    for script in hook_scripts:
        try:
            validate_hook_size(script)
            validate_item_source(script, cache_dir)
        except SecurityViolation as exc:
            err(f"{pkg_name}: {exc}")
            raise

    safe_pkg = pkg_name.replace("/", "-")
    dest_hooks_dir = project / ".claude" / "hooks" / f"cleo-{safe_pkg}"
    installed_names = []

    for script in sorted(hook_scripts):
        hook_name = script.stem
        dest = dest_hooks_dir / script.name

        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(script, dest)
            dest.chmod(0o755)

        # Register in settings.json hooks config.
        # Hook script name (without .sh) is used as the event key if it matches known events,
        # otherwise it's registered under "userDefined".
        if not dry_run:
            settings_path = _settings_path(project, bucket)
            data = _load_settings(settings_path)
            hooks_cfg = data.setdefault(HOOKS_KEY, {})
            hooks_cfg[f"cleo-{safe_pkg}-{hook_name}"] = {
                "command": str(dest),
                "event": hook_name,
            }
            _save_settings(settings_path, data)

        installed_names.append(hook_name)
        if not quiet:
            ok(f"hook '{hook_name}' → {dest if not dry_run else '(dry-run)'}")

    return installed_names


# ---- Gitignore ----------------------------------------------------------


def _write_gitignore_block(project: Path) -> bool:
    """Idempotently ensure the cleo-local gitignore block matches the
    current GITIGNORE_LOCAL_PATHS. Refreshes stale blocks from older
    cleo versions so newly-gitignored paths take effect."""
    p = project / ".gitignore"
    start, end = GITIGNORE_MARKER_START, GITIGNORE_MARKER_END
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    block = "\n".join([start, *GITIGNORE_LOCAL_PATHS, end]) + "\n"
    if start in existing:
        pattern = re.escape(start) + r"[\s\S]*?" + re.escape(end) + r"\n?"
        new = re.sub(pattern, block, existing, count=1)
        if new == existing:
            return False
        p.write_text(new, encoding="utf-8")
        return True
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    p.write_text(existing + sep + block, encoding="utf-8")
    return True


# ---- Materialize artifacts ----------------------------------------------


def _dest_path(project: Path, type_: str, pkg_name: str, item_name: str, bucket: str) -> Path:
    parent, suffix = DEST_BY_TYPE[type_]
    safe_pkg = pkg_name.replace("/", "-")
    stem = f"cleo-{safe_pkg}-{item_name}"
    if bucket == BUCKET_LOCAL and type_ in LOCAL_TYPES:
        base = project / parent / "local"
    elif bucket == BUCKET_USER:
        base = _user_home() / parent
    else:
        base = project / parent
    if suffix is None:
        return base / stem
    return base / (stem + suffix)


def _source_for_item(type_: str, item_name: str, item_path: Path, cache_dir: Path) -> Path:
    if type_ == "skill":
        return item_path.parent  # the skill directory
    return item_path


def _materialize(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        tmp = dst.with_name(dst.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        os.replace(tmp, dst)
    else:
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(src, tmp)
        if dst.is_dir():
            shutil.rmtree(dst)
        os.replace(tmp, dst)


def _materialize_symlink(src: Path, dst: Path) -> None:
    """Symlink dst → src. Replaces existing dst atomically via tmp-rename.

    Raises OSError if the OS rejects symlink creation (Windows without
    developer mode / admin). On failure, dst is left in its prior state.
    Callers are responsible for fallback.
    """
    assert src.exists(), f"_materialize_symlink: src must exist: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)

    tmp = dst.with_name(dst.name + ".tmp")
    # Clean stale tmp (best-effort; if cleanup fails, the symlink call will fail loudly).
    if tmp.is_symlink() or tmp.exists():
        if tmp.is_dir() and not tmp.is_symlink():
            shutil.rmtree(tmp)
        else:
            tmp.unlink()

    # Build the symlink at tmp. If this raises OSError (Windows no-privilege),
    # dst is untouched.
    os.symlink(src.resolve(), tmp, target_is_directory=src.is_dir())

    # Atomic swap. os.replace on POSIX renames over an existing target file/symlink
    # atomically. For directory dst we have to remove first, narrowing the
    # window to just the rename call.
    if dst.is_dir() and not dst.is_symlink():
        shutil.rmtree(dst)
    os.replace(tmp, dst)


# ---- Install a single package -------------------------------------------


def install_package(
    project: Path,
    name: str,
    url: str,
    constraint: str,
    bucket: str,
    *,
    locked_version: Optional[str] = None,
    locked_commit: Optional[str] = None,
    install_mode: str = "copy",
    dry_run: bool = False,
    offline: bool = False,
    quiet: bool = False,
) -> Optional[LockPackage]:
    """Fetch and materialize one package. Returns a LockPackage or None on failure."""

    # Resolve version
    if locked_version and locked_commit:
        version = locked_version
        tag = f"v{version}" if not locked_version.startswith("v") else locked_version
        commit = locked_commit
    else:
        if offline:
            warn(f"{name}: offline mode and no cached version — skipping")
            return None
        resolved = resolve_version(url, constraint)
        if resolved is None:
            err(f"{name}: no version matching '{constraint}' found at {url}")
            return None
        version, tag = resolved
        commit = resolve_commit(url, tag) or ""

    cache_dir = _pkg_cache_dir(name, version)

    if not dry_run:
        needs_fetch = not cache_dir.exists() or not (cache_dir / "cleo.json").exists()
        if not needs_fetch and commit:
            head = _cache_head_commit(cache_dir)
            if head and head != commit:
                needs_fetch = True
                if not quiet:
                    warn(f"{name}: cached {head[:7]} != expected {commit[:7]} — re-cloning")
        if needs_fetch:
            if offline:
                warn(f"{name}: not cached (or stale) and offline mode — skipping")
                return None
            if not quiet:
                info(f"fetching {name}@{version} …")
            ok_clone = _clone_or_fetch(url, cache_dir, tag, expected_commit=commit or None)
            if not ok_clone:
                err(f"{name}: failed to clone from {url} at tag {tag}")
                return None

    if not dry_run:
        try:
            pkg_manifest = _read_package_manifest(cache_dir)
            validate_package_manifest(pkg_manifest, name)
        except SecurityViolation as exc:
            err(f"{name}: {exc}")
            return None
    else:
        pkg_manifest = None

    pkg_type = (pkg_manifest or {}).get("type", "bundle")
    # Belt-and-suspenders: validate_package_manifest already rejects unknown
    # types, but cover the dry-run / None-manifest path too.
    if pkg_type not in VALID_PKG_TYPES:
        err(f"{name}: unknown package type {pkg_type!r} (expected one of {', '.join(VALID_PKG_TYPES)})")
        return None

    # Discover artifacts up front so the empty-package gate can run before
    # anything is materialized.
    if not dry_run:
        items_found = discover_items(cache_dir)
        has_mcp_json = (cache_dir / "mcp.json").exists()
        try:
            validate_package_has_artifacts(
                items_count=len(items_found),
                has_mcp_json=has_mcp_json,
                pkg_type=pkg_type,
            )
        except SecurityViolation as exc:
            err(f"{name}: {exc}")
            return None
    else:
        items_found = []
        has_mcp_json = False

    lock_pkg = LockPackage(
        name=name, pkg_type=pkg_type, url=url,
        version=version, commit=commit, bucket=bucket,
        install_mode=install_mode,
    )

    # Materialize artifacts (rules/skills/agents/commands/hooks)
    if pkg_type in ("bundle", "mixed") and not dry_run:
        if bucket == BUCKET_USER:
            forbidden = sorted({t for t, _, _ in items_found if t not in USER_TYPES})
            if forbidden:
                err(f"{name}: user bucket does not support {', '.join(forbidden)} "
                    f"(allowed: {', '.join(sorted(USER_TYPES))}). "
                    f"Install with --local or default (project) bucket instead.")
                return None
        for type_, item_name, item_path in items_found:
            if type_ == "hook":
                continue  # handled separately
            try:
                validate_dest_item_name(item_name)
                src = _source_for_item(type_, item_name, item_path, cache_dir)
                validate_item_source(src, cache_dir)
            except SecurityViolation as exc:
                err(f"{name}: {exc}")
                return None
            dst = _dest_path(project, type_, name, item_name, bucket)
            if install_mode == "symlink":
                try:
                    _materialize_symlink(src, dst)
                except OSError as exc:
                    warn(f"{name}: symlink not permitted ({exc}); falling back to copy")
                    _materialize(src, dst)
                    lock_pkg.install_mode = "copy"
            else:
                _materialize(src, dst)
            sha = sha256_artifact(dst)
            lock_pkg.items.append(LockItem(type=type_, name=item_name, path=str(dst), sha=sha))
            if not quiet:
                ok(f"  {type_} {item_name}")

        # Hooks
        try:
            hook_names = install_hooks(project, bucket, name, cache_dir, dry_run=dry_run, quiet=quiet)
        except SecurityViolation:
            return None
        for hn in hook_names:
            lock_pkg.items.append(LockItem(type="hook", name=hn, path="", sha=""))

        if bucket == BUCKET_LOCAL:
            _write_gitignore_block(project)

    # MCP server
    if pkg_type in ("mcp-server", "mixed") and not dry_run:
        server_key = install_mcp_server(project, bucket, name, cache_dir, dry_run=dry_run, quiet=quiet)
        lock_pkg.mcp_server_key = server_key

    if not quiet:
        item_count = len([i for i in lock_pkg.items if i.type != "hook"])
        mcp_info = f" + MCP server" if lock_pkg.mcp_server_key else ""
        ok(f"{name} {version} [{pkg_type}] {item_count} item(s){mcp_info}")

    return lock_pkg


def _install_from_local_dir(
    project: Path, name: str, url: str, *,
    cache_dir: Path, bucket: str, install_mode: str = "copy", quiet: bool = False,
) -> Optional[LockPackage]:
    """Install a package whose source lives at `cache_dir` on disk (no clone).

    Mirrors `install_package`'s materialize loop but skips fetch and version
    resolution. Used by the local-path source form.
    """
    try:
        pkg_manifest = _read_package_manifest(cache_dir)
        validate_package_manifest(pkg_manifest, name)
    except SecurityViolation as exc:
        err(f"{name}: {exc}")
        return None

    pkg_type = (pkg_manifest or {}).get("type", "bundle")
    if pkg_type not in VALID_PKG_TYPES:
        err(f"{name}: unknown package type {pkg_type!r}")
        return None

    items_found = discover_items(cache_dir)
    has_mcp_json = (cache_dir / "mcp.json").exists()
    try:
        validate_package_has_artifacts(
            items_count=len(items_found),
            has_mcp_json=has_mcp_json,
            pkg_type=pkg_type,
        )
    except SecurityViolation as exc:
        err(f"{name}: {exc}")
        return None

    lock_pkg = LockPackage(
        name=name, pkg_type=pkg_type, url=url,
        version="0.0.0+local", commit="0" * 40, bucket=bucket,
        install_mode=install_mode,
    )

    if pkg_type in ("bundle", "mixed"):
        if bucket == BUCKET_USER:
            forbidden = sorted({t for t, _, _ in items_found if t not in USER_TYPES})
            if forbidden:
                err(f"{name}: user bucket does not support {', '.join(forbidden)}")
                return None
        for type_, item_name, item_path in items_found:
            if type_ == "hook":
                continue
            try:
                validate_dest_item_name(item_name)
                source_p = _source_for_item(type_, item_name, item_path, cache_dir)
                validate_item_source(source_p, cache_dir)
            except SecurityViolation as exc:
                err(f"{name}: {exc}")
                return None
            dst = _dest_path(project, type_, name, item_name, bucket)
            if install_mode == "symlink":
                try:
                    _materialize_symlink(source_p, dst)
                except OSError as exc:
                    warn(f"{name}: symlink not permitted ({exc}); falling back to copy")
                    _materialize(source_p, dst)
                    lock_pkg.install_mode = "copy"
            else:
                _materialize(source_p, dst)
            sha = sha256_artifact(dst)
            lock_pkg.items.append(LockItem(type=type_, name=item_name, path=str(dst), sha=sha))
            if not quiet:
                ok(f"  {type_} {item_name}")

        try:
            hook_names = install_hooks(project, bucket, name, cache_dir, quiet=quiet)
        except SecurityViolation:
            return None
        for hn in hook_names:
            lock_pkg.items.append(LockItem(type="hook", name=hn, path="", sha=""))
        if bucket == BUCKET_LOCAL:
            _write_gitignore_block(project)

    if pkg_type in ("mcp-server", "mixed"):
        server_key = install_mcp_server(project, bucket, name, cache_dir, quiet=quiet)
        lock_pkg.mcp_server_key = server_key

    if not quiet:
        item_count = len([i for i in lock_pkg.items if i.type != "hook"])
        ok(f"{name} [local, {pkg_type}] {item_count} item(s)")
    return lock_pkg


# ---- Subcommands --------------------------------------------------------


def cmd_install(args: argparse.Namespace) -> int:
    """Lock-strict install (mirrors `composer install`).

    When cleo.lock exists: every package is pinned to the exact version+commit
    in the lock — constraints in cleo.json are NOT re-evaluated. Files missing
    from disk are re-materialized from cache (or re-cloned if cache was cleared).

    When cleo.lock does not exist: resolve versions from cleo.json constraints,
    install, and write a fresh lock. Transitive dependencies declared in
    package manifests are resolved and installed automatically.
    """
    project = args.project.resolve()
    cli_install_mode = "symlink" if getattr(args, "symlink", False) else None
    manifest = load_manifest(project)
    lock = load_lock(project)
    lock_exists = _lock_path(project).exists()
    jobs = getattr(args, "jobs", 1)

    buckets_to_install = [
        (BUCKET_PROJECT, manifest.get("require", {})),
        (BUCKET_LOCAL,   manifest.get("require-local", {})),
        (BUCKET_USER,    manifest.get("require-user", {})),
    ]

    installed = restored = skipped = 0
    new_lock: dict[str, LockPackage] = {}

    # Collect packages that need fresh resolution (not locked).
    needs_resolve: list[tuple[str, str, str, str]] = []  # (name, constraint, url, bucket)

    for bucket, requires in buckets_to_install:
        for pkg_name, constraint in requires.items():
            try:
                pkg_name = validate_package_ref(pkg_name)
            except SecurityViolation as exc:
                err(f"manifest entry: {exc}")
                continue
            existing = lock.get(pkg_name)

            if lock_exists and existing:
                all_present = all(Path(i.path).exists() for i in existing.items if i.path)
                if all_present and not args.dry_run:
                    new_lock[pkg_name] = existing
                    skipped += 1
                    if not args.quiet:
                        info(f"skipped {pkg_name} {existing.version} (locked)")
                    continue
                result = install_package(
                    project, pkg_name, existing.url, constraint, bucket,
                    locked_version=existing.version,
                    locked_commit=existing.commit,
                    install_mode=cli_install_mode or existing.install_mode,
                    dry_run=args.dry_run,
                    offline=args.offline,
                    quiet=args.quiet,
                )
                if result is None:
                    continue
                new_lock[pkg_name] = result
                restored += 1
                continue

            try:
                url = _resolve_url(manifest, pkg_name, None)
            except SystemExit as exc:
                err(str(exc).replace(f"{TAG} ", ""))
                continue

            needs_resolve.append((pkg_name, constraint, url, bucket))

    # Resolve transitive dependencies for unlocked packages.
    if needs_resolve and not args.dry_run and not args.offline:
        try:
            resolved_pkgs = resolve_all(
                needs_resolve,
                pkg_cache_dir_fn=_pkg_cache_dir,
                clone_fn=_clone_or_fetch,
                resolve_version_fn=resolve_version,
                resolve_commit_fn=resolve_commit,
                offline=args.offline,
                jobs=jobs,
            )
        except DependencyCycle as exc:
            err(str(exc))
            return 1
        except VersionConflict as exc:
            err(str(exc))
            return 1

        # Install in topological order (deps first).
        for rpkg in resolved_pkgs:
            if rpkg.name in new_lock:
                continue  # already handled (locked/restored)
            result = install_package(
                project, rpkg.name, rpkg.url, rpkg.constraint, rpkg.bucket,
                locked_version=rpkg.version,
                locked_commit=rpkg.commit,
                install_mode=cli_install_mode or "copy",
                dry_run=args.dry_run,
                offline=args.offline,
                quiet=args.quiet,
            )
            if result is None:
                continue
            result.required_by = rpkg.required_by
            new_lock[rpkg.name] = result
            installed += 1
    else:
        # Dry-run / offline: install top-level only (no transitive resolution).
        for pkg_name, constraint, url, bucket in needs_resolve:
            result = install_package(
                project, pkg_name, url, constraint, bucket,
                install_mode=cli_install_mode or "copy",
                dry_run=args.dry_run,
                offline=args.offline,
                quiet=args.quiet,
            )
            if result is None:
                continue
            new_lock[pkg_name] = result
            installed += 1

    if not args.dry_run:
        save_lock(project, new_lock)

    if not args.quiet:
        suffix = " (dry-run)" if args.dry_run else ""
        parts = [f"installed={installed}"]
        if restored:
            parts.append(f"restored={restored}")
        parts.append(f"skipped={skipped}")
        print(f"\n{_wrap(TAG, 'cyan')} {' '.join(parts)}{suffix}")
    return 0


def cmd_require(args: argparse.Namespace) -> int:
    from lib.sources import parse_source, SourceKind

    project = args.project.resolve()
    manifest = load_manifest(project) if _manifest_path(project).exists() else None

    raw_spec = args.package
    constraint = args.constraint or "*"
    explicit_repo = args.repo

    try:
        src = parse_source(raw_spec)
    except ValueError as exc:
        err(str(exc))
        return 1

    if src.kind == SourceKind.GIT_SUBDIR:
        pkg_ref = src.name
        try:
            pkg_ref = validate_package_ref(pkg_ref)
            validate_git_ref(src.url)
        except SecurityViolation as exc:
            err(str(exc))
            return 1

        bucket = BUCKET_LOCAL if args.local else (BUCKET_USER if args.user else BUCKET_PROJECT)
        if manifest is None:
            scaffold_manifest(project)
            manifest = load_manifest(project)

        ref = src.ref or "main"
        version = "0.0.0+subdir"
        commit = resolve_commit(src.url, ref) or ""

        cache_dir = _pkg_cache_dir(pkg_ref, version)
        if not args.dry_run:
            if not _clone_or_fetch_subdir(src.url, cache_dir, ref, src.subpath,
                                           expected_commit=commit or None):
                err(f"{pkg_ref}: failed to fetch subdir from {src.url}")
                return 1

        install_mode = "symlink" if getattr(args, "symlink", False) else "copy"
        result = install_package(
            project, pkg_ref, src.url, constraint, bucket,
            locked_version=version, locked_commit=commit or "0" * 40,
            install_mode=install_mode,
            dry_run=args.dry_run, quiet=args.quiet,
        )
        if result is None:
            return 1
        manifest_add_package(project, pkg_ref, constraint, bucket, src.url)
        lock = load_lock(project)
        lock[pkg_ref] = result
        if not args.dry_run:
            save_lock(project, lock)
        if not args.quiet:
            ok(f"Added {pkg_ref} (subdir: {src.subpath})")
        return 0

    if src.kind == SourceKind.LOCAL_PATH:
        pkg_ref = src.name
        try:
            pkg_ref = validate_package_ref(pkg_ref)
        except SecurityViolation as exc:
            err(str(exc))
            return 1
        bucket = BUCKET_LOCAL if args.local else (BUCKET_USER if args.user else BUCKET_PROJECT)
        if manifest is None:
            scaffold_manifest(project)
            manifest = load_manifest(project)

        if args.dry_run:
            if not args.quiet:
                ok(f"Would add {pkg_ref} (local: {src.local_path}) (dry-run)")
            return 0

        cache_dir = src.local_path
        install_mode = "symlink" if getattr(args, "symlink", False) else "copy"
        result = _install_from_local_dir(
            project, pkg_ref, f"file://{src.local_path}",
            cache_dir=src.local_path, bucket=bucket,
            install_mode=install_mode, quiet=args.quiet,
        )
        if result is None:
            return 1
        manifest_add_package(project, pkg_ref, "*", bucket, f"file://{src.local_path}")
        lock = load_lock(project)
        lock[pkg_ref] = result
        save_lock(project, lock)
        if not args.quiet:
            ok(f"Added {pkg_ref} (local: {src.local_path})")
        return 0

    pkg_ref = src.name
    repo_url = explicit_repo or src.url

    try:
        pkg_ref = validate_package_ref(pkg_ref)
    except SecurityViolation as exc:
        err(str(exc))
        return 1

    bucket = BUCKET_LOCAL if args.local else (BUCKET_USER if args.user else BUCKET_PROJECT)

    if manifest is None:
        scaffold_manifest(project)
        info("Created cleo.json")
        manifest = load_manifest(project)

    try:
        validate_git_ref(repo_url)
    except SecurityViolation as exc:
        err(str(exc))
        return 1

    install_mode = "symlink" if getattr(args, "symlink", False) else "copy"

    if not args.quiet:
        info(f"resolving {pkg_ref} ({constraint}) from {repo_url} …")

    # Resolve transitive dependencies before installing.
    try:
        resolved_pkgs = resolve_all(
            [(pkg_ref, constraint, repo_url, bucket)],
            pkg_cache_dir_fn=_pkg_cache_dir,
            clone_fn=_clone_or_fetch,
            resolve_version_fn=resolve_version,
            resolve_commit_fn=resolve_commit,
            offline=False,
            jobs=getattr(args, "jobs", 1),
        )
    except DependencyCycle as exc:
        err(str(exc))
        return 1
    except VersionConflict as exc:
        err(str(exc))
        return 1

    lock = load_lock(project)
    dep_count = 0

    # Install in topological order (deps first, main package last).
    for rpkg in resolved_pkgs:
        result = install_package(
            project, rpkg.name, rpkg.url, rpkg.constraint, rpkg.bucket,
            locked_version=rpkg.version,
            locked_commit=rpkg.commit,
            install_mode=install_mode,
            dry_run=args.dry_run, quiet=args.quiet,
        )
        if result is None:
            if rpkg.name == pkg_ref:
                return 1
            continue
        result.required_by = rpkg.required_by
        lock[rpkg.name] = result
        if rpkg.name != pkg_ref:
            dep_count += 1

    manifest_add_package(project, pkg_ref, constraint, bucket, repo_url)

    if not args.dry_run:
        save_lock(project, lock)

    if not args.quiet:
        suffix = " (dry-run)" if args.dry_run else ""
        dep_info = f" + {dep_count} dep(s)" if dep_count else ""
        ok(f"Added {pkg_ref}@{constraint}{dep_info}{suffix}")
        info("Commit cleo.json and cleo.lock.")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    from lib.adopt import scan_untracked

    project = args.project.resolve()
    # Load manifest and lock conditionally so scan runs even when files are missing.
    if _manifest_path(project).exists():
        manifest = load_manifest(project)
    else:
        manifest = {}
    if _lock_path(project).exists():
        lock = load_lock(project)
    else:
        lock = {}

    # ---- Pre-scan: surface untracked SKILL.md dirs in scope ----
    scope = getattr(args, "scope", "both")
    scan_dirs: list[Path] = []
    if scope in ("project", "both"):
        scan_dirs.append(project / ".claude" / "skills")
    if scope in ("global", "both"):
        scan_dirs.append(_user_home() / ".claude" / "skills")

    tracked_paths: set[Path] = set()
    for pkg in lock.values():
        for item in pkg.items:
            if item.path:
                tracked_paths.add(Path(item.path))

    discoveries = []
    for sd in scan_dirs:
        discoveries.extend(scan_untracked(sd, tracked_paths))

    if discoveries:
        if not getattr(args, "adopt", False):
            names = ", ".join(d.skill_name for d in discoveries)
            info(
                f"note: {len(discoveries)} untracked skill director"
                f"{'y' if len(discoveries) == 1 else 'ies'} found ({names})"
            )
            info("      re-run with --adopt to register them.")
        else:
            from lib.adopt import enrich_provenance
            for d in discoveries:
                enriched = enrich_provenance(d)
                _adopt_one(project, enriched, dry_run=args.dry_run, quiet=args.quiet)

    # ---- Existing update loop continues here ----
    if not lock:
        info("No packages installed. Run: cleo install")
        return 0

    all_requires: dict[str, tuple[str, str]] = {}
    for bucket, key in [(BUCKET_PROJECT, "require"), (BUCKET_LOCAL, "require-local"), (BUCKET_USER, "require-user")]:
        for name, constraint in manifest.get(key, {}).items():
            try:
                normalized = validate_package_ref(name)
            except SecurityViolation as exc:
                err(f"manifest entry: {exc}")
                continue
            all_requires[normalized] = (constraint, bucket)

    target_packages = set(args.packages) if args.packages else set(all_requires)

    updated = already_current = skipped = 0
    new_lock = dict(lock)

    for pkg_name in sorted(target_packages):
        try:
            pkg_name = validate_package_ref(pkg_name)
        except SecurityViolation as exc:
            err(f"{pkg_name}: {exc}")
            skipped += 1
            continue
        if pkg_name not in all_requires:
            warn(f"{pkg_name} is not in {MANIFEST_FILE}")
            continue
        constraint, bucket = all_requires[pkg_name]
        existing = lock.get(pkg_name)

        try:
            url = _resolve_url(manifest, pkg_name, existing.url if existing else None)
        except SystemExit as exc:
            err(str(exc).replace(f"{TAG} ", ""))
            continue

        if url.startswith("file://"):
            # Local-path or adopted packages have no git version tags; skip silently.
            already_current += 1
            continue

        if not args.offline:
            resolved = resolve_version(url, constraint)
            if resolved is None:
                warn(f"{pkg_name}: no version matching '{constraint}'")
                skipped += 1
                continue
            new_version, new_tag = resolved

            if existing and existing.version == new_version:
                already_current += 1
                if not args.quiet:
                    info(f"already current {pkg_name} {new_version}")
                continue

        existing_install_mode = existing.install_mode if existing else "copy"
        result = install_package(
            project, pkg_name, url, constraint, bucket,
            install_mode=existing_install_mode,
            dry_run=args.dry_run, offline=args.offline, quiet=args.quiet,
        )
        if result is None:
            skipped += 1
            continue

        old_ver = existing.version if existing else "?"
        new_lock[pkg_name] = result
        updated += 1
        if not args.quiet:
            ok(f"{pkg_name} {old_ver} → {result.version}")

    if not args.dry_run:
        save_lock(project, new_lock)

    if not args.quiet:
        suffix = " (dry-run)" if args.dry_run else ""
        print(f"\n{_wrap(TAG, 'cyan')} updated={updated} already-current={already_current} skipped={skipped}{suffix}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    lock = load_lock(project)

    if not lock:
        info("No packages installed. Run: cleo install")
        return 0

    if args.json:
        print(json.dumps({
            "packages": {name: pkg.to_dict() for name, pkg in sorted(lock.items())}
        }, indent=2))
        return 0

    info(f"{len(lock)} package(s) installed\n")
    header = "| Package | Type | Version | Commit | Bucket | Items |"
    sep    = "| --- | --- | --- | --- | --- | --- |"
    print(header)
    print(sep)
    for name, pkg in sorted(lock.items()):
        art = len([i for i in pkg.items if i.type != "hook"])
        mcp = " + MCP" if pkg.mcp_server_key else ""
        hooks = len([i for i in pkg.items if i.type == "hook"])
        hook_s = f" + {hooks} hook(s)" if hooks else ""
        commit_short = pkg.commit[:7] if pkg.commit else "—"
        print(f"| {name} | {pkg.pkg_type} | {pkg.version} | {commit_short} | {pkg.bucket} | {art}{mcp}{hook_s} |")

    if args.verbose:
        print()
        for name, pkg in sorted(lock.items()):
            dep_info = f" (required by: {', '.join(pkg.required_by)})" if pkg.required_by else ""
            info(f"{name} {pkg.version}{dep_info}")
            for item in pkg.items:
                print(f"    {item.type:<10} {item.name:<40} {item.path}")
    return 0


def _find_in_frontmatter(root: Path, q: str) -> Optional[str]:
    """Return the relative path of the first .md file whose frontmatter
    description contains `q`, or None.
    """
    for md in root.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = parse_frontmatter(text)
        if fm and q in (fm.get("description", "") or "").lower():
            return md.relative_to(root).as_posix()
    return None


def cmd_find(args: argparse.Namespace) -> int:
    """Substring-match the query over installed package names, item names,
    and descriptions found in their SKILL.md / rule frontmatter.

    Local-only — does not query a remote index.
    """
    project = args.project.resolve()
    lock = load_lock(project)
    if not lock:
        info("No packages installed.")
        return 0

    q = args.query.lower()
    matched: list[tuple[str, str]] = []  # (package, reason)
    for pkg_name, pkg in sorted(lock.items()):
        if q in pkg_name.lower():
            matched.append((pkg_name, f"name matches '{args.query}'"))
            continue
        item_hit = False
        for item in pkg.items:
            if q in item.name.lower():
                matched.append((pkg_name, f"item {item.name} matches"))
                item_hit = True
                break
        if item_hit:
            continue
        cache = _pkg_cache_dir(pkg_name, pkg.version)
        if cache.exists():
            hit_item = _find_in_frontmatter(cache, q)
            if hit_item:
                matched.append((pkg_name, f"description matches in {hit_item}"))

    if not matched:
        info(f"No matches for {args.query!r}.")
        return 0

    info(f"{len(matched)} match(es) for {args.query!r}:")
    for name, why in matched:
        print(f"  {name} — {why}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    manifest = load_manifest(project)
    lock = load_lock(project)

    issues = 0
    all_requires = {}
    for key in ("require", "require-local", "require-user"):
        all_requires.update(manifest.get(key, {}))

    for pkg_name in all_requires:
        if pkg_name not in lock:
            warn(f"{pkg_name} is in {MANIFEST_FILE} but not installed — run: cleo install")
            issues += 1
            continue
        pkg = lock[pkg_name]
        for item in pkg.items:
            if not item.path:
                continue
            p = Path(item.path)
            if not p.exists():
                warn(f"{pkg_name}: {item.type} '{item.name}' missing from disk — run: cleo install")
                issues += 1
                continue
            if item.sha:
                current = sha256_artifact(p)
                if current != item.sha:
                    warn(f"{pkg_name}: {item.type} '{item.name}' modified on disk — run: cleo install --force")
                    issues += 1

    for pkg_name in lock:
        if pkg_name not in all_requires:
            warn(f"{pkg_name} is in lock but not in {MANIFEST_FILE}")

    if issues == 0:
        ok("all packages OK")
    return 0 if issues == 0 else 1


def cmd_remove(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    manifest = load_manifest(project)
    lock = load_lock(project)

    removed = not_found = 0
    new_lock = dict(lock)

    for pkg_name in args.packages:
        try:
            pkg_name = validate_package_ref(pkg_name)
        except SecurityViolation as exc:
            err(f"{pkg_name}: {exc}")
            not_found += 1
            continue
        pkg = lock.get(pkg_name)
        if pkg is None:
            warn(f"{pkg_name} not installed — nothing to remove")
            not_found += 1
            continue

        # Remove materialized files/dirs.
        for item in pkg.items:
            if not item.path:
                continue
            p = Path(item.path)
            if p.exists():
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                if not args.quiet:
                    ok(f"  removed {item.type} {item.name}")

        safe_pkg = pkg_name.replace("/", "-")

        # Remove hooks directory.
        hook_dir = project / ".claude" / "hooks" / f"cleo-{safe_pkg}"
        if hook_dir.exists():
            shutil.rmtree(hook_dir)

        # Remove MCP server entry from settings.json.
        if pkg.mcp_server_key:
            settings_path = _settings_path(project, pkg.bucket)
            data = _load_settings(settings_path)
            servers = data.get(MCP_SERVERS_KEY, {})
            if pkg.mcp_server_key in servers:
                del servers[pkg.mcp_server_key]
                data[MCP_SERVERS_KEY] = servers
                _save_settings(settings_path, data)
                if not args.quiet:
                    ok(f"  removed MCP server '{pkg.mcp_server_key}' from settings.json")

        # Remove hook registrations from settings.json.
        settings_path = _settings_path(project, pkg.bucket)
        data = _load_settings(settings_path)
        hooks_cfg = data.get(HOOKS_KEY, {})
        stale_keys = [k for k in hooks_cfg if k.startswith(f"cleo-{safe_pkg}-")]
        if stale_keys:
            for k in stale_keys:
                del hooks_cfg[k]
            data[HOOKS_KEY] = hooks_cfg
            _save_settings(settings_path, data)

        # Remove from manifest.
        manifest_changed = False
        for key in ("require", "require-local", "require-user"):
            if pkg_name in manifest.get(key, {}):
                del manifest[key][pkg_name]
                manifest_changed = True
        if manifest_changed:
            save_manifest(project, manifest)

        del new_lock[pkg_name]
        removed += 1
        if not args.quiet:
            ok(f"removed {pkg_name}")

    # Remove orphaned transitive deps (required_by all removed).
    orphan_pass = True
    while orphan_pass:
        orphan_pass = False
        for dep_name, dep_pkg in list(new_lock.items()):
            if not dep_pkg.required_by:
                continue
            # Check if all requirers are still in the lock.
            still_needed = any(r in new_lock for r in dep_pkg.required_by)
            # Also keep if it's a direct manifest entry.
            in_manifest = False
            for key in ("require", "require-local", "require-user"):
                if dep_name in manifest.get(key, {}):
                    in_manifest = True
                    break
            if not still_needed and not in_manifest:
                # Remove orphan's files.
                for item in dep_pkg.items:
                    if item.path:
                        p = Path(item.path)
                        if p.exists():
                            if p.is_dir() and not p.is_symlink():
                                shutil.rmtree(p)
                            else:
                                p.unlink()
                del new_lock[dep_name]
                removed += 1
                orphan_pass = True
                if not args.quiet:
                    ok(f"removed orphan {dep_name}")

    save_lock(project, new_lock)

    if not args.quiet:
        suffix = " (nothing removed)" if removed == 0 else ""
        print(f"\n{_wrap(TAG, 'cyan')} removed={removed} not-found={not_found}{suffix}")
    return 0 if not_found == 0 else 1


def cmd_init(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if _manifest_path(project).exists():
        info(f"{MANIFEST_FILE} already exists.")
        return 0
    scaffold_manifest(project)
    ok(f"Created {MANIFEST_FILE}")
    info("Next: cleo require <vendor/package> --repo <url>")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    pkg_dir = args.package.resolve()
    if not pkg_dir.is_dir():
        err(f"--package {pkg_dir} is not a directory")
        return 1

    if args.release:
        if args.bump is None:
            args.bump = "patch"
        args.commit = True
        args.tag = True
        args.push = True

    cleo_json = pkg_dir / "cleo.json"
    existing: dict | None = None
    if cleo_json.exists():
        try:
            existing = json.loads(cleo_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            err(f"existing cleo.json is not valid JSON: {exc}")
            return 1

    detected = publish_mod.detect_package(pkg_dir)
    merged = publish_mod.merge_manifest(existing, detected)
    if "name" not in merged:
        err("cannot detect package name — set `name` in cleo.json or "
            "configure an origin remote matching <vendor>/<name>")
        return 1

    if args.bump:
        current = merged.get("version", "0.0.0")
        try:
            merged["version"] = publish_mod.bump_version(current, args.bump)
        except ValueError as exc:
            err(str(exc))
            return 1
        if not args.quiet:
            info(f"bumped version {current} → {merged['version']}")

    changed = publish_mod.write_manifest(pkg_dir, merged)
    if changed and not args.quiet:
        info(f"refreshed {pkg_dir / 'cleo.json'}")

    errors = publish_mod.validate_publish(pkg_dir)
    if errors:
        for e in errors:
            err(e)
        return 1

    if args.commit:
        version_for_subject = merged.get("version", "0.0.0")
        if publish_mod.working_tree_dirty(pkg_dir, ["cleo.json"]):
            try:
                publish_mod.commit_file(
                    pkg_dir, "cleo.json", f"chore(publish): v{version_for_subject}",
                )
            except RuntimeError as exc:
                err(str(exc))
                return 1
            if not args.quiet:
                ok(f"committed cleo.json (v{version_for_subject})")
        elif not args.quiet:
            info("cleo.json already committed — nothing to do")

    if args.tag:
        version_for_tag = merged.get("version", "0.0.0")
        tag = f"v{version_for_tag}"
        try:
            if publish_mod.tag_exists(pkg_dir, tag):
                if publish_mod.tag_at_head(pkg_dir, tag):
                    if not args.quiet:
                        info(f"tag {tag} already at HEAD — skipping")
                elif args.yes:
                    publish_mod.delete_tag(pkg_dir, tag)
                    publish_mod.create_tag(pkg_dir, tag)
                    if not args.quiet:
                        ok(f"moved tag {tag} to HEAD")
                else:
                    err(f"tag {tag} already exists at a different commit "
                        f"(rerun with --yes to move it)")
                    return 1
            else:
                publish_mod.create_tag(pkg_dir, tag)
                if not args.quiet:
                    ok(f"created tag {tag}")
        except (RuntimeError, SecurityViolation) as exc:
            err(str(exc))
            return 1

    if args.push:
        version_for_tag = merged.get("version", "0.0.0")
        tag = f"v{version_for_tag}"
        branch = publish_mod.current_branch(pkg_dir)
        if not branch:
            err("HEAD is detached — cannot push a branch ref")
            return 1
        try:
            publish_mod.push(pkg_dir, args.remote, [f"HEAD:refs/heads/{branch}", tag])
        except (RuntimeError, SecurityViolation) as exc:
            err(str(exc))
            return 1
        if not args.quiet:
            ok(f"pushed {branch} + {tag} to {args.remote}")

    if not args.quiet:
        ok(f"{merged['name']} {merged.get('version', '?')} [{merged.get('type')}] — validation passed")
    return 0


# ---- CLI ----------------------------------------------------------------


def main(argv: list[str]) -> int:
    # Shared flags available both before AND after the subcommand.
    # `common_root` sets the default; `common_sub` uses SUPPRESS so the
    # subparser doesn't clobber a value the root parser already captured.
    common_root = argparse.ArgumentParser(add_help=False)
    common_root.add_argument("--project", type=Path, default=Path.cwd(),
                             help="Project root (default: cwd)")
    common_root.add_argument("--quiet", action="store_true")

    common_sub = argparse.ArgumentParser(add_help=False)
    common_sub.add_argument("--project", type=Path, default=argparse.SUPPRESS,
                            help="Project root (default: cwd)")
    common_sub.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(
        prog="cleo",
        description="Dependency manager for the Claude ecosystem.",
        parents=[common_root],
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("install", help="Install packages from cleo.json", parents=[common_sub])
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--symlink", action="store_true",
                   help="Symlink artifacts from cache (live updates) instead of copying")
    s.add_argument("--jobs", "-j", type=int, default=1,
                   help="Parallel fetch workers (default: 1, sequential)")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("require", aliases=["add"], help="Add a package to cleo.json and install it", parents=[common_sub])
    s.add_argument("package", help="<vendor/name>[@constraint]")
    s.add_argument("--constraint", "-c", default="*")
    s.add_argument("--repo", help="Git URL for the package")
    s.add_argument("--local", action="store_true")
    s.add_argument("--user", action="store_true")
    s.add_argument("--symlink", action="store_true",
                   help="Symlink artifacts from cache (live updates) instead of copying")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--jobs", "-j", type=int, default=1,
                   help="Parallel fetch workers (default: 1, sequential)")
    s.set_defaults(fn=cmd_require)

    s = sub.add_parser("remove", aliases=["rm"], help="Remove packages, clean up files, update manifest", parents=[common_sub])
    s.add_argument("packages", nargs="+", metavar="vendor/pkg")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("update", help="Update packages to latest matching version", parents=[common_sub])
    s.add_argument("packages", nargs="*", metavar="vendor/pkg")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--force", action="store_true", help="Overwrite hand-edited files")
    s.add_argument("--adopt", action="store_true",
                   help="Register untracked SKILL.md directories found in .claude/skills/")
    s.add_argument("--scope", choices=["project", "global", "both"], default="both",
                   help="Where to scan for untracked skills (default: both)")
    s.add_argument("--jobs", "-j", type=int, default=1,
                   help="Parallel fetch workers (default: 1, sequential)")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("list", aliases=["ls"], help="List installed packages", parents=[common_sub])
    s.add_argument("--json", action="store_true")
    s.add_argument("--verbose", "-v", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("find", help="Search installed packages by name or description", parents=[common_sub])
    s.add_argument("query")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("check", help="Validate cleo.json and report drift", parents=[common_sub])
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("init", help="Scaffold a starter cleo.json", parents=[common_sub])
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("publish", help="Refresh package cleo.json, validate, and optionally release",
                       parents=[common_sub])
    s.add_argument("--package", type=Path, default=Path.cwd(),
                   help="Path to the package repo (default: cwd)")
    s.add_argument("--bump", choices=["patch", "minor", "major"], default=None,
                   help="Bump version in cleo.json before validating")
    s.add_argument("--commit", action="store_true",
                   help="git add cleo.json and commit it")
    s.add_argument("--tag", action="store_true",
                   help="git tag vX.Y.Z on HEAD after committing")
    s.add_argument("--yes", action="store_true",
                   help="skip confirms; allow tag overwrite")
    s.add_argument("--push", action="store_true",
                   help="git push HEAD + the new tag to --remote")
    s.add_argument("--release", action="store_true",
                   help="shortcut for --bump patch --commit --tag --push")
    s.add_argument("--remote", default="origin",
                   help="git remote name for --push (default: origin)")
    s.set_defaults(fn=cmd_publish)

    args = p.parse_args(argv)
    # Propagate --quiet to subcommands that don't declare it explicitly
    if not hasattr(args, "quiet"):
        args.quiet = False
    if not hasattr(args, "dry_run"):
        args.dry_run = False
    if not hasattr(args, "offline"):
        args.offline = False
    return args.fn(args)


def main_cli() -> None:
    """Console-scripts entry point (installed by ``pip install ClaudeCleo``)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    main_cli()
