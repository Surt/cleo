"""Security gates for cleo package installation.

Pure validators. No I/O side-effects beyond reading paths the caller asks
about. Each gate raises SecurityViolation on failure; cleo.py catches and
converts to a CLI error.
"""
from __future__ import annotations

import re
import stat
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
            "package cleo.json must be a JSON object"
        )
    if "type" in manifest and manifest["type"] not in VALID_PKG_TYPES:
        raise SecurityViolation(
            f"unknown type {manifest['type']!r} "
            f"(expected one of {', '.join(sorted(VALID_PKG_TYPES))})"
        )
    if "name" in manifest:
        name = manifest["name"]
        if not isinstance(name, str) or not _PKG_NAME_RE.match(name):
            raise SecurityViolation(
                f"package name {name!r} must match "
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


def validate_package_has_artifacts(
    items_count: int, has_mcp_json: bool, pkg_type: str
) -> None:
    """Reject packages that contribute nothing to .claude/ or settings.json.

    A repo with only a cleo.json (or just a README) is almost certainly the
    wrong repo or an unfinished package. Refusing to install is friendlier
    than silently writing an empty lock entry.
    """
    if pkg_type == "skills-pack" and items_count == 0:
        raise SecurityViolation(
            "package has no recognized artifacts (rules/, skills/, agents/, "
            "commands/, hooks/) — is this the right repo?"
        )
    if pkg_type == "mcp-server" and not has_mcp_json:
        raise SecurityViolation(
            "package declares type 'mcp-server' but ships no mcp.json"
        )
    if pkg_type == "mixed" and items_count == 0 and not has_mcp_json:
        raise SecurityViolation(
            "package declares type 'mixed' but ships neither artifacts nor mcp.json"
        )


def validate_package_ref(ref: str) -> None:
    """Validate a package reference (`<vendor>/<name>`) before it touches paths.

    Catches CLI-supplied and project-manifest-supplied package names that
    would escape the cache directory via `..` segments, or pass a value
    starting with `-` into a later git subprocess.

    Uses the same regex as validate_package_manifest's `name` field check —
    references and manifest names share the same shape.
    """
    if not ref:
        raise SecurityViolation("package reference is empty")
    if not _PKG_NAME_RE.match(ref):
        raise SecurityViolation(
            f"package reference {ref!r} must match <vendor>/<name> "
            f"with [a-z0-9._-] chars only"
        )


def validate_git_ref(value: str) -> None:
    """Reject tag/URL strings that could smuggle args into git subprocess.

    Defense alongside the `--` separator used at call sites: even with
    `--`, a leading-dash value still confuses humans reading commands and
    some shells/runners. Null bytes and newlines break command logging.
    """
    if not value:
        raise SecurityViolation("git ref is empty")
    if "\x00" in value:
        raise SecurityViolation(f"git ref {value!r} contains null byte")
    if "\n" in value or "\r" in value:
        raise SecurityViolation(f"git ref {value!r} contains newline")
    if value.startswith("-"):
        raise SecurityViolation(
            f"git ref {value!r} has leading '-' (potential arg injection)"
        )


def validate_manifest_file_not_symlink(path: Path) -> None:
    """Refuse to read package metadata files that are symlinks.

    cleo.json and mcp.json get `read_text` which follows symlinks. A
    symlinked manifest could redirect to host files. Missing files are
    fine — the caller decides whether absence is an error.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise SecurityViolation(
            f"manifest file {path.name} is a symlink — refusing to follow"
        )
