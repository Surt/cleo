---
name: cleo-publish
description: "Refresh a cleo package's cleo.json, validate it, and optionally bump/commit/tag/push a release. Run from inside the package repo. Args: $ARGUMENTS"
scope: generic
---

# /cleo-publish

Refresh the package's `cleo.json`, run the same validation `cleo install` runs, and optionally bump version + commit + tag + push. Args: `$ARGUMENTS`.

Syntax: `/cleo-publish [--bump patch|minor|major] [--commit] [--tag] [--push] [--release] [--yes] [--remote <name>] [--package <path>]`

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\""
```

Abort with install instructions if engine missing.

## Run the engine

```bash
$PY publish $ARGUMENTS
```

The engine:
1. Detects the package name (from `cleo.json` or origin remote), type (from artifacts present), and version (from highest semver git tag).
2. Merges with the existing `cleo.json` if present — author-set fields always win.
3. Writes `cleo.json` to disk (atomic).
4. Runs all security gates the install pipeline runs, plus frontmatter checks on every rule/skill/agent/command, plus a dry install against a temp project.
5. If `--bump` / `--commit` / `--tag` / `--push` (or `--release`) flags are set, performs those git operations in order. Each step is opt-in; earlier steps are implied by later ones.

## Examples

- `/cleo-publish` → refresh manifest + validate, no git ops.
- `/cleo-publish --bump patch` → refresh + bump 0.1.0 → 0.1.1 in cleo.json, no commit/tag.
- `/cleo-publish --bump minor --commit` → refresh + bump + commit cleo.json.
- `/cleo-publish --release` → full chain: bump patch, commit, tag, push to origin.
- `/cleo-publish --release --bump major` → full chain with explicit major bump.

## Output

```
[cleo] refreshed /path/to/cleo.json
[cleo] bumped version 0.1.0 → 0.1.1
[cleo] acme/widgets 0.1.1 [skills-pack] — validation passed
[cleo] committed cleo.json (v0.1.1)
[cleo] created tag v0.1.1
[cleo] pushed main + v0.1.1 to origin
```

## When validation fails

The command exits non-zero and prints each error. No git operation runs — the (possibly newly written) `cleo.json` stays on disk so you can inspect and fix.
