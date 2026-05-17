---
name: cleo-install
description: Install all packages declared in cleo.json into the current project. Resolves semver, fetches packages, materializes rules/skills/agents/commands/hooks, wires MCP servers. Reads cleo.lock when present (reproducible installs). Args: $ARGUMENTS
scope: generic
---

# /cleo-install

Install all packages from `cleo.json` in the current project. Args: `$ARGUMENTS`.

## Locate the engine

The cleo engine lives in the marketplace clone that Claude Code maintains. Resolve:

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

If `$CLEO_ROOT/tools/cleo.py` does not exist, abort with:

```
[cleo/install] cleo engine not found. Is the plugin installed?
Run: /plugin marketplace add <cleo-repo-url>
     /plugin install cleo@cleo
```

## Check for cleo.json

If `cleo.json` does not exist in `$PWD`:

```
[cleo/install] No cleo.json found in this project.

To get started, run:
  /cleo-require <vendor/package> --repo <url>

Or scaffold a manifest manually — see spec/cleo-json.md for the format.
```

Stop. Do not create cleo.json automatically.

## Run the engine

```bash
$PY install [--dry-run] [--offline]
```

Supported flags from `$ARGUMENTS`:
- `--dry-run` — print what would be installed, make no changes
- `--offline` — skip git fetch, use cached packages only

## Output format

The engine prints one line per package as it works. After completion, surface a summary:

```
[cleo/install] installed=2 updated=1 skipped=0 mcp-added=1

Installed:
  acme/cleo-generic   1.2.0  [bundle]  3 rules, 1 skill
  acme/cleo-example   1.0.0  [bundle]  4 rules

Updated:
  acme/cleo-mcp-example  2.1.0 → 2.2.0  [mcp-server]  MCP key: cleo-acme-cleo-mcp-example
```

## MCP server prompts

When a package declares `type: mcp-server` and its `mcp.json` has `${VAR}` placeholders not already set in the environment, prompt **once per variable** before installing:

```
[cleo/install] acme/cleo-mcp-example requires env var API_BASE_URL.
Enter value (leave blank to skip and configure later):
```

If skipped, install the package but leave the placeholder as-is in `settings.json`. The user can fill it in later.

## Collision detection

Before materializing any file, check if an unmanaged file with the same frontmatter `name:` already exists in the target `.claude/<type>/` dir. If so:

```
[cleo/install] Heads-up: .claude/rules/my-rule.md already declares name: plan-then-doc.
Installing acme/cleo-generic would load both — duplicated rule.

- r — remove the local file and install
- k — keep the local file and install anyway
- a — abort this package
```

Wait for user input. Never delete without confirmation.

## What to commit

| Path | Commit? |
|---|---|
| `cleo.json` | yes |
| `cleo.lock` | yes |
| `.claude/rules/cleo-*`, `.claude/skills/cleo-*`, etc. | yes |
| `.claude/rules/local/cleo-*` | no (gitignored) |
| `~/.claude/rules/cleo-*` | no (out-of-tree) |

## Examples

```
/cleo-install
/cleo-install --dry-run
/cleo-install --offline
```
