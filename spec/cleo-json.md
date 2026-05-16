# cleo.json format

`cleo.json` is the project-level manifest file. It declares which packages the project depends on. Commit this file — it is the source of truth for the team.

## Schema

```json
{
  "name": "my-project",
  "repositories": [
    { "type": "git", "url": "https://github.com/vendor/package" }
  ],
  "require": {
    "vendor/package": "^1.0"
  },
  "require-local": {
    "vendor/personal": "*"
  },
  "require-user": {}
}
```

## Fields

### `name` (string, optional)

Human label for this project. Defaults to the directory name.

### `repositories` (array, optional)

Extra package sources. Required for private or unregistered packages. When cleo encounters a package name not in the default registry, it searches this list.

Each entry:

```json
{ "type": "git", "url": "https://github.com/vendor/package" }
```

Only `"type": "git"` is supported in v1. The `url` must be a valid git remote.

### `require` (object)

Packages installed for the whole team. Files land in `.claude/` and are committed to git. Teammates get them on next pull + `cleo install`.

Keys are `<vendor>/<name>` strings. Values are semver constraints:

| Constraint | Meaning |
|---|---|
| `*` | any version |
| `1.2.3` | exact version |
| `^1.2.3` | >=1.2.3 <2.0.0 |
| `~1.2.3` | >=1.2.3 <1.3.0 |
| `>=1.0` | at least 1.0 |
| `>=1.0 <2.0` | range (space-separated AND) |

### `require-local` (object)

Same constraint format. Installed into `.claude/rules/local/` (gitignored). Only the current developer on this repo. Rules only — skills/agents/commands require top-level `.claude/` placement to be auto-discovered by Claude Code.

### `require-user` (object)

Installed into `~/.claude/` (out-of-tree). Available to the current user across all repos on this machine. Supports rules, skills, agents, and commands.

## Minimal example

```json
{
  "name": "my-project",
  "repositories": [
    { "type": "git", "url": "https://github.com/acme/cleo-example" }
  ],
  "require": {
    "acme/cleo-example": "^1.0"
  }
}
```

## Scaffold

Run `cleo init` (or `/cleo-install` with no manifest present) to generate a starter file.
