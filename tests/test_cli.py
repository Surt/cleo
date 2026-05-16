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

# Direct-import shim for unit-testing internal helpers (subprocess CLI tests follow below).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from cleo import _materialize_symlink

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


# ---- Security gate: empty package rejected ----------------------------------

class TestEmptyPackageGate:
    def test_package_with_no_artifacts_is_rejected(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        # cleo.json declares type, but the repo has NO artifact dirs
        # and NO mcp.json — just a README.
        (pkg / "cleo.json").write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (pkg / "README.md").write_text("# v/p\nempty\n", encoding="utf-8")
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
        assert r.returncode != 0, (
            f"empty package should be rejected; got stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        # Package must NOT be in the project manifest.
        manifest = json.loads((proj / "cleo.json").read_text(encoding="utf-8"))
        assert "v/p" not in manifest.get("require", {})
        # No lock file written (no successful install).
        if (proj / "cleo.lock").exists():
            import json as _j
            lock = _j.loads((proj / "cleo.lock").read_text(encoding="utf-8"))
            assert "v/p" not in lock.get("packages", {})


# ---- Security gate: bad package ref via CLI rejected ------------------------

class TestPackageRefGateCLI:
    def test_cli_require_with_traversal_pkg_ref_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        r = run_cleo(
            "require", "v/../../tmp/evil", "-c", "*",
            "--repo", "file:///nonexistent",
            "--project", str(proj),
        )
        assert r.returncode != 0, (
            f"path-traversal pkg ref should be rejected; "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        combined = (r.stdout + r.stderr).lower()
        assert (
            "package reference" in combined
            or "vendor" in combined
            or "unrecognized source" in combined
        )
        # Nothing should be written outside the project, and the project
        # manifest must not list the bad ref.
        manifest = json.loads((proj / "cleo.json").read_text(encoding="utf-8"))
        assert "v/../../tmp/evil" not in manifest.get("require", {})


# ---- Security gate: bad package ref via project manifest rejected -----------

class TestPackageRefGateManifest:
    def test_install_with_traversal_key_in_manifest_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        # Hand-craft a malicious manifest.
        (proj / "cleo.json").write_text(
            json.dumps({
                "name": "proj",
                "repositories": [],
                "require": {"v/../../tmp/evil": "*"},
                "require-local": {},
                "require-user": {},
            }),
            encoding="utf-8",
        )
        r = run_cleo("install", "--project", str(proj))
        # The install should fail (or skip the bad ref with non-zero exit).
        # At minimum: nothing under the project's .claude/ for the bad ref,
        # and no lock entry for it.
        bad_claude_marker = proj / ".claude" / "rules" / "cleo-v---tmp-evil-anything.md"
        assert not bad_claude_marker.exists()
        # Lock either absent or lacks the bad ref.
        lock_path = proj / "cleo.lock"
        if lock_path.exists():
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            assert "v/../../tmp/evil" not in lock.get("packages", {})
        combined = (r.stdout + r.stderr).lower()
        assert "package reference" in combined or "vendor" in combined


# ---- Security gate: leading-dash tag/url rejected ---------------------------

class TestGitRefGate:
    def test_cli_require_with_leading_dash_url_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        run_cleo("init", "--project", str(proj))
        # Use = syntax so argparse passes the leading-dash value to cleo rather
        # than treating it as a flag; this lets the security gate (not argparse)
        # produce the rejection.
        r = run_cleo(
            "require", "v/p", "-c", "*",
            "--repo=-upload-pack=evil",
            "--project", str(proj),
        )
        assert r.returncode != 0, (
            f"leading-dash URL should be rejected; "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        combined = (r.stdout + r.stderr).lower()
        assert "git ref" in combined or "leading" in combined or "potential" in combined


# ---- Security gate: symlinked manifest files rejected -----------------------

@pytest.mark.skipif(sys.platform == "win32",
                     reason="symlink creation needs admin on Windows")
class TestSymlinkedManifestGate:
    def test_symlinked_cleo_json_is_rejected(self, tmp_path):
        # The decoy "real" file lives outside the package.
        outside = tmp_path / "outside.json"
        outside.write_text(
            json.dumps({"name": "v/p", "type": "skills-pack", "version": "1.0.0"}),
            encoding="utf-8",
        )

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        # Symlink pkg/cleo.json -> ../outside.json. The package STILL has
        # an artifact dir so the empty-package gate doesn't pre-empt this.
        (pkg / "cleo.json").symlink_to(outside)
        (pkg / "rules").mkdir()
        (pkg / "rules" / "r.md").write_text(
            "---\nname: r\ndescription: smoke rule for symlinked-manifest gate test\n---\n\nbody\n",
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
        manifest = json.loads((proj / "cleo.json").read_text(encoding="utf-8"))
        assert "v/p" not in manifest.get("require", {})


# ---- _materialize_symlink unit tests ----------------------------------------

class TestMaterializeSymlink:
    def test_materialize_symlink_creates_symlink_for_dir(self, tmp_path):
        src = tmp_path / "src_skill"
        src.mkdir()
        (src / "SKILL.md").write_text("---\nname: foo\n---\nbody\n", encoding="utf-8")
        dst = tmp_path / "dst" / "cleo-foo-bar"
        try:
            _materialize_symlink(src, dst)
        except OSError:
            pytest.skip("symlink not permitted on this platform")
        assert dst.is_symlink()
        assert dst.resolve() == src.resolve()
        assert (dst / "SKILL.md").read_text(encoding="utf-8").startswith("---")

    def test_materialize_symlink_replaces_existing_dst(self, tmp_path):
        src = tmp_path / "src_skill"
        src.mkdir()
        (src / "SKILL.md").write_text("---\nname: foo\n---\n", encoding="utf-8")
        dst = tmp_path / "dst" / "cleo-foo-bar"
        dst.parent.mkdir(parents=True)
        dst.mkdir()
        (dst / "stale.md").write_text("old", encoding="utf-8")
        try:
            _materialize_symlink(src, dst)
        except OSError:
            pytest.skip("symlink not permitted on this platform")
        assert dst.is_symlink()
        assert not (dst / "stale.md").exists()

    def test_materialize_symlink_replaces_existing_symlink_at_dst(self, tmp_path):
        src = tmp_path / "src_skill"
        src.mkdir()
        (src / "SKILL.md").write_text("---\nname: foo\n---\n", encoding="utf-8")

        other = tmp_path / "other_dir"
        other.mkdir()
        (other / "junk.md").write_text("nope", encoding="utf-8")

        dst = tmp_path / "dst" / "cleo-foo-bar"
        dst.parent.mkdir(parents=True)
        try:
            dst.symlink_to(other)
        except OSError:
            import pytest
            pytest.skip("symlink not permitted on this platform")

        try:
            _materialize_symlink(src, dst)
        except OSError:
            import pytest
            pytest.skip("symlink not permitted on this platform")
        assert dst.is_symlink()
        assert dst.resolve() == src.resolve()  # now points at src, not other
        # The pre-existing dst symlink was replaced — its old target (other/) is unaffected.
        assert (other / "junk.md").exists()


# ---- install_package: install_mode wiring ------------------------------------


def test_install_package_records_symlink_mode(tmp_path, monkeypatch):
    """install_package with install_mode='symlink' records it on the LockPackage."""
    import pytest, sys
    if sys.platform == "win32":
        pytest.skip("symlink mode test runs on POSIX (Windows needs dev-mode)")
    from cleo import install_package, _pkg_cache_dir

    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
    name = "test/pkg"
    version = "1.0.0"
    cache = _pkg_cache_dir(name, version)
    cache.mkdir(parents=True)
    (cache / "cleo.json").write_text(
        '{"name":"test/pkg","type":"skills-pack","version":"1.0.0"}\n',
        encoding="utf-8",
    )
    skill_dir = cache / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: x\n---\nbody\n", encoding="utf-8"
    )

    project = tmp_path / "proj"
    project.mkdir()

    result = install_package(
        project, name, "https://example/test/pkg", "1.0.0", "project",
        locked_version=version, locked_commit="0" * 40,
        install_mode="symlink", quiet=True,
    )
    assert result is not None
    assert result.install_mode == "symlink"
    dst = project / ".claude" / "skills" / "cleo-test-pkg-my-skill"
    assert dst.is_symlink()


def test_require_with_symlink_flag_symlinks_artifact(tmp_path, monkeypatch):
    """`cleo require --symlink foo/bar` results in a symlinked artifact and install_mode=symlink in lock."""
    import pytest, sys, json
    if sys.platform == "win32":
        pytest.skip("symlink mode test runs on POSIX")
    from cleo import main, _pkg_cache_dir
    import cleo as cleo_mod

    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
    # Pre-seed cache so we don't need to clone.
    cache = _pkg_cache_dir("test/pkg", "1.0.0")
    cache.mkdir(parents=True)
    (cache / "cleo.json").write_text(
        '{"name":"test/pkg","type":"skills-pack","version":"1.0.0"}\n', encoding="utf-8"
    )
    sd = cache / "skills" / "my-skill"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\nname: my-skill\ndescription: x\n---\n", encoding="utf-8")

    project = tmp_path / "proj"
    project.mkdir()

    # Mock version resolution to avoid network.
    monkeypatch.setattr(cleo_mod, "resolve_version", lambda url, c: ("1.0.0", "v1.0.0"))
    monkeypatch.setattr(cleo_mod, "resolve_commit", lambda url, tag: "a" * 40)
    monkeypatch.setattr(cleo_mod, "_clone_or_fetch",
                         lambda url, cdir, tag, expected_commit=None: True)

    rc = main(["--project", str(project), "require", "test/pkg",
               "--repo", "https://example/test/pkg", "--symlink", "--quiet"])
    assert rc == 0
    dst = project / ".claude" / "skills" / "cleo-test-pkg-my-skill"
    assert dst.is_symlink()

    lock = json.loads((project / "cleo.lock").read_text(encoding="utf-8"))
    assert lock["packages"]["test/pkg"]["install_mode"] == "symlink"


def test_install_package_records_copy_mode_default(tmp_path, monkeypatch):
    """install_package defaults to install_mode='copy' and copies (not symlinks)."""
    from cleo import install_package, _pkg_cache_dir

    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
    name = "test/pkg"
    version = "1.0.0"
    cache = _pkg_cache_dir(name, version)
    cache.mkdir(parents=True)
    (cache / "cleo.json").write_text(
        '{"name":"test/pkg","type":"skills-pack","version":"1.0.0"}\n',
        encoding="utf-8",
    )
    skill_dir = cache / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: x\n---\nbody\n", encoding="utf-8"
    )

    project = tmp_path / "proj"
    project.mkdir()

    result = install_package(
        project, name, "https://example/test/pkg", "1.0.0", "project",
        locked_version=version, locked_commit="0" * 40,
        quiet=True,
    )
    assert result is not None
    assert result.install_mode == "copy"
    dst = project / ".claude" / "skills" / "cleo-test-pkg-my-skill"
    assert dst.is_dir()
    assert not dst.is_symlink()


# ---- B2: cmd_require accepts URL and shorthand as positional source ----------

class TestRequireSources:
    """cmd_require accepts GitHub shorthand and full URLs as positional arg."""

    def _fake_clone(self, captured: dict, cdir_ref: list):
        """Return a fake _clone_or_fetch that records the URL and writes a minimal package."""
        def fake_clone(url, cdir, tag, expected_commit=None):
            captured["url"] = url
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "cleo.json").write_text(
                '{"name":"test/foo","type":"skills-pack","version":"1.0.0"}\n',
                encoding="utf-8",
            )
            sd = cdir / "skills" / "x"
            sd.mkdir(parents=True)
            (sd / "SKILL.md").write_text(
                "---\nname: x\ndescription: y\n---\n", encoding="utf-8"
            )
            return True
        return fake_clone

    def test_require_accepts_github_shorthand_as_positional(self, tmp_path, monkeypatch):
        """`cleo require foo/bar` resolves to https://github.com/foo/bar without --repo."""
        import cleo as cleo_mod

        monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(cleo_mod, "resolve_version", lambda url, c: ("1.0.0", "v1.0.0"))
        monkeypatch.setattr(cleo_mod, "resolve_commit", lambda url, tag: "a" * 40)
        captured = {}
        monkeypatch.setattr(cleo_mod, "_clone_or_fetch", self._fake_clone(captured, []))

        project = tmp_path / "proj"
        project.mkdir()
        rc = cleo_mod.main(["--project", str(project), "require", "test/foo", "--quiet"])
        assert rc == 0
        assert captured["url"] == "https://github.com/test/foo"

    def test_require_accepts_full_url_as_positional(self, tmp_path, monkeypatch):
        """`cleo require https://github.com/foo/bar` works without --repo."""
        import cleo as cleo_mod

        monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(cleo_mod, "resolve_version", lambda url, c: ("1.0.0", "v1.0.0"))
        monkeypatch.setattr(cleo_mod, "resolve_commit", lambda url, tag: "a" * 40)
        captured = {}
        monkeypatch.setattr(cleo_mod, "_clone_or_fetch", self._fake_clone(captured, []))

        project = tmp_path / "proj"
        project.mkdir()
        rc = cleo_mod.main(["--project", str(project), "require",
                             "https://github.com/test/foo", "--quiet"])
        assert rc == 0
        assert captured["url"] == "https://github.com/test/foo"


def test_require_subdir_form_installs_only_subdir(tmp_path, monkeypatch):
    """`cleo require https://github.com/owner/repo/tree/main/skills/foo` installs only that subdir."""
    import cleo as cleo_mod

    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cleo_mod, "resolve_version", lambda url, c: ("1.0.0", "v1.0.0"))
    monkeypatch.setattr(cleo_mod, "resolve_commit", lambda url, tag: "a" * 40)

    def fake_clone_subdir(url, cdir, tag, subpath, expected_commit=None):
        # Simulate a sparse-checkout result: only the named subdir exists.
        cdir.mkdir(parents=True, exist_ok=True)
        skill_dir = cdir / "skills" / "foo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: foo\ndescription: y\n---\n", encoding="utf-8"
        )
        # cleo.json synthesized at cache root by the implementation.
        (cdir / "cleo.json").write_text(
            '{"name":"owner/foo","type":"skills-pack","version":"1.0.0"}\n',
            encoding="utf-8",
        )
        return True
    monkeypatch.setattr(cleo_mod, "_clone_or_fetch_subdir", fake_clone_subdir, raising=False)

    project = tmp_path / "proj"
    project.mkdir()
    rc = cleo_mod.main([
        "--project", str(project), "require",
        "https://github.com/owner/repo/tree/main/skills/foo", "--quiet",
    ])
    assert rc == 0
    skills_dir = project / ".claude" / "skills"
    assert (skills_dir / "cleo-owner-foo-foo").exists()


