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


from lib.security import validate_package_ref


class TestValidatePackageRef:
    def test_valid_ref(self):
        validate_package_ref("vendor/pkg")
        validate_package_ref("v/p")
        validate_package_ref("my-vendor/my.pkg_name")

    def test_rejects_empty(self):
        with pytest.raises(SecurityViolation, match="empty"):
            validate_package_ref("")

    def test_rejects_no_slash(self):
        with pytest.raises(SecurityViolation, match="<vendor>/<name>"):
            validate_package_ref("novendor")

    def test_rejects_path_traversal(self):
        with pytest.raises(SecurityViolation, match="<vendor>/<name>"):
            validate_package_ref("../../etc/passwd")
        with pytest.raises(SecurityViolation, match="<vendor>/<name>"):
            validate_package_ref("v/../../tmp/evil")

    def test_rejects_leading_dash(self):
        with pytest.raises(SecurityViolation, match="<vendor>/<name>"):
            validate_package_ref("-evil/p")

    def test_rejects_uppercase(self):
        with pytest.raises(SecurityViolation, match="<vendor>/<name>"):
            validate_package_ref("Vendor/Pkg")

    def test_rejects_extra_slash(self):
        with pytest.raises(SecurityViolation, match="<vendor>/<name>"):
            validate_package_ref("v/p/extra")


from lib.security import validate_git_ref


class TestValidateGitRef:
    def test_valid_tag(self):
        validate_git_ref("v1.2.3")
        validate_git_ref("1.0.0")
        validate_git_ref("release-2024-01")

    def test_valid_url(self):
        validate_git_ref("https://github.com/v/p")
        validate_git_ref("git@github.com:v/p.git")
        validate_git_ref("file:///tmp/repo")

    def test_rejects_empty(self):
        with pytest.raises(SecurityViolation, match="empty"):
            validate_git_ref("")

    def test_rejects_leading_dash(self):
        with pytest.raises(SecurityViolation, match="leading"):
            validate_git_ref("-evil-tag")
        with pytest.raises(SecurityViolation, match="leading"):
            validate_git_ref("--upload-pack=cmd")

    def test_rejects_null_byte(self):
        with pytest.raises(SecurityViolation, match="null byte"):
            validate_git_ref("v1.0\x00.0")

    def test_rejects_newline(self):
        with pytest.raises(SecurityViolation, match="newline"):
            validate_git_ref("v1.0\nmalicious")


import stat as _stat
from lib.security import validate_manifest_file_not_symlink


class TestValidateManifestFileNotSymlink:
    def test_regular_file_ok(self, tmp_path):
        p = tmp_path / "cleo.json"
        p.write_text("{}")
        validate_manifest_file_not_symlink(p)

    def test_missing_file_ok(self, tmp_path):
        # Caller is expected to handle absence separately; the validator
        # only complains about the symlink case.
        validate_manifest_file_not_symlink(tmp_path / "missing.json")

    @pytest.mark.skipif(sys.platform == "win32",
                         reason="symlink creation needs admin on Windows")
    def test_symlink_rejected(self, tmp_path):
        target = tmp_path / "real.json"
        target.write_text("{}")
        link = tmp_path / "cleo.json"
        link.symlink_to(target)
        with pytest.raises(SecurityViolation, match="symlink"):
            validate_manifest_file_not_symlink(link)


class TestValidateItemSourceFileType:
    def test_regular_file_still_ok(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        item = cache / "f.md"
        item.write_text("body")
        validate_item_source(item, cache)

    def test_directory_still_ok(self, tmp_path):
        cache = tmp_path / "cache"
        sub = cache / "sub"
        sub.mkdir(parents=True)
        validate_item_source(sub, cache)

    @pytest.mark.skipif(sys.platform == "win32",
                         reason="FIFO not supported on Windows")
    def test_fifo_rejected(self, tmp_path):
        import os as _os
        cache = tmp_path / "cache"
        cache.mkdir()
        fifo = cache / "f.md"
        _os.mkfifo(fifo)
        with pytest.raises(SecurityViolation, match="not a regular file"):
            validate_item_source(fifo, cache)
