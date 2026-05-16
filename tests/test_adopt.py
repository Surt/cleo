import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from lib.adopt import scan_untracked, Discovery


def _make_skill(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc for {name}\n---\nbody\n",
        encoding="utf-8",
    )
    return d


def test_scan_finds_untracked_skill_dirs(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    _make_skill(skills, "foo")
    _make_skill(skills, "bar")

    discoveries = scan_untracked(skills, tracked_paths=set())
    names = {d.skill_name for d in discoveries}
    assert names == {"foo", "bar"}


def test_scan_skips_tracked_paths(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    foo = _make_skill(skills, "foo")
    _make_skill(skills, "bar")

    discoveries = scan_untracked(skills, tracked_paths={foo.resolve()})
    names = {d.skill_name for d in discoveries}
    assert names == {"bar"}


def test_scan_skips_cleo_namespaced_dirs(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    _make_skill(skills, "cleo-vendor-pkg-myskill")  # cleo-managed prefix
    _make_skill(skills, "foo")

    discoveries = scan_untracked(skills, tracked_paths=set())
    names = {d.skill_name for d in discoveries}
    assert names == {"foo"}


def test_scan_records_symlink_provenance(tmp_path):
    # Symlink target inside a git working tree → provenance recoverable
    upstream = tmp_path / "upstream-repo"
    upstream.mkdir()
    (upstream / ".git").mkdir()  # fake git marker
    skill_src = _make_skill(upstream, "linked-skill")

    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    link = skills / "linked-skill"
    try:
        link.symlink_to(skill_src)
    except OSError:
        import pytest
        pytest.skip("symlink not permitted on this platform")

    discoveries = scan_untracked(skills, tracked_paths=set())
    assert len(discoveries) == 1
    d = discoveries[0]
    assert d.skill_name == "linked-skill"
    assert d.symlink_target == skill_src.resolve()
    assert d.is_symlink


def test_scan_records_no_provenance_for_plain_dir(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    _make_skill(skills, "plain")

    discoveries = scan_untracked(skills, tracked_paths=set())
    assert len(discoveries) == 1
    d = discoveries[0]
    assert d.skill_name == "plain"
    assert d.symlink_target is None
    assert not d.is_symlink


def test_scan_handles_missing_directory(tmp_path):
    nonexistent = tmp_path / "does-not-exist"
    discoveries = scan_untracked(nonexistent, tracked_paths=set())
    assert discoveries == []


from lib.adopt import enrich_provenance, _extract_origin_url


def test_extract_origin_url_simple():
    config = '''
[core]
    bare = false
[remote "origin"]
    url = https://github.com/vendor/repo.git
    fetch = +refs/heads/*:refs/remotes/origin/*
[branch "main"]
    remote = origin
'''
    assert _extract_origin_url(config) == "https://github.com/vendor/repo.git"


def test_extract_origin_url_returns_none_when_no_origin():
    config = '[core]\n    bare = false\n'
    assert _extract_origin_url(config) is None


def test_extract_origin_url_handles_ssh_url():
    config = '''
[remote "origin"]
    url = git@github.com:vendor/repo.git
'''
    assert _extract_origin_url(config) == "git@github.com:vendor/repo.git"


def test_enrich_populates_git_remote_for_symlink_into_repo(tmp_path):
    from lib.adopt import Discovery

    repo = tmp_path / "checkout"
    repo.mkdir()
    git = repo / ".git"
    git.mkdir()
    (git / "config").write_text(
        '[remote "origin"]\n    url = https://github.com/x/y.git\n',
        encoding="utf-8",
    )
    skill_src = repo / "skills" / "my-skill"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")

    d = Discovery(
        skill_name="my-skill", path=tmp_path / "linked", is_symlink=True,
        symlink_target=skill_src,
    )
    enriched = enrich_provenance(d)
    assert enriched.git_remote == "https://github.com/x/y.git"


def test_enrich_returns_unchanged_for_non_symlink(tmp_path):
    from lib.adopt import Discovery
    d = Discovery(skill_name="plain", path=tmp_path / "plain", is_symlink=False)
    out = enrich_provenance(d)
    assert out.git_remote is None
