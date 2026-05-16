"""Subprocess-based regression tests for fixed CLI bugs.

One test per bug. These would have caught the issues found in pre-release
audit and prevent regressions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLEO = str(Path(__file__).resolve().parent.parent / "tools" / "cleo.py")


@pytest.fixture(autouse=True)
def isolated_cleo_home(tmp_path, monkeypatch):
    """Redirect ~/.claude/cleo/ to a per-test tmp dir so tests do not pollute
    the user's real cleo cache and so previous-test cache cannot bleed in."""
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "fake-home"))


def run_cleo(*args, cwd=None, env_extra=None):
    """Invoke cleo.py in a subprocess. Returns CompletedProcess."""
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


def make_pkg(pkg_root: Path, name: str, version: str, pkg_type: str = "skills-pack",
             with_rule: bool = True, with_mcp: bool = False) -> None:
    """Create a fake cleo package git repo with one tagged version."""
    pkg_root.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "type": pkg_type, "version": version}
    (pkg_root / "cleo.json").write_text(json.dumps(manifest), encoding="utf-8")
    if with_rule:
        (pkg_root / "rules").mkdir(exist_ok=True)
        (pkg_root / "rules" / "hello.md").write_text(
            "---\nname: hello\ndescription: test rule for cleo regression suite\n---\n\nbody\n",
            encoding="utf-8",
        )
    if with_mcp:
        (pkg_root / "mcp.json").write_text(
            json.dumps({"command": "echo", "args": ["hi"]}),
            encoding="utf-8",
        )
    _git(pkg_root, "init", "-q", "-b", "main")
    _git(pkg_root, "config", "user.email", "t@t.t")
    _git(pkg_root, "config", "user.name", "t")
    _git(pkg_root, "add", "-A")
    _git(pkg_root, "commit", "-qm", version)
    _git(pkg_root, "tag", f"v{version}")


