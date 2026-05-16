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
