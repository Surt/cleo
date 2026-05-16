"""Filesystem scan for untracked SKILL.md directories.

Used by `cleo update` to surface skills installed by other tools (notably
vercel-labs/skills) so users can adopt them via `cleo update --adopt`.

Read-only: this module never modifies the filesystem.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


CLEO_NAMESPACE_PREFIX = "cleo-"


@dataclass
class Discovery:
    skill_name: str            # directory name in .claude/skills/
    path: Path                 # absolute path of the discovered dir
    is_symlink: bool
    symlink_target: Optional[Path] = None  # resolved target if is_symlink
    git_remote: Optional[str] = None       # populated by enrich_provenance


def scan_untracked(skills_dir: Path, tracked_paths: Iterable[Path]) -> list[Discovery]:
    """Return Discoveries for every SKILL.md-containing dir under `skills_dir`
    that is not already in `tracked_paths` and not cleo-namespaced.
    """
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []
    tracked = {p.resolve() for p in tracked_paths}
    out: list[Discovery] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(CLEO_NAMESPACE_PREFIX):
            continue
        if entry.resolve() in tracked:
            continue
        if not (entry / "SKILL.md").exists():
            continue
        is_link = entry.is_symlink()
        target = entry.resolve() if is_link else None
        out.append(Discovery(
            skill_name=entry.name,
            path=entry,
            is_symlink=is_link,
            symlink_target=target,
        ))
    return out


def enrich_provenance(d: Discovery) -> Discovery:
    """Best-effort: populate d.git_remote if the discovery is a symlink
    pointing inside a git working tree.

    Walks up from symlink target until a `.git` directory is found, then
    reads `origin` URL from `.git/config`. Parses the config file directly
    rather than invoking git, so it works even when git isn't on PATH.
    """
    if not d.is_symlink or d.symlink_target is None:
        return d
    cur = d.symlink_target
    for parent in [cur, *cur.parents]:
        git_dir = parent / ".git"
        config = git_dir / "config"
        if config.is_file():
            try:
                text = config.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return d
            d.git_remote = _extract_origin_url(text)
            return d
    return d


def _extract_origin_url(config_text: str) -> Optional[str]:
    """Find [remote "origin"] block, return its url. Tiny ini-style parser."""
    in_origin = False
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_origin = (line == '[remote "origin"]')
            continue
        if in_origin and line.startswith("url"):
            _, _, value = line.partition("=")
            return value.strip() or None
    return None