def test_require_local_path_dry_run_makes_no_changes(tmp_path, monkeypatch):
    """`cleo require ./local-pkg --dry-run` neither installs artifacts nor mutates the manifest."""
    import cleo as cleo_mod
    import json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    src_pkg = tmp_path / "local-pkg"
    src_pkg.mkdir()
    (src_pkg / "cleo.json").write_text(
        '{"name":"local/local-pkg","type":"skills-pack","version":"0.0.1"}\n',
        encoding="utf-8",
    )
    skill_dir = src_pkg / "skills" / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hello\ndescription: hi\n---\nbody\n", encoding="utf-8"
    )

    project = tmp_path / "proj"
    project.mkdir()
    rc = cleo_mod.main(["--project", str(project), "require", str(src_pkg), "--dry-run", "--quiet"])
    assert rc == 0

    # No artifact materialized.
    assert not (project / ".claude" / "skills" / "cleo-local-local-pkg-hello").exists()

    # Manifest entry NOT added (scaffold_manifest creates an empty require-* set; we check the bucket the install would target).
    manifest = json.loads((project / "cleo.json").read_text(encoding="utf-8"))
    # Default bucket is BUCKET_PROJECT → "require"
    assert "local/local-pkg" not in manifest.get("require", {})

    # Lock file should not have been written.
    assert not (project / "cleo.lock").exists()


