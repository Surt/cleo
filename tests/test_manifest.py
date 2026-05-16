"""Tests for cleo.py manifest + lock I/O functions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# Import only pure functions — avoid triggering argparse at module level
from cleo import (
    load_manifest,
    save_manifest,
    scaffold_manifest,
    _bucket_key,
    LockPackage,
    MANIFEST_FILE,
    LOCK_FILE,
    BUCKET_PROJECT,
    BUCKET_LOCAL,
    BUCKET_USER,
)


# ---- load_manifest ---------------------------------------------------------

class TestLoadManifest:
    def test_loads_valid(self, tmp_path):
        data = {"name": "test", "require": {}}
        (tmp_path / MANIFEST_FILE).write_text(json.dumps(data), encoding="utf-8")
        loaded = load_manifest(tmp_path)
        assert loaded["name"] == "test"

    def test_raises_if_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            load_manifest(tmp_path)

    def test_raises_on_invalid_json(self, tmp_path):
        (tmp_path / MANIFEST_FILE).write_text("{bad json", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_manifest(tmp_path)

    def test_raises_on_non_object(self, tmp_path):
        (tmp_path / MANIFEST_FILE).write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_manifest(tmp_path)


# ---- save_manifest ---------------------------------------------------------

class TestSaveManifest:
    def test_roundtrip(self, tmp_path):
        data = {"name": "proj", "require": {"foo/bar": "^1.0"}}
        save_manifest(tmp_path, data)
        loaded = load_manifest(tmp_path)
        assert loaded == data

    def test_writes_valid_json(self, tmp_path):
        save_manifest(tmp_path, {"name": "x"})
        raw = (tmp_path / MANIFEST_FILE).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["name"] == "x"

    def test_atomic_write(self, tmp_path):
        save_manifest(tmp_path, {"name": "first"})
        save_manifest(tmp_path, {"name": "second"})
        loaded = load_manifest(tmp_path)
        assert loaded["name"] == "second"


# ---- scaffold_manifest -----------------------------------------------------

class TestScaffoldManifest:
    def test_creates_file(self, tmp_path):
        target = tmp_path / "newproject"
        scaffold_manifest(target)
        assert (target / MANIFEST_FILE).exists()

    def test_default_structure(self, tmp_path):
        data = scaffold_manifest(tmp_path)
        assert "require" in data
        assert "require-local" in data
        assert "require-user" in data
        assert "repositories" in data

    def test_name_matches_dirname(self, tmp_path):
        target = tmp_path / "my-project"
        data = scaffold_manifest(target)
        assert data["name"] == "my-project"

    def test_creates_nested_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        scaffold_manifest(target)
        assert (target / MANIFEST_FILE).exists()


# ---- _bucket_key -----------------------------------------------------------

class TestBucketKey:
    def test_project(self):
        assert _bucket_key(BUCKET_PROJECT) == "require"

    def test_local(self):
        assert _bucket_key(BUCKET_LOCAL) == "require-local"

    def test_user(self):
        assert _bucket_key(BUCKET_USER) == "require-user"


# ---- LockPackage.install_mode ----------------------------------------------

class TestLockPackageInstallMode:
    def test_serializes_install_mode_default(self):
        pkg = LockPackage(
            name="foo/bar", pkg_type="skills-pack", url="https://github.com/foo/bar",
            version="1.0.0", commit="abc123", bucket="project",
        )
        d = pkg.to_dict()
        assert d["install_mode"] == "copy"

    def test_serializes_install_mode_symlink(self):
        pkg = LockPackage(
            name="foo/bar", pkg_type="skills-pack", url="https://github.com/foo/bar",
            version="1.0.0", commit="abc123", bucket="project",
            install_mode="symlink",
        )
        d = pkg.to_dict()
        assert d["install_mode"] == "symlink"

    def test_roundtrips_install_mode(self):
        src = LockPackage(
            name="foo/bar", pkg_type="skills-pack", url="https://github.com/foo/bar",
            version="1.0.0", commit="abc123", bucket="project",
            install_mode="symlink",
        )
        restored = LockPackage.from_dict("foo/bar", src.to_dict())
        assert restored.install_mode == "symlink"

    def test_reads_legacy_lock_without_install_mode(self):
        legacy = {
            "type": "skills-pack",
            "url": "https://github.com/foo/bar",
            "version": "1.0.0",
            "commit": "abc123",
            "bucket": "project",
            "items": [],
        }
        pkg = LockPackage.from_dict("foo/bar", legacy)
        assert pkg.install_mode == "copy"
