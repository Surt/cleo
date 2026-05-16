import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.sources import parse_source, Source, SourceKind


def test_parses_github_shorthand():
    s = parse_source("vercel-labs/skills")
    assert s.kind == SourceKind.GITHUB_SHORTHAND
    assert s.url == "https://github.com/vercel-labs/skills"
    assert s.name == "vercel-labs/skills"
    assert s.subpath is None


def test_parses_full_github_url():
    s = parse_source("https://github.com/vercel-labs/skills")
    assert s.kind == SourceKind.GIT_URL
    assert s.url == "https://github.com/vercel-labs/skills"
    assert s.name == "vercel-labs/skills"
    assert s.subpath is None


def test_parses_github_url_with_dot_git_suffix():
    s = parse_source("https://github.com/vercel-labs/skills.git")
    assert s.kind == SourceKind.GIT_URL
    assert s.url == "https://github.com/vercel-labs/skills.git"
    assert s.name == "vercel-labs/skills"


def test_parses_github_subdir_url():
    s = parse_source("https://github.com/vercel-labs/skills/tree/main/skills/foo")
    assert s.kind == SourceKind.GIT_SUBDIR
    assert s.url == "https://github.com/vercel-labs/skills"
    assert s.name == "vercel-labs/foo"
    assert s.subpath == "skills/foo"
    assert s.ref == "main"


def test_parses_gitlab_url():
    s = parse_source("https://gitlab.com/org/repo")
    assert s.kind == SourceKind.GIT_URL
    assert s.url == "https://gitlab.com/org/repo"
    assert s.name == "org/repo"


def test_parses_ssh_git_url():
    s = parse_source("git@github.com:vendor/pkg.git")
    assert s.kind == SourceKind.GIT_URL
    assert s.url == "git@github.com:vendor/pkg.git"
    assert s.name == "vendor/pkg"


def test_parses_local_path_relative(tmp_path, monkeypatch):
    target = tmp_path / "my-skills"
    target.mkdir()
    monkeypatch.chdir(tmp_path)
    s = parse_source("./my-skills")
    assert s.kind == SourceKind.LOCAL_PATH
    assert s.local_path == target.resolve()
    assert s.name.endswith("/my-skills")


def test_parses_local_path_absolute(tmp_path):
    target = tmp_path / "my-skills"
    target.mkdir()
    s = parse_source(str(target))
    assert s.kind == SourceKind.LOCAL_PATH
    assert s.local_path == target.resolve()


def test_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        parse_source("")
    with pytest.raises(ValueError):
        parse_source("not a thing")
