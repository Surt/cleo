---
name: cleo-find
description: Search installed cleo packages by name, item name, or description. Args: $ARGUMENTS
scope: generic
---

# /cleo-find

Search currently-installed cleo packages by name, item name, or description. Args: `$ARGUMENTS`.

Syntax: `/cleo-find <query>`

## Locate the engine

```
CLEO_ROOT="$HOME/.claude/plugins/marketplaces/cleo"
PY="python3 \"$CLEO_ROOT/tools/cleo.py\" --project \"$PWD\""
```

## Run the engine

```bash
$PY find <query>
```

## What it does

Runs `cleo find <query>` against the project's `cleo.lock` and the package cache. Prints each matching package and the reason for the match (package name, item name, or `description:` frontmatter field of a SKILL.md or rule file).

Local-only — does NOT query a remote index.

## Output

```
[cleo/find] 2 matches for "style"

  acme/cleo-example          matched: rule description ("style patterns")
  acme/cleo-generic          matched: skill name ("style-conventions")
```

## No matches

```
[cleo/find] No installed packages match "style".
```

## No lock file

```
[cleo/find] No cleo.lock found. Run /cleo-install to install packages from cleo.json.
```

## Examples

```
/cleo-find style
/cleo-find security
/cleo-find acme
```
