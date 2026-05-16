"""Security gates for cleo package installation.

Pure validators. No I/O side-effects beyond reading paths the caller asks
about. Each gate raises SecurityViolation on failure; cleo.py catches and
converts to a CLI error.
"""
from __future__ import annotations

import re
from pathlib import Path


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


def validate_item_source(src: Path, cache_root: Path) -> None:
    """Refuse to materialize items whose real path escapes the cache root.

    Symlinks pointing outside the package are the main attack vector here —
    shutil.copytree/copy2 follow them silently. Resolve both paths and
    require containment.
    """
    try:
        real_src = src.resolve(strict=True)
        real_cache = cache_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SecurityViolation(
            f"cannot resolve source path {src}: {exc}"
        ) from exc
    try:
        real_src.relative_to(real_cache)
    except ValueError:
        raise SecurityViolation(
            f"source {src} resolves outside the package cache ({real_src})"
        ) from None


HOOK_SIZE_MAX_BYTES = 64 * 1024  # 64 KiB


def validate_hook_size(hook_path: Path) -> None:
    size = hook_path.stat().st_size
    if size > HOOK_SIZE_MAX_BYTES:
        raise SecurityViolation(
            f"hook {hook_path.name} ({size} bytes) exceeds limit of "
            f"{HOOK_SIZE_MAX_BYTES} bytes"
        )
