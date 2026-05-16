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
