---
name: cleo-require
description: Add a cleo package to cleo.json and install it immediately. Creates cleo.json if absent. Args: $ARGUMENTS
scope: generic
---

# /cleo-require

Add a package to `cleo.json` and install it. Args: `$ARGUMENTS`.

Syntax: `/cleo-require <vendor/package>[@<constraint>] [--local] [--user] [--repo <url>] [--dry-run]`

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

Abort with install instructions if engine missing (same message as `/cleo-install`).

## Parse arguments

From `$ARGUMENTS`:
- `<vendor/package>[@<constraint>]` — required. Constraint defaults to `*` if omitted.
- `--repo <url>` — optional. Git URL for the package repo. Required if the package is not in the registry.
- `--local` — install into local bucket (gitignored, this repo only). Rules only in `--local` mode.
- `--user` — install into user bucket (`~/.claude/`, all repos on machine).
- `--dry-run` — show what would change, make no changes.

Examples:
- `/cleo-require acme/cleo-example` → `*` constraint, project bucket
- `/cleo-require acme/cleo-example@^1.0` → semver constraint
- `/cleo-require acme/cleo-example --repo https://github.com/acme/cleo-example`
- `/cleo-require user/my-rules --local --repo https://github.com/user/my-rules`

## Validate the ref

If `vendor/package` does not contain a `/`, abort:

```
[cleo/require] Package name must be in <vendor>/<name> format.
Example: /cleo-require acme/cleo-example --repo https://github.com/acme/cleo-example
```

## Run the engine

```bash
$PY require <vendor/package> [--constraint <constraint>] [--local|--user] [--repo <url>] [--dry-run]
```

The engine:
1. Adds `--repo` URL to `repositories` in `cleo.json` (if provided and not already present)
2. Resolves the latest version matching the constraint
3. Fetches + materializes the package
4. Adds the package to `cleo.json` under the appropriate `require`/`require-local`/`require-user` key
5. Updates `cleo.lock`

## Output

```
[cleo/require] Added acme/cleo-example@^1.0 → resolved 1.2.0

Installed:
  acme/cleo-example  1.2.0  [skills-pack]
    .claude/rules/cleo-acme-cleo-example-style-patterns.md
    .claude/rules/cleo-acme-cleo-example-style-conventions.md
    .claude/skills/cleo-acme-cleo-example-scaffold/

Next: commit cleo.json and cleo.lock.
```

## If cleo.json absent

Create a minimal `cleo.json` first:

```json
{
  "name": "<basename of $PWD>",
  "repositories": [],
  "require": {},
  "require-local": {},
  "require-user": {}
}
```

Inform the user: `[cleo/require] Created cleo.json.`

## Examples

```
/cleo-require acme/cleo-generic --repo https://github.com/acme/cleo-generic
/cleo-require acme/cleo-example@^1.0 --repo https://github.com/acme/cleo-example
/cleo-require acme/cleo-mcp-example --repo https://github.com/acme/cleo-mcp-example
/cleo-require user/my-rules --local --repo https://github.com/user/my-rules
/cleo-require acme/cleo-generic --dry-run
```
