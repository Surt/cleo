"""Minimal semver parsing and constraint resolution for cleo.

Supports the constraint forms used by Composer/npm:
  *          any version
  1.2.3      exact
  ^1.2.3     >=1.2.3 <2.0.0  (compatible with)
  ~1.2.3     >=1.2.3 <1.3.0  (approximately)
  >=1.2.3
  <=1.2.3
  >1.2.3
  <1.2.3
  >=1.0 <2.0 (space-separated AND of range clauses)

No third-party dependencies.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .security import SecurityViolation, validate_git_ref


# ---- Version ---------------------------------------------------------------


@dataclass(order=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: str = field(default="", compare=False)

    sort_key: tuple = field(init=False, repr=False, compare=True)

    def __post_init__(self) -> None:
        self.sort_key = (self.major, self.minor, self.patch, 0 if not self.pre else -1)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.pre}" if self.pre else base


_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:-(?P<pre>[a-zA-Z0-9._-]+))?$"
)


def parse_version(s: str) -> Optional[Version]:
    m = _VERSION_RE.match(s.strip())
    if not m:
        return None
    return Version(
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(m.group("patch") or "0"),
        pre=m.group("pre") or "",
    )


# ---- Constraint ------------------------------------------------------------


@dataclass
class _Clause:
    op: str   # "", "^", "~", ">=", "<=", ">", "<", "="
    ver: Version

    def matches(self, v: Version) -> bool:
        if v.pre:
            return False
        if self.op in ("", "="):
            return v == self.ver
        if self.op == "^":
            if self.ver.major > 0:
                return v >= self.ver and v.major == self.ver.major
            if self.ver.minor > 0:
                return v >= self.ver and v.major == 0 and v.minor == self.ver.minor
            return v >= self.ver and v.major == 0 and v.minor == 0 and v.patch == self.ver.patch
        if self.op == "~":
            return v >= self.ver and v.major == self.ver.major and v.minor == self.ver.minor
        if self.op == ">=":
            return v >= self.ver
        if self.op == "<=":
            return v <= self.ver
        if self.op == ">":
            return v > self.ver
        if self.op == "<":
            return v < self.ver
        return False


_CLAUSE_RE = re.compile(r"([~^]|>=|<=|>|<|=)?(\d[\d.]*(?:-[a-zA-Z0-9._-]+)?)")


def parse_constraint(constraint: str) -> list[list[_Clause]]:
    """Parse a constraint string into OR-of-AND-of-clauses.

    Currently only a single AND group is supported (space-separated clauses).
    Multiple OR groups (||) are not implemented.
    """
    if constraint.strip() in ("*", ""):
        return [[]]  # match anything

    clauses: list[_Clause] = []
    for m in _CLAUSE_RE.finditer(constraint):
        op = m.group(1) or ""
        ver = parse_version(m.group(2))
        if ver is None:
            continue
        clauses.append(_Clause(op=op, ver=ver))
    return [clauses]


def matches_constraint(version: Version, constraint: str) -> bool:
    groups = parse_constraint(constraint)
    if not groups:
        return True
    for and_group in groups:
        if not and_group:
            return True
        if all(clause.matches(version) for clause in and_group):
            return True
    return False


# ---- Git tag resolution ----------------------------------------------------


def fetch_tags(url: str, *, timeout: int = 30) -> list[str]:
    """Return raw tag names from a remote git repo (does not clone).

    Uses `git ls-remote --tags`. Returns [] on any failure.
    """
    try:
        validate_git_ref(url)
    except SecurityViolation:
        return []
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--", url],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return []
        tags = []
        for line in result.stdout.splitlines():
            # lines look like: <sha>\trefs/tags/<name>
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1].startswith("refs/tags/"):
                tag = parts[1].removeprefix("refs/tags/")
                if not tag.endswith("^{}"):  # skip peeled refs
                    tags.append(tag)
        return tags
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def resolve_version(url: str, constraint: str, *, offline: bool = False) -> Optional[tuple[str, str]]:
    """Return (version_str, tag_name) for the highest tag matching constraint.

    Returns None if no matching tag found or git is unavailable.
    When offline=True, skips the network call and returns None.
    """
    if offline:
        return None
    tags = fetch_tags(url)
    candidates: list[tuple[Version, str]] = []
    for tag in tags:
        v = parse_version(tag)
        if v is None:
            continue
        if matches_constraint(v, constraint):
            candidates.append((v, tag))
    if not candidates:
        return None
    best_ver, best_tag = max(candidates, key=lambda x: x[0])
    return str(best_ver), best_tag


def resolve_commit(url: str, tag: str) -> Optional[str]:
    """Return the commit SHA that a tag points to (follows peeled refs)."""
    try:
        validate_git_ref(url)
        validate_git_ref(tag)
    except SecurityViolation:
        return None
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--", url, tag, f"{tag}^{{}}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        # Prefer the peeled ref (^{}) if present — that's the commit, not the tag object.
        peeled: Optional[str] = None
        direct: Optional[str] = None
        for line in result.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) != 2:
                continue
            sha, ref = parts
            if ref == f"refs/tags/{tag}^{{}}":
                peeled = sha
            elif ref == f"refs/tags/{tag}":
                direct = sha
        return peeled or direct
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