def test_require_local_path_installs_without_clone(tmp_path, monkeypatch):
    """`cleo require ./local-pkg` installs from filesystem; no git clone."""
    import cleo as cleo_mod
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    src_pkg = tmp_path / "local-pkg"
    src_pkg.mkdir()
    (src_pkg / "cleo.json").write_text(
        '{"name":"local/local-pkg","type":"skills-pack","version":"0.0.1"}\n',
        encoding="utf-8",
    )
    skill_dir = src_pkg / "skills" / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hello\ndescription: hi\n---\nbody\n", encoding="utf-8"
    )

    # Guard: clone must NOT be called.
    def boom(*a, **kw):
        raise AssertionError("clone called for local-path install")
    monkeypatch.setattr(cleo_mod, "_clone_or_fetch", boom)
    monkeypatch.setattr(cleo_mod, "_clone_or_fetch_subdir", boom, raising=False)

    project = tmp_path / "proj"
    project.mkdir()
    rc = cleo_mod.main(["--project", str(project), "require", str(src_pkg), "--quiet"])
    assert rc == 0
    assert (project / ".claude" / "skills" / "cleo-local-local-pkg-hello").exists()


# ---- C1: verb aliases (add / ls / rm) ---------------------------------------

def test_alias_add_dispatches_to_require(tmp_path, monkeypatch):
    """`cleo add foo/bar` invokes cmd_require."""
    import cleo as cleo_mod
    calls = {}
    def fake_require(args):
        calls["called"] = True
        calls["package"] = args.package
        return 0
    monkeypatch.setattr(cleo_mod, "cmd_require", fake_require)
    rc = cleo_mod.main(["--project", str(tmp_path), "add", "foo/bar", "--quiet"])
    assert rc == 0
    assert calls.get("called")
    assert calls["package"] == "foo/bar"


