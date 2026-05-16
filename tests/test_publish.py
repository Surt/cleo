"""Tests for tools/lib/publish.py."""
from __future__ import annotations

import json as _json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from lib.publish import bump_version, detect_package, merge_manifest, write_manifest


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(pkg_dir: Path, remote: str | None = None) -> None:
    _git(pkg_dir, "init", "-q", "-b", "main")
    _git(pkg_dir, "config", "user.email", "t@t.t")
    _git(pkg_dir, "config", "user.name", "t")
    if remote:
        _git(pkg_dir, "remote", "add", "origin", remote)


class TestBumpVersion:
    def test_patch(self):
        assert bump_version("1.2.3", "patch") == "1.2.4"

    def test_minor_resets_patch(self):
        assert bump_version("1.2.3", "minor") == "1.3.0"

    def test_major_resets_minor_and_patch(self):
        assert bump_version("1.2.3", "major") == "2.0.0"

    def test_from_zero(self):
        assert bump_version("0.0.0", "patch") == "0.0.1"
        assert bump_version("0.0.0", "minor") == "0.1.0"
        assert bump_version("0.0.0", "major") == "1.0.0"

    def test_accepts_v_prefix(self):
        assert bump_version("v1.2.3", "patch") == "1.2.4"

    def test_drops_prerelease(self):
        assert bump_version("1.2.3-rc1", "patch") == "1.2.4"

    def test_rejects_non_semver(self):
        with pytest.raises(ValueError, match="not parseable"):
            bump_version("not-a-version", "patch")

    def test_rejects_unknown_level(self):
        with pytest.raises(ValueError, match="level"):
            bump_version("1.2.3", "weird")


