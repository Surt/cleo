"""Tests for tools/lib/checks.py — frontmatter parsing and package discovery."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.checks import (
    parse_frontmatter,
    split_frontmatter_and_body,
    discover_items,
    find_vague_directives,
    find_leaks,
    jaccard_similarity,
    jaccard_words,
    DEDUPE_THRESHOLD,
)


# ---- parse_frontmatter -----------------------------------------------------

class TestParseFrontmatter:
    def test_valid(self):
        text = "---\nname: test-rule\ndescription: A rule\n---\nBody text.\n"
        data, err = parse_frontmatter(text)
        assert err is None
        assert data is not None
        assert data["name"] == "test-rule"

    def test_missing_delimiter(self):
        data, err = parse_frontmatter("No frontmatter here.\n")
        assert data is None
        assert err is not None
        assert "missing" in err.lower()

    def test_not_terminated(self):
        text = "---\nname: test\n"
        data, err = parse_frontmatter(text)
        assert data is None
        assert "terminated" in err.lower() or "not terminated" in err.lower()

    def test_invalid_yaml(self):
        text = "---\nname: [unclosed\n---\nBody\n"
        data, err = parse_frontmatter(text)
        assert data is None
        assert "invalid" in err.lower() or "yaml" in err.lower()

    def test_empty_frontmatter(self):
        text = "---\n---\nBody\n"
        data, err = parse_frontmatter(text)
        assert data is None
        assert err is not None

    def test_crlf_line_endings(self):
        text = "---\r\nname: rule\r\n---\r\nBody\r\n"
        data, err = parse_frontmatter(text)
        assert err is None
        assert data["name"] == "rule"

    def test_from_path(self, tmp_path):
        f = tmp_path / "rule.md"
        f.write_text("---\nname: path-test\n---\nBody\n", encoding="utf-8")
        data, err = parse_frontmatter(f)
        assert err is None
        assert data["name"] == "path-test"


# ---- split_frontmatter_and_body --------------------------------------------

class TestSplitFrontmatterAndBody:
    def test_splits_correctly(self):
        text = "---\nname: foo\n---\nHello world\n"
        fm, body = split_frontmatter_and_body(text)
        assert "name: foo" in fm
        assert "Hello world" in body

    def test_no_frontmatter(self):
        text = "Just body content.\n"
        fm, body = split_frontmatter_and_body(text)
        assert fm == ""
        assert body == text


# ---- discover_items --------------------------------------------------------

class TestDiscoverItems:
    def _make_rule(self, pkg: Path, name: str) -> Path:
        d = pkg / "rules"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}.md"
        f.write_text(f"---\nname: {name}\n---\nBody\n")
        return f

    def _make_skill(self, pkg: Path, name: str) -> Path:
        d = pkg / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        f = d / "SKILL.md"
        f.write_text(f"---\nname: {name}\n---\nBody\n")
        return f

    def _make_hook(self, pkg: Path, name: str) -> Path:
        d = pkg / "hooks"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}.sh"
        f.write_text("#!/bin/sh\necho hi\n")
        return f

    def test_finds_rule(self, tmp_path):
        self._make_rule(tmp_path, "my-rule")
        items = discover_items(tmp_path)
        types = [t for t, _, _ in items]
        names = [n for _, n, _ in items]
        assert "rule" in types
        assert "my-rule" in names

    def test_finds_skill(self, tmp_path):
        self._make_skill(tmp_path, "my-skill")
        items = discover_items(tmp_path)
        assert any(t == "skill" and n == "my-skill" for t, n, _ in items)

    def test_finds_hook(self, tmp_path):
        self._make_hook(tmp_path, "post-save")
        items = discover_items(tmp_path)
        assert any(t == "hook" and n == "post-save" for t, n, _ in items)

    def test_empty_package(self, tmp_path):
        assert discover_items(tmp_path) == []

    def test_multiple_types(self, tmp_path):
        self._make_rule(tmp_path, "rule-a")
        self._make_rule(tmp_path, "rule-b")
        self._make_skill(tmp_path, "skill-a")
        items = discover_items(tmp_path)
        assert len(items) == 3


# ---- find_vague_directives -------------------------------------------------

class TestFindVagueDirectives:
    def test_detects_be_careful(self):
        hits = find_vague_directives("Please be careful with user data.")
        assert len(hits) > 0
        assert any("be careful" in h[0].lower() for h in hits)

    def test_detects_clean_code(self):
        hits = find_vague_directives("Always write clean code.")
        assert len(hits) > 0

    def test_no_false_positive(self):
        hits = find_vague_directives("Use strict TypeScript types and explicit error handling.")
        assert len(hits) == 0

    def test_try_to_flagged(self):
        hits = find_vague_directives("Try to avoid unnecessary imports.")
        assert len(hits) > 0


# ---- find_leaks ------------------------------------------------------------

class TestFindLeaks:
    def test_detects_email(self):
        leaks = find_leaks("Contact user@example.com for support.")
        assert "email" in leaks

    def test_detects_ticket(self):
        leaks = find_leaks("See PROJ-123 for context.")
        assert "ticket-prefix" in leaks

    def test_detects_linux_home(self):
        leaks = find_leaks("Path is /home/jdoe/.config/app")
        assert "home-path-linux" in leaks

    def test_detects_windows_home(self):
        leaks = find_leaks(r"File at C:\Users\jdoe\Documents\file.txt")
        assert "home-path-windows" in leaks

    def test_ignores_code_blocks(self):
        text = "```\nuser@example.com\n```"
        leaks = find_leaks(text)
        assert "email" not in leaks

    def test_clean_text(self):
        leaks = find_leaks("This rule applies to all API calls that return 200 OK.")
        assert leaks == {}


# ---- jaccard_similarity ----------------------------------------------------

class TestJaccardSimilarity:
    def test_identical(self):
        words = jaccard_words("always use strict types everywhere")
        assert jaccard_similarity(words, words) == 1.0

    def test_disjoint(self):
        a = jaccard_words("alpha beta gamma delta")
        b = jaccard_words("zeta omega kappa sigma")
        assert jaccard_similarity(a, b) == 0.0

    def test_partial_overlap(self):
        a = jaccard_words("use strict types always in code")
        b = jaccard_words("use strict typing please in tests")
        sim = jaccard_similarity(a, b)
        assert 0.0 < sim < 1.0

    def test_both_empty(self):
        assert jaccard_similarity(set(), set()) == 0.0

    def test_threshold_constant(self):
        assert 0.0 < DEDUPE_THRESHOLD <= 1.0
