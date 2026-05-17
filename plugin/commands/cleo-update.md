---
name: cleo-update
description: Update cleo packages to the latest version within their declared constraints. Updates cleo.lock. Args: $ARGUMENTS
scope: generic
---

# /cleo-update

Update packages to the latest version within their constraints. Args: `$ARGUMENTS`.

Syntax: `/cleo-update [<vendor/package> ...] [--dry-run] [--offline]`

With no package names, updates all packages in `cleo.json`.

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

## Preflight checks

1. `cleo.json` must exist — abort with message if missing.
2. `cleo.lock` must exist — warn if missing (first install needed): `[cleo/update] No lock file found. Run /cleo-install first.`

## Run the engine

```bash
$PY update [<vendor/package> ...] [--dry-run] [--offline]
```

The engine:
1. For each package in scope (all, or the specified ones):
   a. Re-fetch available tags from the package's git remote
   b. Resolve the highest version matching the constraint in `cleo.json`
   c. If the resolved version differs from the lock, fetch the new version and re-materialize
2. Update `cleo.lock` with new versions + commit SHAs
3. Report: updated=N, already-current=N, skipped=N (drift)

## Drift detection

If a managed file was hand-edited (local SHA diverged from lock SHA), skip it and warn:

```
[cleo/update] warn: Surt/cleo-plan-then-doc — .claude/rules/cleo-surt-cleo-plan-then-doc-style-patterns.md was hand-edited.
Skipping. Re-run with --force to overwrite.
```

`--force` flag overrides drift protection.

## Output

```
[cleo/update] updated=1 already-current=2 skipped=0

Updated:
  Surt/cleo-plan-then-doc  1.0.0 → 1.2.0
    3 rules refreshed

Already current:
  acme/cleo-generic        1.2.0
  acme/cleo-mcp-example  2.2.0
```

## Adoption

`cleo update` also scans `~/.claude/skills/` and `./.claude/skills/` for SKILL.md directories not yet tracked in `cleo.lock` and prints a one-line note when it finds any. Pass `--adopt` to register them.

- `--adopt` — register any untracked skill directories found during the scan
- `--scope project|global|both` — limit the scan to project skills, user-global skills, or both (default: `both`)
- `--adopt --dry-run` — preview the `cleo.json` / `cleo.lock` diff without writing

## Examples

```
/cleo-update
/cleo-update Surt/cleo-plan-then-doc
/cleo-update --dry-run
/cleo-update Surt/cleo-plan-then-doc acme/cleo-generic
/cleo-update --adopt
/cleo-update --adopt --scope project
/cleo-update --adopt --dry-run
```
