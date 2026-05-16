"""Tests for tools/lib/semver.py — version parsing and constraint resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.semver import (
    Version,
    parse_version,
    matches_constraint,
    parse_constraint,
    fetch_tags,
    resolve_version,
    resolve_commit,
)


# ---- parse_version ---------------------------------------------------------

class TestParseVersion:
    def test_basic(self):
        v = parse_version("1.2.3")
        assert v == Version(1, 2, 3)

    def test_v_prefix(self):
        assert parse_version("v1.2.3") == Version(1, 2, 3)

    def test_no_patch(self):
        v = parse_version("1.2")
        assert v == Version(1, 2, 0)

    def test_pre_release(self):
        v = parse_version("1.0.0-alpha.1")
        assert v is not None
        assert v.pre == "alpha.1"

    def test_invalid_returns_none(self):
        assert parse_version("not-a-version") is None
        assert parse_version("") is None
        assert parse_version("abc") is None

    def test_zero_zero(self):
        assert parse_version("0.0.1") == Version(0, 0, 1)


# ---- Version ordering ------------------------------------------------------

class TestVersionOrdering:
    def test_major_wins(self):
        assert Version(2, 0, 0) > Version(1, 9, 9)

    def test_minor_wins(self):
        assert Version(1, 2, 0) > Version(1, 1, 9)

    def test_patch_wins(self):
        assert Version(1, 0, 2) > Version(1, 0, 1)

    def test_pre_lower_than_release(self):
        release = parse_version("1.0.0")
        pre = parse_version("1.0.0-alpha")
        assert release > pre

    def test_equal(self):
        assert Version(1, 2, 3) == Version(1, 2, 3)


# ---- matches_constraint ----------------------------------------------------

class TestMatchesConstraint:
    def _v(self, s: str) -> Version:
        v = parse_version(s)
        assert v is not None
        return v

    # wildcard
    def test_wildcard(self):
        assert matches_constraint(self._v("99.0.0"), "*") is True

    def test_empty_constraint(self):
        assert matches_constraint(self._v("1.0.0"), "") is True

    # exact
    def test_exact_match(self):
        assert matches_constraint(self._v("1.2.3"), "1.2.3") is True

    def test_exact_no_match(self):
        assert matches_constraint(self._v("1.2.4"), "1.2.3") is False

    # caret (^)
    def test_caret_major_compat(self):
        assert matches_constraint(self._v("1.9.9"), "^1.0.0") is True

    def test_caret_major_boundary(self):
        assert matches_constraint(self._v("2.0.0"), "^1.0.0") is False

    def test_caret_zero_major(self):
        assert matches_constraint(self._v("0.2.5"), "^0.2.3") is True
        assert matches_constraint(self._v("0.3.0"), "^0.2.3") is False

    def test_caret_zero_zero(self):
        assert matches_constraint(self._v("0.0.3"), "^0.0.3") is True
        assert matches_constraint(self._v("0.0.4"), "^0.0.3") is False

    # tilde (~)
    def test_tilde_patch_range(self):
        assert matches_constraint(self._v("1.2.9"), "~1.2.3") is True
        assert matches_constraint(self._v("1.3.0"), "~1.2.3") is False

    # comparison operators
    def test_gte(self):
        assert matches_constraint(self._v("2.0.0"), ">=1.0.0") is True
        assert matches_constraint(self._v("1.0.0"), ">=1.0.0") is True
        assert matches_constraint(self._v("0.9.9"), ">=1.0.0") is False

    def test_lte(self):
        assert matches_constraint(self._v("0.9.9"), "<=1.0.0") is True
        assert matches_constraint(self._v("1.0.0"), "<=1.0.0") is True
        assert matches_constraint(self._v("1.0.1"), "<=1.0.0") is False

    def test_gt(self):
        assert matches_constraint(self._v("1.0.1"), ">1.0.0") is True
        assert matches_constraint(self._v("1.0.0"), ">1.0.0") is False

    def test_lt(self):
        assert matches_constraint(self._v("0.9.9"), "<1.0.0") is True
        assert matches_constraint(self._v("1.0.0"), "<1.0.0") is False

    # range (AND)
    def test_range(self):
        assert matches_constraint(self._v("1.5.0"), ">=1.0.0 <2.0.0") is True
        assert matches_constraint(self._v("2.0.0"), ">=1.0.0 <2.0.0") is False
        assert matches_constraint(self._v("0.9.9"), ">=1.0.0 <2.0.0") is False

    # pre-release: excluded from operator constraints, included in wildcard
    def test_pre_release_excluded_from_operators(self):
        pre = parse_version("1.0.0-alpha")
        assert matches_constraint(pre, "^1.0.0") is False
        assert matches_constraint(pre, ">=1.0.0") is False
        assert matches_constraint(pre, "~1.0.0") is False

    def test_pre_release_matched_by_wildcard(self):
        pre = parse_version("1.0.0-alpha")
        assert matches_constraint(pre, "*") is True


# ---- fetch_tags (subprocess mock) ------------------------------------------

class TestFetchTags:
    def test_parses_refs(self):
        fake_output = (
            "abc123\trefs/tags/v1.0.0\n"
            "def456\trefs/tags/v1.0.0^{}\n"
            "789abc\trefs/tags/v2.0.0\n"
            "000000\trefs/heads/main\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = fake_output
            tags = fetch_tags("https://example.com/repo")
        assert "v1.0.0" in tags
        assert "v2.0.0" in tags
        assert "v1.0.0^{}" not in tags  # peeled stripped
        assert len(tags) == 2

    def test_returns_empty_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            assert fetch_tags("https://example.com/repo") == []

    def test_returns_empty_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            assert fetch_tags("https://example.com/repo") == []


# ---- resolve_version -------------------------------------------------------

class TestResolveVersion:
    def _mock_tags(self, tags: list[str]):
        fake_output = "\n".join(f"abc\trefs/tags/{t}" for t in tags) + "\n"

        def fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = fake_output
            return R()

        return patch("subprocess.run", side_effect=fake_run)

    def test_picks_highest_matching(self):
        with self._mock_tags(["v1.0.0", "v1.2.0", "v2.0.0"]):
            result = resolve_version("https://example.com", "^1.0.0")
        assert result is not None
        version_str, tag = result
        assert version_str == "1.2.0"
        assert tag == "v1.2.0"

    def test_returns_none_when_no_match(self):
        with self._mock_tags(["v1.0.0"]):
            result = resolve_version("https://example.com", "^2.0.0")
        assert result is None

    def test_offline_returns_none(self):
        result = resolve_version("https://example.com", "*", offline=True)
        assert result is None

    def test_ignores_non_semver_tags(self):
        with self._mock_tags(["v1.0.0", "latest", "release-1.2"]):
            result = resolve_version("https://example.com", "*")
        assert result is not None
        assert result[0] == "1.0.0"


# ---- resolve_commit --------------------------------------------------------

class TestResolveCommit:
    def test_prefers_peeled_ref(self):
        fake_output = (
            "aaaa\trefs/tags/v1.0.0\n"
            "bbbb\trefs/tags/v1.0.0^{}\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = fake_output
            sha = resolve_commit("https://example.com", "v1.0.0")
        assert sha == "bbbb"

    def test_falls_back_to_direct(self):
        fake_output = "aaaa\trefs/tags/v1.0.0\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = fake_output
            sha = resolve_commit("https://example.com", "v1.0.0")
        assert sha == "aaaa"

    def test_returns_none_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            assert resolve_commit("https://example.com", "v1.0.0") is None