def test_alias_ls_dispatches_to_list(tmp_path, monkeypatch):
    import cleo as cleo_mod
    calls = {}
    def fake_list(args):
        calls["called"] = True
        return 0
    monkeypatch.setattr(cleo_mod, "cmd_list", fake_list)
    rc = cleo_mod.main(["--project", str(tmp_path), "ls"])
    assert rc == 0
    assert calls.get("called")


def test_alias_rm_dispatches_to_remove(tmp_path, monkeypatch):
    import cleo as cleo_mod
    calls = {}
    def fake_remove(args):
        calls["called"] = True
        calls["packages"] = args.packages
        return 0
    monkeypatch.setattr(cleo_mod, "cmd_remove", fake_remove)
    rc = cleo_mod.main(["--project", str(tmp_path), "rm", "foo/bar"])
    assert rc == 0
    assert calls.get("called")


# ---------------------------------------------------------------------------
# C2: cleo find — local substring search
# ---------------------------------------------------------------------------

def test_find_matches_description_substring(tmp_path, monkeypatch, capsys):
    """`cleo find foo` finds installed packages whose name OR item names contain 'foo'."""
    import cleo as cleo_mod, json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )
    lock = {
        "version": 1,
        "generated": "2026-05-16T00:00:00Z",
        "packages": {
            "ven/foo-pkg": {
                "type": "skills-pack", "url": "https://x", "version": "1.0.0",
                "commit": "a" * 40, "bucket": "project", "items": [
                    {"type": "skill", "name": "do-foo", "path": "/tmp/foo", "sha": ""}
                ],
            },
            "ven/bar-pkg": {
                "type": "skills-pack", "url": "https://y", "version": "1.0.0",
                "commit": "b" * 40, "bucket": "project", "items": [
                    {"type": "skill", "name": "do-bar", "path": "/tmp/bar", "sha": ""}
                ],
            },
        },
    }
    (project / "cleo.lock").write_text(json.dumps(lock), encoding="utf-8")

    rc = cleo_mod.main(["--project", str(project), "find", "foo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ven/foo-pkg" in out
    assert "do-foo" in out or "ven/foo-pkg" in out  # match shows up by name or item
    assert "ven/bar-pkg" not in out  # not a match


def test_find_no_match_returns_zero(tmp_path, capsys):
    import cleo as cleo_mod, json
    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )
    (project / "cleo.lock").write_text(
        json.dumps({"version": 1, "generated": "x", "packages": {}}), encoding="utf-8"
    )
    rc = cleo_mod.main(["--project", str(project), "find", "zzz"])
    assert rc == 0


def test_update_has_adopt_flag(tmp_path):
    """`cleo update --help` exits 0 and mentions --adopt and --scope."""
    import cleo as cleo_mod
    import io, contextlib, pytest
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc:
            cleo_mod.main(["update", "--help"])
        assert exc.value.code == 0
    out = buf.getvalue()
    assert "--adopt" in out
    assert "--scope" in out


def test_update_reports_untracked_without_adopting(tmp_path, monkeypatch, capsys):
    """`cleo update` finds untracked skill dirs in .claude/skills/ and prints a note,
    but does NOT modify cleo.json or cleo.lock.
    """
    import cleo as cleo_mod, json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
    # Create global skills/ with one untracked dir.
    user_home = tmp_path / "home"
    global_skills = user_home / ".claude" / "skills"
    global_skills.mkdir(parents=True)
    untracked = global_skills / "untracked-skill"
    untracked.mkdir()
    (untracked / "SKILL.md").write_text(
        "---\nname: untracked-skill\ndescription: x\n---\n", encoding="utf-8"
    )

    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )
    (project / "cleo.lock").write_text(
        json.dumps({"version": 1, "generated": "x", "packages": {}}), encoding="utf-8"
    )

    rc = cleo_mod.main(["--project", str(project), "update", "--scope", "global"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "untracked-skill" in out
    assert "--adopt" in out

    # cleo.json + cleo.lock unchanged.
    manifest = json.loads((project / "cleo.json").read_text(encoding="utf-8"))
    assert manifest.get("require") == {}
    lock = json.loads((project / "cleo.lock").read_text(encoding="utf-8"))
    assert lock["packages"] == {}


def test_update_with_adopt_registers_untracked_skills(tmp_path, monkeypatch):
    """`cleo update --adopt` writes the discoveries into cleo.json + cleo.lock."""
    import cleo as cleo_mod, json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cleo_mod, "resolve_version", lambda url, c: ("1.0.0", "v1.0.0"))
    monkeypatch.setattr(cleo_mod, "resolve_commit", lambda url, tag: "a" * 40)

    user_home = tmp_path / "home"
    global_skills = user_home / ".claude" / "skills"
    global_skills.mkdir(parents=True)
    sk = global_skills / "external-skill"
    sk.mkdir()
    (sk / "SKILL.md").write_text(
        "---\nname: external-skill\ndescription: hello\n---\n", encoding="utf-8"
    )

    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )

    rc = cleo_mod.main([
        "--project", str(project), "update", "--scope", "global", "--adopt", "--quiet",
    ])
    assert rc == 0

    manifest = json.loads((project / "cleo.json").read_text(encoding="utf-8"))
    user_req = manifest.get("require-user", {})
    assert any(k.endswith("external-skill") for k in user_req)

    lock = json.loads((project / "cleo.lock").read_text(encoding="utf-8"))
    assert any(
        pkg.get("url", "").startswith("file://") for pkg in lock["packages"].values()
    )


