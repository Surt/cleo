---
name: cleo-require
description: Add a cleo package to cleo.json and install it immediately. Creates cleo.json if absent. Args: $ARGUMENTS
scope: generic
---

# /cleo-require

Add a package to `cleo.json` and install it. Args: `$ARGUMENTS`.

Syntax: `/cleo-require <source>[@<constraint>] [--local] [--user] [--repo <url>] [--symlink] [--dry-run]`

`<source>` accepts any of:

| Form | Example |
| --- | --- |
| GitHub shorthand | `vendor/pkg` |
| Full HTTPS URL | `https://github.com/vendor/pkg` |
| Subdirectory URL | `https://github.com/vendor/pkg/tree/<ref>/<subpath>` |
| GitLab URL | `https://gitlab.com/org/repo` |
| SSH git URL | `git@github.com:vendor/pkg.git` |
| Local path | `./relative` or `/absolute` |

`--repo` is still accepted but prefer the positional source form above.

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

Abort with install instructions if engine missing (same message as `/cleo-install`).

## Parse arguments

From `$ARGUMENTS`:
- `<source>[@<constraint>]` — required. Accepts github shorthand, full URL, subdir URL, gitlab URL, SSH URL, or local path. Constraint defaults to `*` if omitted.
- `--repo <url>` — optional (legacy). Prefer passing the URL as the positional source instead.
- `--symlink` — symlink installed files from the package cache rather than copying them.
- `--local` — install into local bucket (gitignored, this repo only). Rules only in `--local` mode.
- `--user` — install into user bucket (`~/.claude/`, all repos on machine).
- `--dry-run` — show what would change, make no changes.

Examples:
- `/cleo-require acme/cleo-example` → `*` constraint, project bucket
- `/cleo-require acme/cleo-example@^1.0` → semver constraint
- `/cleo-require https://github.com/acme/cleo-example` → full URL form
- `/cleo-require git@github.com:acme/cleo-example.git` → SSH URL form
- `/cleo-require ./local-skills` → local path form
- `/cleo-require acme/cleo-example --symlink` → symlink from cache
- `/cleo-require user/my-rules --local --repo https://github.com/user/my-rules`

## Validate the ref

If `vendor/package` does not contain a `/`, abort:

```
[cleo/require] Package name must be in <vendor>/<name> format.
Example: /cleo-require acme/cleo-example --repo https://github.com/acme/cleo-example
```

## Run the engine

```bash
$PY require <source> [--constraint <constraint>] [--local|--user] [--repo <url>] [--symlink] [--dry-run]
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
/cleo-require acme/cleo-generic
/cleo-require https://github.com/acme/cleo-generic
/cleo-require acme/cleo-example@^1.0
/cleo-require https://github.com/acme/cleo-example/tree/main/skills/my-skill
/cleo-require git@github.com:acme/cleo-mcp-example.git
/cleo-require ./local-skills
/cleo-require user/my-rules --local --repo https://github.com/user/my-rules
/cleo-require acme/cleo-generic --symlink
/cleo-require acme/cleo-generic --dry-run
```
