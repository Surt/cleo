---
name: cleo-remove
description: Remove one or more cleo packages — deletes installed files, wipes MCP/hook registrations, updates cleo.json and cleo.lock. Args: $ARGUMENTS
scope: generic
---

# /cleo-remove

Remove packages from the project. Args: `$ARGUMENTS`.

Syntax: `/cleo-remove <vendor/package> [<vendor/package> ...]`

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

## Run the engine

```bash
$PY remove <vendor/package> [<vendor/package> ...]
```

The engine:
1. Looks up each package in `cleo.lock`. Warns and skips if not found.
2. Deletes every installed file/directory from disk (rules, skills, agents, commands).
3. Removes the hook directory `.claude/hooks/cleo-<vendor>-<pkg>/` if present.
4. Removes the MCP server entry from `settings.json` if the package installed one.
5. Removes hook registrations from `settings.json`.
6. Removes the package from `cleo.json` (`require` / `require-local` / `require-user`).
7. Removes the package from `cleo.lock`.

## Output

```
[cleo]   removed rule no-assumptions-as-facts
[cleo]   removed rule plan-then-doc
[cleo] removed test/cleo-generic

[cleo] removed=1 not-found=0
```

## Examples

```
/cleo-remove Surt/cleo-plan-then-doc
/cleo-remove Surt/cleo-plan-then-doc acme/cleo-mcp-example
```

Alias: `cleo rm <pkg>` is equivalent to `cleo remove <pkg>`.