def test_update_adopt_synthesizes_safe_name_for_leading_dashes(tmp_path, monkeypatch):
    """`_adopt_one` strips leading dashes/dots so the synthesized name is valid."""
    import cleo as cleo_mod, json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    user_home = tmp_path / "home"
    global_skills = user_home / ".claude" / "skills"
    global_skills.mkdir(parents=True)
    # Skill dir with a name that filters to a leading-dash string.
    sk = global_skills / "--weird.name"
    sk.mkdir()
    (sk / "SKILL.md").write_text("---\nname: weird\n---\n", encoding="utf-8")

    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )

    rc = cleo_mod.main([
        "--project", str(project), "update", "--scope", "global", "--adopt", "--quiet",
    ])
    assert rc == 0
    manifest = json.loads((project / "cleo.json").read_text(encoding="utf-8"))
    # Every registered name in require-user must start with [a-z0-9] in its leaf part.
    for name in manifest.get("require-user", {}):
        leaf = name.split("/", 1)[1]
        assert leaf[0].isalnum(), f"leaf starts with non-alnum: {leaf!r}"


def test_update_with_adopt_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    """`cleo update --adopt --dry-run` prints the diff but does not modify files."""
    import cleo as cleo_mod, json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    user_home = tmp_path / "home"
    global_skills = user_home / ".claude" / "skills"
    global_skills.mkdir(parents=True)
    sk = global_skills / "dry-skill"
    sk.mkdir()
    (sk / "SKILL.md").write_text("---\nname: dry-skill\n---\n", encoding="utf-8")

    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )

    rc = cleo_mod.main([
        "--project", str(project), "update", "--scope", "global", "--adopt", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-skill" in out
    manifest = json.loads((project / "cleo.json").read_text(encoding="utf-8"))
    assert manifest["require-user"] == {}  # unchanged


def test_full_migration_roundtrip(tmp_path, monkeypatch, capsys):
    """End-to-end: simulate vercel-labs/skills install, then cleo update --adopt,
    then plain cleo update (no further discoveries, no churn).
    """
    import cleo as cleo_mod, json
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    # Simulate `npx skills add foo/bar` — drops a skill in ~/.claude/skills/.
    global_skills = tmp_path / "home" / ".claude" / "skills"
    global_skills.mkdir(parents=True)
    pre_existing = global_skills / "preexisting"
    pre_existing.mkdir()
    (pre_existing / "SKILL.md").write_text(
        "---\nname: preexisting\ndescription: from vercel-labs\n---\n",
        encoding="utf-8",
    )

    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{},"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )

    # Step 1: bare update reports discovery.
    rc = cleo_mod.main([
        "--project", str(project), "update", "--scope", "global",
    ])
    out1 = capsys.readouterr().out
    assert rc == 0
    assert "preexisting" in out1
    assert "--adopt" in out1

    # Step 2: update --adopt registers.
    rc = cleo_mod.main([
        "--project", str(project), "update", "--scope", "global", "--adopt", "--quiet",
    ])
    capsys.readouterr()
    assert rc == 0
    lock = json.loads((project / "cleo.lock").read_text(encoding="utf-8"))
    assert any("preexisting" in name for name in lock["packages"])

    # Step 3: another plain update reports NO new discoveries.
    rc = cleo_mod.main([
        "--project", str(project), "update", "--scope", "global",
    ])
    out3 = capsys.readouterr().out
    assert rc == 0
    assert "preexisting" not in out3
    assert "untracked" not in out3


# ---------------------------------------------------------------------------
# Issue 1: cmd_update silently downgrades symlink installs to copy
# ---------------------------------------------------------------------------

def test_update_preserves_symlink_install_mode(tmp_path, monkeypatch):
    """`cleo update` on a symlink-installed package keeps install_mode=symlink in lock."""
    if sys.platform == "win32":
        pytest.skip("symlink mode requires POSIX or Windows dev-mode")
    import cleo as cleo_mod
    monkeypatch.setenv("CLEO_USER_HOME", str(tmp_path / "home"))

    # Seed a project with a symlinked package in lock.
    project = tmp_path / "proj"
    project.mkdir()
    (project / "cleo.json").write_text(
        '{"name":"proj","repositories":[],"require":{"test/pkg":"^1.0.0"},'
        '"require-local":{},"require-user":{}}\n',
        encoding="utf-8",
    )
    lock_data = {
        "version": 1,
        "generated": "x",
        "packages": {
            "test/pkg": {
                "type": "skills-pack",
                "url": "https://example/test/pkg",
                "version": "1.0.0",
                "commit": "a" * 40,
                "bucket": "project",
                "install_mode": "symlink",
                "items": [],
            }
        },
    }
    (project / "cleo.lock").write_text(json.dumps(lock_data), encoding="utf-8")

    # Mock fetch / version resolution so update doesn't hit network.
    # New version 1.1.0 to trigger the install path (not "already current").
    monkeypatch.setattr(cleo_mod, "resolve_version", lambda url, c: ("1.1.0", "v1.1.0"))
    monkeypatch.setattr(cleo_mod, "resolve_commit", lambda url, tag: "b" * 40)

    # Pre-seed cache for 1.1.0 so install_package finds it without cloning.
    cache = cleo_mod._pkg_cache_dir("test/pkg", "1.1.0")
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "cleo.json").write_text(
        '{"name":"test/pkg","type":"skills-pack","version":"1.1.0"}\n', encoding="utf-8"
    )
    sd = cache / "skills" / "x"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    monkeypatch.setattr(cleo_mod, "_clone_or_fetch",
                        lambda url, cdir, tag, expected_commit=None: True)
