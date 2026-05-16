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

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .checks import discover_items, parse_frontmatter
from .security import (
    SecurityViolation,
    validate_dest_item_name,
    validate_git_ref,
    validate_hook_size,
    validate_item_source,
    validate_manifest_file_not_symlink,
    validate_package_has_artifacts,
    validate_package_manifest,
)
from .semver import parse_version

_BUMP_LEVELS = ("patch", "minor", "major")
_PRESERVED_FIELDS = ("name", "type", "version", "description", "homepage")

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


def merge_manifest(existing: dict | None, detected: dict) -> dict:
    """Combine an existing cleo.json with detected fields.

    For every preserved field, the existing value wins. Detected values are
    only used when the existing dict has no entry for that field. Detected
    values that are None are skipped entirely (no key emitted).
    """
    out: dict = {}
    existing = existing or {}
    for field in _PRESERVED_FIELDS:
        if field in existing:
            out[field] = existing[field]
        elif detected.get(field) is not None:
            out[field] = detected[field]
    # Preserve any non-standard fields the author added.
    for k, v in existing.items():
        if k not in out:
            out[k] = v
    return out


def _serialize_manifest(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def write_manifest(pkg_dir: Path, data: dict) -> bool:
    """Atomically write data to pkg_dir/cleo.json. Return True if disk
    content actually changed; False if the new bytes match what was already
    on disk."""
    path = pkg_dir / "cleo.json"
    new_text = _serialize_manifest(data)
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
            if current == new_text:
                return False
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _gate(errors: list[str], fn, *args, **kwargs) -> None:
    """Run a security gate and append its message to errors on failure."""
    try:
        fn(*args, **kwargs)
    except SecurityViolation as exc:
        errors.append(str(exc))


def validate_publish(pkg_dir: Path, *, skip_dry_install: bool = False) -> list[str]:
    """Run the security gates, frontmatter checks, and (unless skipped) a
    dry install. Return a list of error strings; empty means pass.

    skip_dry_install is for unit tests of the cheap gates — the dry install
    is exercised by its own dedicated tests in Task 7.
    """
    errors: list[str] = []

    cleo_json = pkg_dir / "cleo.json"
    mcp_json = pkg_dir / "mcp.json"
    _gate(errors, validate_manifest_file_not_symlink, cleo_json)
    _gate(errors, validate_manifest_file_not_symlink, mcp_json)

    manifest_data: dict | None = None
    if cleo_json.exists() and not errors:
        try:
            manifest_data = json.loads(cleo_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"cleo.json is not valid JSON: {exc}")

    pkg_name = (manifest_data or {}).get("name", "<unknown>")
    _gate(errors, validate_package_manifest, manifest_data, pkg_name)

    items = discover_items(pkg_dir)
    for type_, item_name, item_path in items:
        _gate(errors, validate_dest_item_name, item_name)
        src = item_path.parent if type_ == "skill" else item_path
        _gate(errors, validate_item_source, src, pkg_dir)
        if type_ == "hook":
            _gate(errors, validate_hook_size, item_path)

    for type_, item_name, item_path in items:
        if type_ == "hook":
            continue
        try:
            data, err = parse_frontmatter(item_path)
        except OSError as exc:
            errors.append(f"{item_path.name}: cannot read ({exc})")
            continue
        if data is None:
            errors.append(f"{item_path.name}: frontmatter error: {err}")
            continue
        for required in ("name", "description"):
            val = data.get(required)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{item_path.name}: missing or empty frontmatter field {required!r}")

    pkg_type = (manifest_data or {}).get("type", "skills-pack")
    _gate(
        errors, validate_package_has_artifacts,
        items_count=len(items),
        has_mcp_json=mcp_json.exists(),
        pkg_type=pkg_type,
    )

    if not skip_dry_install and not errors:
        errors.extend(_dry_install(pkg_dir))

    return errors


def _file_url(path: Path) -> str:
    return "file:///" + str(path.resolve()).replace("\\", "/")


def _has_any_tag(pkg_dir: Path) -> bool:
    raw = _git_capture(pkg_dir, "tag", "--list")
    return bool(raw)


def _is_git_repo(pkg_dir: Path) -> bool:
    return (pkg_dir / ".git").exists()


def _dry_install(pkg_dir: Path) -> list[str]:
    """Run install_package against pkg_dir in a temp project; return error list."""
    errors: list[str] = []

    if not _is_git_repo(pkg_dir):
        errors.append("not a git repository — run `git init` and tag a release before publishing")
        return errors
    if not _has_any_tag(pkg_dir):
        errors.append("no git tags found — run `git tag v0.0.0` before publishing")
        return errors

    version = _highest_tag_version(pkg_dir) or "0.0.0"

    pkg_name: str | None = None
    cleo_json = pkg_dir / "cleo.json"
    if cleo_json.exists():
        try:
            pkg_name = json.loads(cleo_json.read_text(encoding="utf-8")).get("name")
        except json.JSONDecodeError:
            pkg_name = None
    if not pkg_name:
        errors.append("package cleo.json missing `name` — dry install needs one")
        return errors

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import cleo as cleo_mod  # type: ignore  # noqa: PLC0415

    tmp_root = Path(tempfile.mkdtemp(prefix="cleo-publish-"))
    try:
        proj = tmp_root / "proj"
        proj.mkdir()
        proj_manifest = {
            "name": "publish-validate",
            "repositories": [{"type": "git", "url": _file_url(pkg_dir)}],
            "require": {pkg_name: "*"},
            "require-local": {},
            "require-user": {},
        }
        (proj / "cleo.json").write_text(json.dumps(proj_manifest), encoding="utf-8")

        import os as _os
        prev_home = _os.environ.get("CLEO_USER_HOME")
        _os.environ["CLEO_USER_HOME"] = str(tmp_root / "fake-home")
        try:
            result = cleo_mod.install_package(
                proj,
                pkg_name,
                _file_url(pkg_dir),
                "*",
                cleo_mod.BUCKET_PROJECT,
                quiet=True,
            )
        finally:
            if prev_home is None:
                _os.environ.pop("CLEO_USER_HOME", None)
            else:
                _os.environ["CLEO_USER_HOME"] = prev_home

        if result is None:
            errors.append(f"dry install of {pkg_name}@{version} failed — see prior error messages")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return errors


def tag_exists(pkg_dir: Path, tag: str) -> bool:
    validate_git_ref(tag)
    out = _git_capture(pkg_dir, "tag", "--list", tag)
    return bool(out)


def tag_at_head(pkg_dir: Path, tag: str) -> bool:
    """True if the named tag points at the same commit as HEAD."""
    validate_git_ref(tag)
    head = _git_capture(pkg_dir, "rev-parse", "HEAD")
    tag_sha = _git_capture(pkg_dir, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if not head or not tag_sha:
        return False
    return head == tag_sha


def current_remote_url(pkg_dir: Path, remote: str) -> str | None:
    validate_git_ref(remote)
    return _git_capture(pkg_dir, "remote", "get-url", remote)


def working_tree_dirty(pkg_dir: Path, paths: list[str]) -> bool:
    """True if any of `paths` has uncommitted changes or is untracked."""
    out = _git_capture(pkg_dir, "status", "--porcelain", "--", *paths)
    return bool(out)


def commit_file(pkg_dir: Path, path: str, message: str) -> None:
    """Stage and commit a single file. Raise RuntimeError if nothing to commit."""
    subprocess.run(
        ["git", "-C", str(pkg_dir), "add", "--", path],
        check=True, capture_output=True,
    )
    staged = _git_capture(pkg_dir, "diff", "--cached", "--name-only", "--", path)
    if not staged:
        raise RuntimeError(f"nothing to commit for {path}")
    r = subprocess.run(
        ["git", "-C", str(pkg_dir), "commit", "-m", message, "--", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
