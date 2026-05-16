"""Package-author tooling for cleo: generate manifest, validate, release.

Three logical units live here (kept in one file for now — split if it grows
past ~300 lines):
  - manifest: detect/merge/bump/write the package's own cleo.json
  - validate: run the same gates `cleo install` runs, plus frontmatter +
              a dry install against a temp project
  - gitops:   wrap the subprocess git calls publish needs (tag, push, ...)

All git invocations route through validate_git_ref (lib/security) and use
`--` to separate options from positional refs.
"""

from __future__ import annotations

from .semver import parse_version

_BUMP_LEVELS = ("patch", "minor", "major")


def bump_version(current: str, level: str) -> str:
    """Bump a semver string and return the new value as `MAJOR.MINOR.PATCH`.

    Drops any pre-release/build suffix. Raises ValueError if `current` is
    not parseable or `level` is not one of patch/minor/major.
    """
    if level not in _BUMP_LEVELS:
        raise ValueError(f"bump level {level!r} not in {_BUMP_LEVELS}")
    v = parse_version(current)
    if v is None:
        raise ValueError(f"version {current!r} is not parseable semver")
    if level == "patch":
        return f"{v.major}.{v.minor}.{v.patch + 1}"
    if level == "minor":
        return f"{v.major}.{v.minor + 1}.0"
    return f"{v.major + 1}.0.0"
