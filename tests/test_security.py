"""Unit tests for tools/lib/security.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.security import SecurityViolation  # noqa: E402


def test_security_violation_is_exception():
    exc = SecurityViolation("test reason")
    assert isinstance(exc, Exception)
    assert str(exc) == "test reason"


from lib.security import validate_package_manifest


class TestValidatePackageManifest:
    def test_none_manifest_is_valid(self):
        # No cleo.json in the package — allowed (defaults apply).
        validate_package_manifest(None, "v/p")

    def test_minimal_manifest_is_valid(self):
        validate_package_manifest({"type": "skills-pack"}, "v/p")

    def test_full_valid_manifest(self):
        validate_package_manifest(
            {
                "name": "vendor/pkg",
                "type": "mixed",
                "version": "1.2.3",
                "description": "desc",
            },
            "vendor/pkg",
        )


class TestValidatePackageManifestRejections:
    def test_rejects_non_dict_manifest(self):
        with pytest.raises(SecurityViolation, match="must be a JSON object"):
            validate_package_manifest([1, 2, 3], "v/p")

    def test_rejects_unknown_type(self):
        with pytest.raises(SecurityViolation, match="unknown type"):
            validate_package_manifest({"type": "rules-pack"}, "v/p")

    def test_rejects_non_string_name(self):
        with pytest.raises(SecurityViolation, match="must match"):
            validate_package_manifest({"name": 123, "type": "skills-pack"}, "v/p")

    def test_rejects_name_without_slash(self):
        with pytest.raises(SecurityViolation, match="must match"):
            validate_package_manifest(
                {"name": "no-vendor", "type": "skills-pack"}, "v/p"
            )

    def test_rejects_name_with_path_chars(self):
        with pytest.raises(SecurityViolation, match="must match"):
            validate_package_manifest(
                {"name": "../../etc/passwd", "type": "skills-pack"}, "v/p"
            )


from lib.security import validate_dest_item_name


class TestValidateDestItemName:
    def test_valid_kebab_name(self):
        validate_dest_item_name("my-skill")
        validate_dest_item_name("rule_name")
        validate_dest_item_name("agent.v2")

    def test_rejects_empty(self):
        with pytest.raises(SecurityViolation, match="empty"):
            validate_dest_item_name("")

    def test_rejects_path_traversal(self):
        with pytest.raises(SecurityViolation, match="path separator"):
            validate_dest_item_name("../evil")
        with pytest.raises(SecurityViolation, match="path separator"):
            validate_dest_item_name("evil/sub")
        with pytest.raises(SecurityViolation, match="path separator"):
            validate_dest_item_name("evil\\sub")

    def test_rejects_dot_names(self):
        with pytest.raises(SecurityViolation, match="reserved"):
            validate_dest_item_name(".")
        with pytest.raises(SecurityViolation, match="reserved"):
            validate_dest_item_name("..")

    def test_rejects_null_byte(self):
        with pytest.raises(SecurityViolation, match="null byte"):
            validate_dest_item_name("evil\x00name")


from lib.security import validate_item_source


class TestValidateItemSource:
    def test_normal_path_inside_cache(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        item = cache / "skills" / "s" / "SKILL.md"
        item.parent.mkdir(parents=True)
        item.write_text("body")
        validate_item_source(item, cache)

    def test_directory_source_inside_cache(self, tmp_path):
        cache = tmp_path / "cache"
        skill_dir = cache / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("body")
        validate_item_source(skill_dir, cache)

    @pytest.mark.skipif(sys.platform == "win32",
                         reason="symlink creation needs admin on Windows")
    def test_rejects_symlink_escaping_cache(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "evil.md").write_text("payload")
        link = cache / "skills" / "evil"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        with pytest.raises(SecurityViolation, match="outside the package"):
            validate_item_source(link, cache)

    def test_rejects_source_outside_cache_via_relative(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        outside_file = tmp_path / "outside.md"
        outside_file.write_text("payload")
        with pytest.raises(SecurityViolation, match="outside the package"):
            validate_item_source(outside_file, cache)


from lib.security import validate_hook_size, HOOK_SIZE_MAX_BYTES


class TestValidateHookSize:
    def test_small_hook_ok(self, tmp_path):
        hook = tmp_path / "h.sh"
        hook.write_text("#!/bin/sh\necho hi\n")
        validate_hook_size(hook)

    def test_at_limit_ok(self, tmp_path):
        hook = tmp_path / "h.sh"
        hook.write_bytes(b"#" * HOOK_SIZE_MAX_BYTES)
        validate_hook_size(hook)

    def test_over_limit_rejected(self, tmp_path):
        hook = tmp_path / "h.sh"
        hook.write_bytes(b"#" * (HOOK_SIZE_MAX_BYTES + 1))
        with pytest.raises(SecurityViolation, match="exceeds"):
            validate_hook_size(hook)


from lib.security import validate_package_has_artifacts


class TestValidatePackageHasArtifacts:
    def test_skills_pack_with_items_ok(self):
        validate_package_has_artifacts(items_count=3, has_mcp_json=False, pkg_type="skills-pack")

    def test_skills_pack_empty_rejected(self):
        with pytest.raises(SecurityViolation, match="no recognized artifacts"):
            validate_package_has_artifacts(items_count=0, has_mcp_json=False, pkg_type="skills-pack")

    def test_mcp_server_with_mcp_json_ok(self):
        validate_package_has_artifacts(items_count=0, has_mcp_json=True, pkg_type="mcp-server")

    def test_mcp_server_without_mcp_json_rejected(self):
        with pytest.raises(SecurityViolation, match="ships no mcp.json"):
            validate_package_has_artifacts(items_count=0, has_mcp_json=False, pkg_type="mcp-server")

    def test_mixed_with_items_only_ok(self):
        validate_package_has_artifacts(items_count=2, has_mcp_json=False, pkg_type="mixed")

    def test_mixed_with_mcp_only_ok(self):
        validate_package_has_artifacts(items_count=0, has_mcp_json=True, pkg_type="mixed")

    def test_mixed_with_both_ok(self):
        validate_package_has_artifacts(items_count=2, has_mcp_json=True, pkg_type="mixed")

    def test_mixed_with_nothing_rejected(self):
        with pytest.raises(SecurityViolation, match="neither artifacts nor mcp.json"):
            validate_package_has_artifacts(items_count=0, has_mcp_json=False, pkg_type="mixed")
