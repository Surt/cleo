"""Security gates for cleo package installation.

Pure validators. No I/O side-effects beyond reading paths the caller asks
about. Each gate raises SecurityViolation on failure; cleo.py catches and
converts to a CLI error.
"""
from __future__ import annotations

import re


class SecurityViolation(Exception):
    """Raised when a package fails a hard security gate."""


VALID_PKG_TYPES = frozenset({"skills-pack", "mcp-server", "mixed"})
_PKG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")


def validate_package_manifest(manifest: dict | None, expected_name: str) -> None:
    """Validate a package's own cleo.json.

    A None manifest means the file was absent — allowed.
    A dict with missing optional fields — allowed.
    A dict with a present-but-malformed field — SecurityViolation.
    """
    if manifest is None:
        return
    if not isinstance(manifest, dict):
        raise SecurityViolation(
            f"{expected_name}: package cleo.json must be a JSON object"
        )
    if "type" in manifest and manifest["type"] not in VALID_PKG_TYPES:
        raise SecurityViolation(
            f"{expected_name}: unknown type {manifest['type']!r} "
            f"(expected one of {', '.join(sorted(VALID_PKG_TYPES))})"
        )
    if "name" in manifest:
        name = manifest["name"]
        if not isinstance(name, str) or not _PKG_NAME_RE.match(name):
            raise SecurityViolation(
                f"{expected_name}: package name {name!r} must match "
                f"<vendor>/<name> with [a-z0-9._-] chars only"
            )


def validate_dest_item_name(name: str) -> None:
    """Reject item names that could escape the destination directory."""
    if not name:
        raise SecurityViolation("item name is empty")
    if "\x00" in name:
        raise SecurityViolation(f"item name {name!r} contains null byte")
    if "/" in name or "\\" in name:
        raise SecurityViolation(
            f"item name {name!r} contains path separator"
        )
    if name in (".", ".."):
        raise SecurityViolation(f"item name {name!r} is a reserved path token")
