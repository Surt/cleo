---
name: cleo-list
description: List installed cleo packages from cleo.lock, with version, type, and item count. Args: $ARGUMENTS
scope: generic
---

# /cleo-list

Show installed packages from `cleo.lock`. Args: `$ARGUMENTS`.

Syntax: `/cleo-list [--json] [--verbose]`

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

## Run the engine

```bash
$PY list [--json] [--verbose]
```

## Output (default)

Markdown table, one row per installed package:

```
[cleo/list] 3 packages installed

| Package                     | Type        | Version | Commit  | Bucket  | Items |
| --------------------------- | ----------- | ------- | ------- | ------- | ----- |
| acme/cleo-mcp-example       | mcp-server  | 2.2.0   | abc123f | project | 1 rule + MCP |
| acme/cleo-generic           | skills-pack | 1.2.0   | def456a | project | 3 rules, 1 skill |
| acme/cleo-example           | skills-pack | 1.0.0   | 789abcd | project | 4 rules |
```

## Output (--verbose)

Expands each package to show all installed item paths.

## No lock file

```
[cleo/list] No cleo.lock found. Run /cleo-install to install packages from cleo.json.
```

## No packages installed

```
[cleo/list] No packages installed yet. Add one with /cleo-require.
```

## Examples

```
/cleo-list
/cleo-list --verbose
/cleo-list --json
```