def _git(cwd: Path, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def file_url(path: Path) -> str:
    return "file:///" + str(path).replace("\\", "/")


# ---- B3: --project flag works before AND after the subcommand ---------------

class TestProjectFlagPosition:
    def test_before_subcommand(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        result = run_cleo("--project", str(proj), "init")
        assert result.returncode == 0, result.stderr
        assert (proj / "cleo.json").exists()

    def test_after_subcommand(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        result = run_cleo("init", "--project", str(proj))
        assert result.returncode == 0, result.stderr
        assert (proj / "cleo.json").exists()

    def test_after_overrides_before(self, tmp_path):
        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        proj_a.mkdir(); proj_b.mkdir()
        result = run_cleo("--project", str(proj_a), "init", "--project", str(proj_b))
        assert result.returncode == 0
        assert (proj_b / "cleo.json").exists()
        assert not (proj_a / "cleo.json").exists()


# ---- B4: unknown package type errors out instead of silent no-op ------------

class TestUnknownPackageType:
    def test_rejects_unknown_type(self, tmp_path):
        pkg = tmp_path / "pkg"
        make_pkg(pkg, "v/p", "1.0.0", pkg_type="rules-pack")  # not a valid type
        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        result = run_cleo(
            "require", "v/p", "-c", "^1.0",
            "--repo", file_url(pkg), "--project", str(proj),
        )
        assert result.returncode != 0 or "unknown package type" in result.stdout + result.stderr
        # Manifest should NOT list the package (failed install)
        manifest = json.loads((proj / "cleo.json").read_text(encoding="utf-8"))
        assert "v/p" not in manifest.get("require", {})


# ---- B5: cleo check detects on-disk drift ----------------------------------

class TestCheckDetectsDrift:
    def test_modified_file_reported(self, tmp_path):
        pkg = tmp_path / "pkg"
        make_pkg(pkg, "v/p", "1.0.0", pkg_type="skills-pack")
        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        assert r.returncode == 0, r.stderr

        # Find the installed rule and tamper.
        installed = proj / ".claude" / "rules" / "cleo-v-p-hello.md"
        assert installed.exists(), "rule was not materialized"
        installed.write_text(installed.read_text() + "\n# tampered\n", encoding="utf-8")

        result = run_cleo("check", "--project", str(proj))
        assert result.returncode != 0, f"check should flag drift, got: {result.stdout}"
        assert "modified" in result.stdout.lower()


# ---- B6: cached commit mismatch triggers re-clone ---------------------------

class TestCacheCommitVerification:
    def test_cache_invalidated_on_commit_mismatch(self, tmp_path):
        # isolated_cleo_home fixture redirects ~/.claude under tmp_path.
        cache_home = tmp_path / "fake-home"

        pkg = tmp_path / "pkg"
        make_pkg(pkg, "v/p", "1.0.0", pkg_type="skills-pack")
        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        assert r.returncode == 0, r.stderr

        # Corrupt the cache: rewrite a file inside cached repo + amend commit.
        cache_dir = cache_home / ".claude" / "cleo" / "packages" / "v" / "p" / "1.0.0"
        assert cache_dir.exists()
        (cache_dir / "rules" / "hello.md").write_text(
            "---\nname: hello\ndescription: tampered cache content for cleo regression suite\n---\n\nTAMPERED\n",
            encoding="utf-8",
        )
        # Cached repo was created by `git clone`, which inherits no user
        # identity. CI runners (especially Linux) have no global identity
        # either, so set it locally on the cache repo before the tamper commit.
        _git(cache_dir, "config", "user.email", "t@t.t")
        _git(cache_dir, "config", "user.name", "t")
        _git(cache_dir, "add", "-A")
        _git(cache_dir, "commit", "-qm", "tamper")

        # Re-install (cache hit branch): cache HEAD now mismatches lock commit.
        # cleo should detect and re-clone from origin.
        # First, remove the materialized file so install re-materializes.
        installed = proj / ".claude" / "rules" / "cleo-v-p-hello.md"
        installed.unlink()
        r = run_cleo("install", "--project", str(proj))
        assert r.returncode == 0, r.stderr

        # Materialized file should be the ORIGINAL (from re-clone), not "TAMPERED".
        assert installed.exists()
        assert "TAMPERED" not in installed.read_text(encoding="utf-8"), \
            "cache was not re-cloned despite commit mismatch"


# ---- require-local: all artifact types nest under local/ + .gitignore ------

class TestLocalBucketGitignore:
    def test_local_install_gitignores_all_artifact_types(self, tmp_path):
        # Build a package with one of each artifact type.
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        for sub, fname in [("rules", "r.md"), ("agents", "a.md"), ("commands", "c.md")]:
            (pkg / sub).mkdir()
            (pkg / sub / fname).write_text(
                f"---\nname: x\ndescription: smoke test {sub} for cleo local regression\n---\n\nbody\n",
                encoding="utf-8",
            )
        (pkg / "skills" / "s").mkdir(parents=True)
        (pkg / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: smoke test skill for cleo local regression\n---\n\nbody\n",
            encoding="utf-8",
        )
        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--local", "--project", str(proj))
        assert r.returncode == 0, r.stderr

        # All artifact types should nest under their type's local/ subdir.
        for sub in ("rules", "agents", "commands"):
            local_dir = proj / ".claude" / sub / "local"
            assert local_dir.exists() and any(local_dir.iterdir()), \
                f"expected files under .claude/{sub}/local/, found nothing"
        skill_local = proj / ".claude" / "skills" / "local"
        assert skill_local.exists() and any(skill_local.iterdir()), \
            "expected skill directory under .claude/skills/local/"

        # .gitignore must list every local path.
        gi = (proj / ".gitignore").read_text(encoding="utf-8")
        for required in (
            ".claude/rules/local/",
            ".claude/skills/local/",
            ".claude/agents/local/",
            ".claude/commands/local/",
            "cleo.local.lock",
        ):
            assert required in gi, f".gitignore missing {required!r}"

    def test_stale_gitignore_block_is_refreshed(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        # Simulate an old cleo install: gitignore contains the marker block
        # with the old rules-only path.
        (proj / ".gitignore").write_text(
            "# cleo local — managed, do not edit\n"
            ".claude/rules/local/\n"
            "cleo.local.lock\n"
            "# /cleo local\n",
            encoding="utf-8",
        )

        # Build + require a local package — should refresh the block.
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (pkg / "rules").mkdir()
        (pkg / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: smoke test rule for stale gitignore refresh\n---\n\nbody\n",
            encoding="utf-8",
        )
        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--local", "--project", str(proj))
        assert r.returncode == 0, r.stderr

        gi = (proj / ".gitignore").read_text(encoding="utf-8")
        assert ".claude/skills/local/" in gi
        assert ".claude/agents/local/" in gi
        assert ".claude/commands/local/" in gi
        # Block markers should still be present exactly once.
        assert gi.count("# cleo local — managed, do not edit") == 1
        assert gi.count("# /cleo local") == 1


# ---- require-user: rejects packages containing hooks ----------------------

class TestUserBucketHookGuard:
    def test_hook_in_user_bucket_package_errors(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (pkg / "hooks").mkdir()
        (pkg / "hooks" / "PreToolUse.sh").write_text(
            "#!/usr/bin/env bash\necho hi\n", encoding="utf-8",
        )
        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        result = run_cleo("require", "v/p", "-c", "^1.0",
                          "--repo", file_url(pkg), "--user", "--project", str(proj))
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "user bucket does not support" in combined
        assert "hook" in combined
        # Manifest should NOT list the package after a rejected install.
        manifest = json.loads((proj / "cleo.json").read_text(encoding="utf-8"))
        assert "v/p" not in manifest.get("require-user", {})


# ---- B1: non-ASCII status output does not crash on Windows-style encoding ---

class TestUnicodeOutput:
    def test_init_does_not_crash_with_cp1252_locale(self, tmp_path):
        # Force cp1252 stdout: cleo's own reconfigure should override.
        proj = tmp_path / "proj"
        proj.mkdir()
        result = run_cleo(
            "init", "--project", str(proj),
            env_extra={"PYTHONIOENCODING": "cp1252:replace"},
        )
        # Even if individual chars get replaced, the process must not crash.
        assert result.returncode == 0, f"stderr={result.stderr}"


# ---- Security gate: malformed manifest is rejected loudly --------------------

class TestManifestSecurityGate:
    def test_malformed_cleo_json_in_package_is_rejected(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        # Invalid JSON in the package's cleo.json.
        (pkg / "cleo.json").write_text("{ not json ", encoding="utf-8")
        (pkg / "rules").mkdir()
        (pkg / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: smoke rule for malformed-manifest gate test\n---\n\nbody\n",
            encoding="utf-8",
        )
        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "cleo.json" in combined.lower() or "manifest" in combined.lower()
        # Package must NOT be added to manifest.
        manifest = json.loads((proj / "cleo.json").read_text(encoding="utf-8"))
        assert "v/p" not in manifest.get("require", {})


# ---- Security gate: symlink escape rejected ---------------------------------

@pytest.mark.skipif(sys.platform == "win32",
                     reason="symlink creation needs admin on Windows")
class TestSymlinkEscapeGate:
    def test_symlinked_skill_dir_pointing_outside_is_rejected(self, tmp_path):
        # Build a "decoy" external directory whose contents would be smuggled in.
        outside = tmp_path / "outside-target"
        outside.mkdir()
        (outside / "SKILL.md").write_text(
            "---\nname: evil\ndescription: external payload that must not be installed\n---\n\npayload\n",
            encoding="utf-8",
        )

        pkg = tmp_path / "pkg"
        (pkg / "skills").mkdir(parents=True)
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        # Symlink skills/evil -> ../outside-target.
        (pkg / "skills" / "evil").symlink_to(outside, target_is_directory=True)

        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        # Install must fail.
        assert r.returncode != 0
        # Project must not contain the smuggled skill.
        assert not (proj / ".claude" / "skills" / "cleo-v-p-evil").exists()


# ---- Security gate: oversized hooks rejected --------------------------------

class TestHookSizeGate:
    def test_oversized_hook_is_rejected(self, tmp_path):
        pkg = tmp_path / "pkg"
        (pkg / "hooks").mkdir(parents=True)
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        # 64 KiB + 1 byte.
        (pkg / "hooks" / "PreToolUse.sh").write_bytes(b"#" * (64 * 1024 + 1))
        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        assert r.returncode != 0
        # Hook must not have been copied.
        assert not (proj / ".claude" / "hooks" / "cleo-v-p" / "PreToolUse.sh").exists()


# ---- Security gate: symlinked hook scripts rejected -------------------------

@pytest.mark.skipif(sys.platform == "win32",
                     reason="symlink creation needs admin on Windows")
class TestHookSymlinkEscapeGate:
    def test_symlinked_hook_pointing_outside_is_rejected(self, tmp_path):
        # Build an external script the symlink would point at.
        outside = tmp_path / "outside-payload"
        outside.mkdir()
        external_script = outside / "evil.sh"
        external_script.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")

        pkg = tmp_path / "pkg"
        (pkg / "hooks").mkdir(parents=True)
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        # Symlink hooks/PreToolUse.sh -> ../outside-payload/evil.sh.
        (pkg / "hooks" / "PreToolUse.sh").symlink_to(external_script)

        _git(pkg, "init", "-q", "-b", "main")
        _git(pkg, "config", "user.email", "t@t.t")
        _git(pkg, "config", "user.name", "t")
        _git(pkg, "add", "-A")
        _git(pkg, "commit", "-qm", "v1")
        _git(pkg, "tag", "v1.0.0")

        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo("require", "v/p", "-c", "^1.0",
                     "--repo", file_url(pkg), "--project", str(proj))
        # Install must fail.
        assert r.returncode != 0
        # Hook must NOT have been copied into the project.
        assert not (proj / ".claude" / "hooks" / "cleo-v-p" / "PreToolUse.sh").exists()
