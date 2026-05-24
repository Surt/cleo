"""Tests for tools/lib/resolver.py — dependency resolution and parallel fetch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from lib.resolver import (
    DependencyCycle,
    ResolvedPackage,
    VersionConflict,
    _toposort,
    _constraints_compatible,
    _merge_constraints,
    resolve_all,
)

CLEO = str(Path(__file__).resolve().parent.parent / "tools" / "cleo.py")


@pytest.fixture(autouse=True)
def isolated_cleo_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "fake-home"))


def _git(cwd: Path, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def file_url(path: Path) -> str:
    return "file:///" + str(path).replace("\\", "/")


def make_pkg(pkg_root: Path, name: str, version: str, *,
             pkg_type: str = "bundle", with_rule: bool = True,
             requires: dict[str, str] | None = None,
             repositories: list[dict] | None = None) -> None:
    """Create a fake cleo package git repo with optional dependencies."""
    pkg_root.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": name, "type": pkg_type, "version": version}
    if requires:
        manifest["require"] = requires
    if repositories:
        manifest["repositories"] = repositories
    (pkg_root / "cleo.json").write_text(json.dumps(manifest), encoding="utf-8")
    if with_rule:
        (pkg_root / "rules").mkdir(exist_ok=True)
        (pkg_root / "rules" / "hello.md").write_text(
            f"---\nname: hello\ndescription: test rule from {name}\n---\n\nbody\n",
            encoding="utf-8",
        )
    _git(pkg_root, "init", "-q", "-b", "main")
    _git(pkg_root, "config", "user.email", "t@t.t")
    _git(pkg_root, "config", "user.name", "t")
    _git(pkg_root, "add", "-A")
    _git(pkg_root, "commit", "-qm", version)
    _git(pkg_root, "tag", f"v{version}")


def run_cleo(*args, cwd=None, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, CLEO, *args],
        cwd=cwd or os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ---- Unit tests for toposort ------------------------------------------

class TestToposort:
    def test_empty_graph(self):
        assert _toposort({}) == []

    def test_single_node(self):
        assert _toposort({"a": set()}) == ["a"]

    def test_linear_chain(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        result = _toposort(graph)
        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")

    def test_diamond(self):
        graph = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}
        result = _toposort(graph)
        assert result.index("a") < result.index("b")
        assert result.index("a") < result.index("c")
        assert result.index("b") < result.index("d")
        assert result.index("c") < result.index("d")

    def test_cycle_raises(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        with pytest.raises(DependencyCycle, match="cycle"):
            _toposort(graph)

    def test_self_cycle_raises(self):
        graph = {"a": {"a"}}
        with pytest.raises(DependencyCycle, match="cycle"):
            _toposort(graph)


# ---- Unit tests for constraint compatibility ---------------------------

class TestConstraintCompatibility:
    def test_wildcard(self):
        assert _constraints_compatible(["*"]) is True

    def test_single(self):
        assert _constraints_compatible(["^1.0"]) is True

    def test_compatible_caret(self):
        assert _constraints_compatible(["^1.0", "^1.2"]) is True

    def test_incompatible(self):
        assert _constraints_compatible(["^1.0", "^2.0"]) is False

    def test_tilde_compatible(self):
        assert _constraints_compatible(["~1.2.0", "~1.2.3"]) is True

    def test_tilde_incompatible(self):
        assert _constraints_compatible(["~1.2.0", "~1.3.0"]) is False


# ---- Unit tests for merge_constraints ----------------------------------

class TestMergeConstraints:
    def test_both_wildcard(self):
        assert _merge_constraints("*", "*") == "*"

    def test_first_wildcard(self):
        assert _merge_constraints("*", "^1.0") == "^1.0"

    def test_second_wildcard(self):
        assert _merge_constraints("^1.0", "*") == "^1.0"

    def test_same(self):
        assert _merge_constraints("^1.0", "^1.0") == "^1.0"

    def test_different_merged(self):
        assert _merge_constraints("^1.0", ">=1.2") == "^1.0 >=1.2"


# ---- Integration tests with fake packages -----------------------------

class TestTransitiveDeps:
    def test_simple_transitive(self, tmp_path):
        """A requires B → both should be installed."""
        pkg_b = tmp_path / "repos" / "v" / "b"
        make_pkg(pkg_b, "v/b", "1.0.0")

        pkg_a = tmp_path / "repos" / "v" / "a"
        make_pkg(pkg_a, "v/a", "1.0.0",
                 requires={"v/b": "^1.0"},
                 repositories=[{"type": "git", "url": file_url(pkg_b)}])

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/a", "-c", "^1.0",
                     "--repo", file_url(pkg_a), "--project", str(proj))
        assert r.returncode == 0, r.stderr

        # Both packages should be in the lock.
        lock_data = json.loads((proj / "cleo.lock").read_text(encoding="utf-8"))
        pkgs = lock_data.get("packages", {})
        assert "v/a" in pkgs
        assert "v/b" in pkgs
        # B should record that A required it.
        assert "v/a" in pkgs["v/b"].get("required_by", [])

    def test_diamond_dependency(self, tmp_path):
        """A→B, A→C, B→D, C→D — D installed once."""
        pkg_d = tmp_path / "repos" / "v" / "d"
        make_pkg(pkg_d, "v/d", "1.0.0")

        pkg_b = tmp_path / "repos" / "v" / "b"
        make_pkg(pkg_b, "v/b", "1.0.0",
                 requires={"v/d": "^1.0"},
                 repositories=[{"type": "git", "url": file_url(pkg_d)}])

        pkg_c = tmp_path / "repos" / "v" / "c"
        make_pkg(pkg_c, "v/c", "1.0.0",
                 requires={"v/d": "^1.0"},
                 repositories=[{"type": "git", "url": file_url(pkg_d)}])

        pkg_a = tmp_path / "repos" / "v" / "a"
        make_pkg(pkg_a, "v/a", "1.0.0",
                 requires={"v/b": "^1.0", "v/c": "^1.0"},
                 repositories=[
                     {"type": "git", "url": file_url(pkg_b)},
                     {"type": "git", "url": file_url(pkg_c)},
                 ])

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/a", "-c", "^1.0",
                     "--repo", file_url(pkg_a), "--project", str(proj))
        assert r.returncode == 0, r.stderr

        lock_data = json.loads((proj / "cleo.lock").read_text(encoding="utf-8"))
        pkgs = lock_data.get("packages", {})
        assert "v/a" in pkgs
        assert "v/b" in pkgs
        assert "v/c" in pkgs
        assert "v/d" in pkgs

    def test_cycle_detected(self, tmp_path):
        """A→B, B→A should error with cycle message."""
        pkg_a = tmp_path / "repos" / "v" / "a"
        pkg_b = tmp_path / "repos" / "v" / "b"

        # B requires A
        make_pkg(pkg_b, "v/b", "1.0.0",
                 requires={"v/a": "^1.0"},
                 repositories=[{"type": "git", "url": file_url(pkg_a)}])

        # A requires B
        make_pkg(pkg_a, "v/a", "1.0.0",
                 requires={"v/b": "^1.0"},
                 repositories=[{"type": "git", "url": file_url(pkg_b)}])

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/a", "-c", "^1.0",
                     "--repo", file_url(pkg_a), "--project", str(proj))
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "cycle" in combined.lower()

    def test_no_deps_still_works(self, tmp_path):
        """Package without require field installs normally."""
        pkg = tmp_path / "pkg"
        make_pkg(pkg, "v/p", "1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        assert r.returncode == 0, r.stderr


class TestParallelFetch:
    def test_parallel_install(self, tmp_path):
        """Install with --jobs 2 should work correctly."""
        pkg_a = tmp_path / "repos" / "v" / "a"
        make_pkg(pkg_a, "v/a", "1.0.0")
        pkg_b = tmp_path / "repos" / "v" / "b"
        make_pkg(pkg_b, "v/b", "1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        manifest = {
            "name": "test",
            "repositories": [
                {"type": "git", "url": file_url(pkg_a)},
                {"type": "git", "url": file_url(pkg_b)},
            ],
            "require": {"v/a": "^1.0", "v/b": "^1.0"},
        }
        (proj / "cleo.json").write_text(json.dumps(manifest), encoding="utf-8")

        r = run_cleo("install", "--jobs", "2", "--project", str(proj))
        assert r.returncode == 0, r.stderr

        lock_data = json.loads((proj / "cleo.lock").read_text(encoding="utf-8"))
        pkgs = lock_data.get("packages", {})
        assert "v/a" in pkgs
        assert "v/b" in pkgs


class TestOrphanRemoval:
    def test_remove_cleans_orphans(self, tmp_path):
        """Removing A should also remove A's transitive dep B if B is orphaned."""
        pkg_b = tmp_path / "repos" / "v" / "b"
        make_pkg(pkg_b, "v/b", "1.0.0")

        pkg_a = tmp_path / "repos" / "v" / "a"
        make_pkg(pkg_a, "v/a", "1.0.0",
                 requires={"v/b": "^1.0"},
                 repositories=[{"type": "git", "url": file_url(pkg_b)}])

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/a", "-c", "^1.0",
                     "--repo", file_url(pkg_a), "--project", str(proj))
        assert r.returncode == 0, r.stderr

        # Verify both installed.
        lock_data = json.loads((proj / "cleo.lock").read_text(encoding="utf-8"))
        assert "v/a" in lock_data["packages"]
        assert "v/b" in lock_data["packages"]

        # Remove A → B should be orphaned and removed.
        r = run_cleo("remove", "v/a", "--project", str(proj))
        assert r.returncode == 0, r.stderr

        lock_data = json.loads((proj / "cleo.lock").read_text(encoding="utf-8"))
        assert "v/a" not in lock_data["packages"]
        assert "v/b" not in lock_data["packages"]