class TestDetectPackage:
    def test_skills_pack_inferred_from_rules_dir(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        _init_repo(tmp_path, remote="https://github.com/acme/widgets.git")
        d = detect_package(tmp_path)
        assert d["type"] == "skills-pack"
        assert d["name"] == "acme/widgets"
        assert d["homepage"] == "https://github.com/acme/widgets"

    def test_mcp_server_inferred_from_mcp_json(self, tmp_path):
        (tmp_path / "mcp.json").write_text('{"command": "x"}', encoding="utf-8")
        _init_repo(tmp_path, remote="https://github.com/acme/srv.git")
        d = detect_package(tmp_path)
        assert d["type"] == "mcp-server"

    def test_mixed_inferred_from_both(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        (tmp_path / "mcp.json").write_text('{"command": "x"}', encoding="utf-8")
        _init_repo(tmp_path, remote="https://github.com/acme/m.git")
        d = detect_package(tmp_path)
        assert d["type"] == "mixed"

    def test_version_from_highest_semver_tag(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        _init_repo(tmp_path, remote="https://github.com/acme/widgets.git")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-qm", "v1")
        _git(tmp_path, "tag", "v0.9.0")
        _git(tmp_path, "tag", "v1.2.3")
        _git(tmp_path, "tag", "v1.0.0")
        d = detect_package(tmp_path)
        assert d["version"] == "1.2.3"

    def test_version_defaults_to_0_0_0_when_no_tags(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        _init_repo(tmp_path, remote="https://github.com/acme/widgets.git")
        d = detect_package(tmp_path)
        assert d["version"] == "0.0.0"

    def test_name_none_when_no_remote(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        _init_repo(tmp_path, remote=None)
        d = detect_package(tmp_path)
        assert d["name"] is None

    def test_handles_ssh_remote(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        _init_repo(tmp_path, remote="git@github.com:acme/widgets.git")
        d = detect_package(tmp_path)
        assert d["name"] == "acme/widgets"
        assert d["homepage"] == "https://github.com/acme/widgets"

    def test_non_github_host_in_homepage(self, tmp_path):
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: x\n---\nbody\n", encoding="utf-8")
        _init_repo(tmp_path, remote="git@gitlab.com:acme/widgets.git")
        d = detect_package(tmp_path)
        assert d["name"] == "acme/widgets"
        assert d["homepage"] == "https://gitlab.com/acme/widgets"


class TestMergeManifest:
    def test_no_existing_uses_all_detected(self):
        detected = {"name": "a/b", "type": "skills-pack", "version": "0.0.0", "homepage": "https://github.com/a/b"}
        merged = merge_manifest(None, detected)
        assert merged == detected

    def test_existing_name_wins(self):
        existing = {"name": "x/y"}
        detected = {"name": "a/b", "type": "skills-pack", "version": "0.0.0", "homepage": None}
        merged = merge_manifest(existing, detected)
        assert merged["name"] == "x/y"

    def test_existing_type_wins(self):
        existing = {"type": "mixed"}
        detected = {"name": "a/b", "type": "skills-pack", "version": "0.0.0", "homepage": None}
        merged = merge_manifest(existing, detected)
        assert merged["type"] == "mixed"

    def test_existing_version_wins(self):
        existing = {"version": "5.0.0"}
        detected = {"name": "a/b", "type": "skills-pack", "version": "1.2.3", "homepage": None}
        merged = merge_manifest(existing, detected)
        assert merged["version"] == "5.0.0"

    def test_existing_description_preserved(self):
        existing = {"description": "hand-written"}
        detected = {"name": "a/b", "type": "skills-pack", "version": "0.0.0", "homepage": None}
        merged = merge_manifest(existing, detected)
        assert merged["description"] == "hand-written"

    def test_description_absent_when_not_in_existing(self):
        detected = {"name": "a/b", "type": "skills-pack", "version": "0.0.0", "homepage": None}
        merged = merge_manifest(None, detected)
        assert "description" not in merged

    def test_homepage_existing_wins(self):
        existing = {"homepage": "https://example.com"}
        detected = {"name": "a/b", "type": "skills-pack", "version": "0.0.0", "homepage": "https://github.com/a/b"}
        merged = merge_manifest(existing, detected)
        assert merged["homepage"] == "https://example.com"

    def test_none_fields_in_detected_omitted(self):
        detected = {"name": None, "type": "skills-pack", "version": "0.0.0", "homepage": None}
        merged = merge_manifest(None, detected)
        assert "name" not in merged
        assert "homepage" not in merged


class TestWriteManifest:
    def test_creates_file_when_absent(self, tmp_path):
        changed = write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        assert changed is True
        loaded = _json.loads((tmp_path / "cleo.json").read_text(encoding="utf-8"))
        assert loaded == {"name": "a/b", "type": "skills-pack"}

    def test_returns_false_when_identical(self, tmp_path):
        data = {"name": "a/b", "type": "skills-pack"}
        write_manifest(tmp_path, data)
        changed = write_manifest(tmp_path, data)
        assert changed is False

    def test_returns_true_when_field_added(self, tmp_path):
        write_manifest(tmp_path, {"name": "a/b"})
        changed = write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        assert changed is True

    def test_returns_true_when_value_changes(self, tmp_path):
        write_manifest(tmp_path, {"name": "a/b", "version": "1.0.0"})
        changed = write_manifest(tmp_path, {"name": "a/b", "version": "1.0.1"})
        assert changed is True

    def test_writes_trailing_newline(self, tmp_path):
        write_manifest(tmp_path, {"name": "a/b"})
        text = (tmp_path / "cleo.json").read_text(encoding="utf-8")
        assert text.endswith("\n")


from lib.publish import validate_publish


def _write_manifest(pkg_dir: Path, data: dict) -> None:
    (pkg_dir / "cleo.json").write_text(_json.dumps(data), encoding="utf-8")


def _write_rule(pkg_dir: Path, name: str = "r", description: str = "x") -> None:
    (pkg_dir / "rules").mkdir(exist_ok=True)
    (pkg_dir / "rules" / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n",
        encoding="utf-8",
    )


class TestValidatePublishSecurityGates:
    def test_clean_package_no_errors(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        _write_rule(tmp_path)
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert errors == []

    def test_bad_manifest_name_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "BAD NAME", "type": "skills-pack"})
        _write_rule(tmp_path)
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("vendor" in e.lower() or "name" in e.lower() for e in errors)

    def test_unknown_type_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "rules-pack"})
        _write_rule(tmp_path)
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("type" in e.lower() for e in errors)

    def test_empty_package_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("artifact" in e.lower() for e in errors)

    def test_oversized_hook_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "hooks").mkdir()
        (tmp_path / "hooks" / "PreToolUse.sh").write_bytes(b"#" * (64 * 1024 + 1))
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("hook" in e.lower() and ("limit" in e.lower() or "exceeds" in e.lower()) for e in errors)

    def test_bad_item_name_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "...md").write_text(
            "---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("reserved" in e.lower() or "item name" in e.lower() for e in errors)


class TestValidatePublishFrontmatter:
    def test_missing_name_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\ndescription: only desc\n---\nbody\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("name" in e.lower() and "r.md" in e for e in errors)

    def test_missing_description_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: r\n---\nbody\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("description" in e.lower() and "r.md" in e for e in errors)

    def test_unparseable_yaml_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text(
            "---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("yaml" in e.lower() or "frontmatter" in e.lower() for e in errors)

    def test_missing_frontmatter_block_reported(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r.md").write_text("plain body, no frontmatter\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("frontmatter" in e.lower() for e in errors)

    def test_skill_frontmatter_checked(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "skills" / "s").mkdir(parents=True)
        (tmp_path / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\n---\nbody\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert any("description" in e.lower() and "SKILL.md" in e for e in errors)

    def test_hooks_skipped_no_frontmatter_required(self, tmp_path):
        _write_manifest(tmp_path, {"name": "a/b", "type": "skills-pack"})
        (tmp_path / "hooks").mkdir()
        (tmp_path / "hooks" / "PreToolUse.sh").write_text(
            "#!/bin/sh\necho hi\n", encoding="utf-8")
        errors = validate_publish(tmp_path, skip_dry_install=True)
        assert errors == []
