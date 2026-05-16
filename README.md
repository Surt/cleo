# cleo

[![CI](https://github.com/Surt/cleo/actions/workflows/ci.yml/badge.svg)](https://github.com/Surt/cleo/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

**npm for MCP servers + Claude skills.**

cleo is a dependency manager for the Claude ecosystem. One manifest file, one command — install rules, skills, agents, commands, hooks, and MCP server configs across your whole team.

> **Status:** early — feedback welcome. API and manifest format may change before `v1.0`.

```bash
cleo require acme/cleo-example        # fetches from github.com/acme/cleo-example
cleo install                          # install everything from cleo.json (lock-strict)
cleo update                           # update to latest matching versions
cleo remove acme/cleo-example         # uninstall + clean up
```

No registration. No central server. `vendor/name` resolves to `github.com/vendor/name` automatically.

---

## Install

**As a Claude Code plugin** (gets you `/cleo-install`, `/cleo-require`, etc.):

```
/plugin marketplace add https://github.com/Surt/cleo
/plugin install cleo@cleo
```

**As a standalone CLI** (works anywhere, no Claude Code needed):

```bash
git clone https://github.com/Surt/cleo
cd cleo && ln -s "$PWD/cleo" /usr/local/bin/cleo   # or add to PATH
pip install pyyaml
```

Windows: add `cleo.cmd` to PATH instead.

---

## cleo.json

Declare your project's dependencies in `cleo.json` at the repo root. Commit it.

```json
{
  "name": "my-project",
  "require": {
    "acme/cleo-generic": "^1.0",
    "acme/cleo-example": "^1.0",
    "acme/cleo-mcp-example": "^2.0"
  },
  "require-local": {
    "myhandle/personal-notes": "*"
  },
  "require-user": {
    "myhandle/global-shortcuts": "^1.0"
  }
}
```

Three buckets: `require` ships with the project (team-wide), `require-local` is gitignored (you, this repo), `require-user` installs into `~/.claude/` (you, every repo). All three are optional.

### Fields

**`name`** — project label (optional, defaults to directory name).

**`require`** — packages installed for the whole team. Files land in `.claude/` and are committed. Teammates get them on `git pull` + `cleo install`.

**`require-local`** — same format, gitignored. Only you, only this repo. Useful for personal reminders or WIP rules.

**`require-user`** — installed into `~/.claude/`, applies to all repos on your machine.

**`repositories`** — override the GitHub convention for specific packages. Use for GitLab, private hosts, or local paths.

```json
{
  "name": "my-project",
  "repositories": [
    { "type": "git", "url": "https://gitlab.com/myorg/private-rules" }
  ],
  "require": {
    "acme/cleo-generic": "^1.0",
    "myorg/private-rules": "*"
  },
  "require-local": {
    "acme/cleo-generic": "^1.0"
  },
  "require-user": {}
}
```

### Version constraints

| Constraint | Meaning |
|---|---|
| `*` | any version |
| `1.2.3` | exact |
| `^1.2.3` | >=1.2.3 <2.0.0 |
| `~1.2.3` | >=1.2.3 <1.3.0 |
| `>=1.0` | at least 1.0 |
| `>=1.0 <2.0` | range |

### URL resolution

cleo resolves `vendor/name` in this order:

1. `--repo <url>` flag (explicit override)
2. Matching entry in `repositories`
3. `https://github.com/vendor/name` (default convention)

---

## Commands

### CLI

| Command | Description |
|---|---|
| `cleo init` | Scaffold a starter `cleo.json` |
| `cleo install` | Install from `cleo.json` (lock-strict when `cleo.lock` exists) |
| `cleo require <vendor/pkg> [--repo <url>]` | Add a package and install it (use `--repo` for GitLab, private hosts) |
| `cleo remove <vendor/pkg>` | Uninstall — removes files, MCP entries, hooks, manifest entry |
| `cleo update [<vendor/pkg>]` | Re-resolve within constraints, update lock |
| `cleo list` | Show installed packages |
| `cleo check` | Validate manifest, report missing files, detect on-disk drift |

### Claude Code slash commands

Same operations, invoked inside a Claude session:

`/cleo-install` · `/cleo-require` · `/cleo-remove` · `/cleo-update` · `/cleo-list`

`/cleo-require` accepts `--repo <url>` for packages not on GitHub.

---

## How it works

Each **cleo package** is a git repo tagged with semver (`v1.0.0`). It can contain:

| Directory | Installs to | Type |
|---|---|---|
| `rules/` | `.claude/rules/` | Claude rules |
| `skills/` | `.claude/skills/` | Claude skills (dir with `SKILL.md`) |
| `agents/` | `.claude/agents/` | Subagent definitions |
| `commands/` | `.claude/commands/` | Slash commands |
| `hooks/` | `.claude/hooks/` + `settings.json` | Shell event hooks |
| `mcp.json` | `settings.json` mcpServers | MCP server config |

cleo fetches packages into `~/.claude/cleo/packages/` (version-pinned cache), then copies files into your project. Installed files are prefixed `cleo-<vendor>-<pkg>-` so they never collide with hand-written files.

---

## cleo.lock

Generated automatically on `cleo install` / `cleo require`. Pins exact versions and commit SHAs.

```json
{
  "version": 1,
  "packages": {
    "acme/cleo-example": {
      "type": "skills-pack",
      "url": "https://github.com/acme/cleo-example",
      "version": "1.2.0",
      "commit": "abc123def456",
      "bucket": "project",
      "items": [...]
    }
  }
}
```

Commit `cleo.lock` — it makes `cleo install` deterministic. `cleo update` is the only command that changes pinned versions.

---

## Install buckets

Mirrors Claude Code's own settings layering:

| Key in cleo.json | Where files land | Committed? | Scope |
|---|---|---|---|
| `require` | `.claude/` | yes | whole team |
| `require-local` | `.claude/<type>/local/` (rules, skills, agents, commands) | no (gitignored) | you, this repo |
| `require-user` | `~/.claude/` | no (out-of-tree) | you, all repos |

---

## Publishing a package

1. Create a GitHub repo (e.g. `github.com/myorg/cleo-example`)
2. Add a `cleo.json` at the root declaring `name`, `type`, `version`
3. Add your rules/skills/agents/commands/hooks
4. Tag a release: `git tag v1.0.0 && git push origin v1.0.0`

Done. Anyone can install it with `cleo require myorg/cleo-example`.

See [`spec/package-format.md`](spec/package-format.md) for the full spec.

---

## Releases

Install from source — `git clone` + `pip install pyyaml`. See [Install](#install) above.

Releases follow [Semantic Versioning](https://semver.org):

- **MAJOR** — breaking changes to `cleo.json`/`cleo.lock` format or CLI interface
- **MINOR** — new commands or package-type support, backward-compatible
- **PATCH** — bug fixes, no interface changes

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Requirements

- Python 3.8+
- `git`
- `pip install pyyaml`

---

## License

Copyright © 2026 Erik Wiesenthal.

Released under [GPL-3.0-or-later](LICENSE).
