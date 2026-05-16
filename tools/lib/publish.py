"""Package-author tooling for cleo: generate manifest, validate, release.

Three logical units live here (kept in one file for now — split if it grows
past ~300 lines):
  - manifest: detect/merge/bump/write the package's own cleo.json
  - validate: run the same gates `cleo install` runs, plus frontmatter +
              a dry install against a temp project
  - gitops:   wrap the subprocess git calls publish needs (tag, push, ...)

All git invocations route through validate_git_ref (lib/security) and use
`--` to separate options from positional refs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .semver import parse_version

_BUMP_LEVELS = ("patch", "minor", "major")

# Parse host and vendor/name out of either https://host/vendor/name(.git) or git@host:vendor/name(.git)
_REMOTE_HTTPS_RE = re.compile(r"^https?://([^/]+)/([^/]+/[^/]+?)(?:\.git)?/?$")
_REMOTE_SSH_RE = re.compile(r"^[\w.-]+@([\w.-]+):([^/]+/[^/]+?)(?:\.git)?/?$")

# Artifact dirs Claude Code understands. Mirrors COMPONENT_GLOBS in lib/checks.py
# but here we only need the parent dir names — discover_items walks the globs
# for the validate path.
_ARTIFACT_DIRS = ("rules", "skills", "agents", "commands", "hooks")


def bump_version(current: str, level: str) -> str:
    """Bump a semver string and return the new value as `MAJOR.MINOR.PATCH`.

    Drops any pre-release/build suffix. Raises ValueError if `current` is
    not parseable or `level` is not one of patch/minor/major.
    """
    if level not in _BUMP_LEVELS:
        raise ValueError(f"bump level {level!r} not in {_BUMP_LEVELS}")
    v = parse_version(current)
    if v is None:
        raise ValueError(f"version {current!r} is not parseable semver")
    if level == "patch":
        return f"{v.major}.{v.minor}.{v.patch + 1}"
    if level == "minor":
        return f"{v.major}.{v.minor + 1}.0"
    return f"{v.major + 1}.0.0"


def _git_capture(pkg_dir: Path, *args: str) -> str | None:
    """Run a git command, return stdout stripped, or None on failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(pkg_dir), *args],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _parse_remote(url: str) -> tuple[str, str] | None:
    """Return (host, vendor/name) parsed from a git remote URL, or None."""
    for pat in (_REMOTE_HTTPS_RE, _REMOTE_SSH_RE):
        m = pat.match(url)
        if m:
            return m.group(1), m.group(2)
    return None


def _highest_tag_version(pkg_dir: Path) -> str | None:
    raw = _git_capture(pkg_dir, "tag", "--list")
    if not raw:
        return None
    best = None
    for tag in raw.splitlines():
        v = parse_version(tag.strip())
        if v is None or v.pre:
            continue
        if best is None or v > best:
            best = v
    return f"{best.major}.{best.minor}.{best.patch}" if best else None


def detect_package(pkg_dir: Path) -> dict:
    """Inspect a package working tree and return suggested manifest fields.

    Returned dict has keys: name (str|None), type (str), version (str),
    homepage (str|None). Caller merges this with any existing cleo.json.
    """
    has_mcp = (pkg_dir / "mcp.json").is_file()
    has_artifacts = any((pkg_dir / d).is_dir() for d in _ARTIFACT_DIRS)
    if has_mcp and has_artifacts:
        pkg_type = "mixed"
    elif has_mcp:
        pkg_type = "mcp-server"
    else:
        pkg_type = "skills-pack"

    remote = _git_capture(pkg_dir, "remote", "get-url", "origin")
    parsed = _parse_remote(remote) if remote else None
    if parsed:
        host, name = parsed
        homepage = f"https://{host}/{name}"
    else:
        name = None
        homepage = None

    version = _highest_tag_version(pkg_dir) or "0.0.0"

    return {"name": name, "type": pkg_type, "version": version, "homepage": homepage}
