"""Source-form parsing for `cleo require`.

Accepts the six source shapes the vercel-labs/skills CLI accepts:
1. github shorthand: `owner/repo`
2. full git URL: `https://github.com/owner/repo[.git]`
3. github subdir URL: `https://github.com/owner/repo/tree/<ref>/<subpath>`
4. gitlab URL: `https://gitlab.com/org/repo`
5. ssh git URL: `git@host:owner/repo.git`
6. local path: `./relative` or `/absolute`

All parsing is offline and pure. Network access lives in cleo.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class SourceKind(str, Enum):
    GITHUB_SHORTHAND = "github_shorthand"
    GIT_URL = "git_url"
    GIT_SUBDIR = "git_subdir"
    LOCAL_PATH = "local_path"


@dataclass
class Source:
    kind: SourceKind
    name: str                       # vendor/pkg form, always present
    url: Optional[str] = None       # clone URL (None for LOCAL_PATH)
    subpath: Optional[str] = None   # path inside repo (GIT_SUBDIR only)
    ref: Optional[str] = None       # branch/tag (GIT_SUBDIR only; from /tree/<ref>/)
    local_path: Optional[Path] = None  # resolved absolute path (LOCAL_PATH only)


_SHORTHAND_RE = re.compile(r"^([a-zA-Z0-9][\w.-]*)/([a-zA-Z0-9][\w.-]*)$")
_GITHUB_TREE_RE = re.compile(
    r"^(https://github\.com/([^/]+)/([^/]+?))(?:\.git)?/tree/([^/]+)/(.+?)/?$"
)
_GITHUB_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_GITLAB_URL_RE = re.compile(r"^https://gitlab\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_SSH_URL_RE = re.compile(r"^[a-zA-Z0-9._-]+@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$")


def parse_source(spec: str) -> Source:
    """Parse a positional source argument. Raises ValueError on garbage."""
    if not spec:
        raise ValueError("empty source")
    s = spec.strip()

    # 1. Subdir URL (must precede plain GitHub URL match)
    m = _GITHUB_TREE_RE.match(s)
    if m:
        repo_url, owner, repo, ref, subpath = m.groups()
        leaf = subpath.rstrip("/").rsplit("/", 1)[-1]
        return Source(
            kind=SourceKind.GIT_SUBDIR,
            name=f"{owner}/{leaf}",
            url=repo_url,
            subpath=subpath.rstrip("/"),
            ref=ref,
        )

    # 2. Plain GitHub URL
    m = _GITHUB_URL_RE.match(s)
    if m:
        owner, repo = m.groups()
        return Source(kind=SourceKind.GIT_URL, name=f"{owner}/{repo}", url=s)

    # 3. GitLab URL
    m = _GITLAB_URL_RE.match(s)
    if m:
        owner, repo = m.groups()
        return Source(kind=SourceKind.GIT_URL, name=f"{owner}/{repo}", url=s)

    # 4. SSH git URL
    m = _SSH_URL_RE.match(s)
    if m:
        owner, repo = m.groups()
        return Source(kind=SourceKind.GIT_URL, name=f"{owner}/{repo}", url=s)

    # 5. Local path (relative or absolute)
    if s.startswith("./") or s.startswith("/") or s.startswith("../") or (len(s) >= 3 and s[1] == ":"):
        p = Path(s).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"local path does not exist: {s}")
        if not p.is_dir():
            raise ValueError(f"local path is not a directory: {s}")
        return Source(
            kind=SourceKind.LOCAL_PATH,
            name=f"local/{p.name}",
            local_path=p,
        )

    # 6. GitHub shorthand
    m = _SHORTHAND_RE.match(s)
    if m:
        owner, repo = m.groups()
        return Source(
            kind=SourceKind.GITHUB_SHORTHAND,
            name=f"{owner}/{repo}",
            url=f"https://github.com/{owner}/{repo}",
        )

    raise ValueError(f"unrecognized source format: {spec!r}")
