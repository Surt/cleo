"""Shared checks for cleo package frontmatter validation.

Used by cleo.py when reading package item metadata.
Ported from claude-o-matic/tools/lib/checks.py with project-specific refs removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required: pip install pyyaml"
    ) from exc


# ---- Constants ----------------------------------------------------------

KEBAB_CASE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

DESCRIPTION_HARD_MAX = 300
DESCRIPTION_WARN_MAX = 160
DESCRIPTION_MIN = 20

RULE_BODY_WARN_LINES = 200
SKILL_BODY_WARN_LINES = 300

DEDUPE_THRESHOLD = 0.50

VAGUE_PATTERNS = [
    re.compile(r"\bbe\s+careful\b", re.IGNORECASE),
    re.compile(r"\bclean\s+code\b", re.IGNORECASE),
    re.compile(r"\bformat\s+(properly|correctly|nicely)\b", re.IGNORECASE),
    re.compile(r"\bappropriate\s+error\s+handling\b", re.IGNORECASE),
    re.compile(r"\bgood\s+practices\b", re.IGNORECASE),
    re.compile(r"\bproperly\s+(handle|implement|test)\b", re.IGNORECASE),
    re.compile(r"\bif\s+possible\b", re.IGNORECASE),
    re.compile(r"\btry\s+to\b", re.IGNORECASE),
]

LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ticket-prefix", re.compile(r"\b[A-Z]{2,5}-\d{1,6}\b")),
    ("home-path-linux", re.compile(r"/home/[a-zA-Z][\w.-]*")),
    ("home-path-windows", re.compile(r"C:\\Users\\[\w.-]+")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
]

# Component layout within a cleo package repo.
COMPONENT_GLOBS = {
    "rule": "rules/*.md",
    "skill": "skills/*/SKILL.md",
    "agent": "agents/*.md",
    "command": "commands/*.md",
    "hook": "hooks/*.sh",
}

KNOWN_FIELDS: dict[str, set[str]] = {
    "common": {"name", "description", "scope", "tech", "teams", "requires"},
    "rule": {"paths"},
    "skill": {"disable-model-invocation"},
    "agent": {"tools", "model"},
    "command": set(),
}

SKILL_TRIGGER_PHRASES = ("trigger when", "use when", "use this", "invoke", "apply when")


@dataclass
class Finding:
    severity: str  # "error" | "warning" | "info"
    path: Path | None
    message: str

    def __str__(self) -> str:
        loc = f"{self.path}: " if self.path else ""
        return f"{self.severity.upper()}: {loc}{self.message}"


# ---- Frontmatter parsing ------------------------------------------------


def parse_frontmatter(source: "Path | str") -> tuple[dict | None, str | None]:
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None, "missing frontmatter delimiter `---`"
    body = text.split("\n", 1)[1] if text.startswith("---\n") else text.split("\r\n", 1)[1]
    end = re.search(r"^---\s*$", body, flags=re.MULTILINE)
    if not end:
        return None, "frontmatter not terminated with `---`"
    raw = body[: end.start()]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, f"invalid YAML: {exc}"
    if data is None:
        return None, "frontmatter block is empty"
    if not isinstance(data, dict):
        return None, "frontmatter is not a YAML mapping"
    return data, None


def split_frontmatter_and_body(text: str) -> tuple[str, str]:
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return "", text
    head = text.split("\n", 1)[1] if text.startswith("---\n") else text.split("\r\n", 1)[1]
    end = re.search(r"^---\s*$", head, flags=re.MULTILINE)
    if not end:
        return "", text
    return head[: end.start()], head[end.end():].lstrip("\n")


# ---- Component detection in a package repo ------------------------------


def discover_items(package_root: Path) -> list[tuple[str, str, Path]]:
    """Return [(type, name, path)] for all items in a cleo package repo."""
    results: list[tuple[str, str, Path]] = []
    seen: set[Path] = set()
    for type_, glob in COMPONENT_GLOBS.items():
        for hit in sorted(package_root.glob(glob)):
            resolved = hit.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if type_ == "skill":
                name = hit.parent.name
            elif type_ in ("rule", "agent", "command"):
                name = hit.stem
            elif type_ == "hook":
                name = hit.stem
            else:
                continue
            results.append((type_, name, hit))
    return results


# ---- Body inspection ----------------------------------------------------


def strip_code(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`\n]+`", "", text)
    return text


def find_vague_directives(prose: str) -> list[tuple[str, int]]:
    hits = []
    for pattern in VAGUE_PATTERNS:
        for m in pattern.finditer(prose):
            line = prose.count("\n", 0, m.start()) + 1
            hits.append((m.group(0), line))
    return hits


def find_leaks(text: str) -> dict[str, list[str]]:
    prose = strip_code(text)
    out: dict[str, list[str]] = {}
    for label, pattern in LEAK_PATTERNS:
        matches = sorted({m.group(0) for m in pattern.finditer(prose)})
        if matches:
            out[label] = matches
    return out


# ---- Dedupe -------------------------------------------------------------


def jaccard_words(text: str) -> set[str]:
    return set(re.findall(r"\b[a-z]{4,}\b", text.lower()))


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)
