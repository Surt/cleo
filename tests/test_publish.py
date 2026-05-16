"""Tests for tools/lib/publish.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from lib.publish import bump_version


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
