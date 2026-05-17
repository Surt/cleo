# cleo

[![CI](https://github.com/Surt/cleo/actions/workflows/ci.yml/badge.svg)](https://github.com/Surt/cleo/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

**npm · pip · composer · cargo — for everything that goes in `.claude/`.**

cleo is a dependency manager for the Claude ecosystem. One manifest, one command, and your whole team gets the same setup:

- **Rules** — `CLAUDE.md` / `.claude/rules/`
- **Skills** — `SKILL.md` directories
- **Agents** — subagent definitions
- **Slash commands**
- **Hooks** — tool-event scripts
- **MCP server configs**

Each package installs into one of three scopes:

- **Project** — committed to the repo, shared with your whole team
- **Local** — gitignored, just you in this repo
- **User** — out-of-tree (`~/.claude/`), just you across every repo on this machine

```bash
cleo require acme/cleo-example        # fetches from github.com/acme/cleo-example
cleo install                          # install everything from cleo.json (lock-strict)
cleo update                           # update to latest matching versions
cleo remove acme/cleo-example         # uninstall + clean up
```

No registration. No central server. `vendor/name` resolves to `github.com/vendor/name` automatically.

The README has two halves: [**Use cleo**](#use-cleo-install-packages-into-your-project) (consume packages in your project) and [**Publish a cleo package**](#publish-a-cleo-package) (author and share your own).

---

## Install cleo

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

## Use cleo (install packages into your project)

### Add your first package

The fastest path is to ask cleo to do it. From inside a Claude Code session:

```
/cleo-require acme/cleo-example
```

Or from the terminal:

```bash
cleo require acme/cleo-example          # latest matching tag
cleo require acme/cleo-example@^1.0     # with a version constraint
cleo require acme/cleo-example --local  # gitignored, this repo only
```

On first run, cleo scaffolds `cleo.json` for you if none exists, resolves the latest matching tag, fetches the package, copies its content into `.claude/`, and writes `cleo.lock`. After that:

```bash
cleo update     # bump matching versions
cleo list       # see what's installed
cleo remove acme/cleo-example
```

Teammates clone the repo + run `cleo install` (or `/cleo-install`) and get the exact same state from `cleo.lock`.

### Your `cleo.json`

`cleo require` writes one for you, but here's what it looks like (and what you'd edit by hand). Full schema: [`spec/cleo-json.md`](spec/cleo-json.md).

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

Three buckets. All three are optional:

| Key | Where files land | Committed? | Scope |
|---|---|---|---|
| `require` | `.claude/` | yes | whole team |
| `require-local` | `.claude/<type>/local/` | no (gitignored) | you, this repo |
| `require-user` | `~/.claude/` | no (out-of-tree) | you, all repos on this machine |

Use `repositories` to override the GitHub convention for specific packages (GitLab, private hosts, local paths):

```json
{
  "repositories": [
    { "type": "git", "url": "https://gitlab.com/myorg/private-rules" }
  ],
  "require": {
    "myorg/private-rules": "*"
  }
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

### Coming from `vercel-labs/skills`?

cleo is a drop-in replacement. Nothing you've installed gets thrown away, no commands need to be re-learned, and switching back is one `git checkout` away.

- **Your existing skills stay put.** `cleo update --adopt` scans `.claude/skills/` (project) and `~/.claude/skills/` (global), finds the directories you installed with `npx skills`, and registers them in `cleo.json` + `cleo.lock`. Nothing is moved, copied, or rewritten. Add `--scope project|global|both` to narrow it, `--dry-run` to preview.
- **Same source forms.** Every URL or path you'd hand `npx skills` works with `cleo require` (table below).
- **Same `--symlink` mode.** `cleo require <src> --symlink` links from the package cache instead of copying — same dev-loop ergonomics.
- **Files land in the same place.** Skills under `.claude/skills/`, rules under `.claude/rules/`, etc. — cleo doesn't invent a new layout. The only difference is the `cleo-<vendor>-<pkg>-` prefix on installed names so cleo can tell its files apart from yours.
- **No lock-in.** `cleo remove` uninstalls cleanly. Delete `cleo.json` + `cleo.lock` and you're back to whatever you had before.
- **What you gain.** Teammates run `cleo install` and get the same state. Version constraints, lock file, and three scope buckets (project / gitignored / user-global) replace ad-hoc `git clone` + manual copy.

`cleo require` accepts:

| Form | Example |
|---|---|
| GitHub shorthand | `cleo require acme/cleo-example` |
| Full git URL | `cleo require https://github.com/acme/cleo-example` |
| GitHub subdir (one folder of a repo) | `cleo require https://github.com/vercel-labs/skills/tree/main/skills/playwright` |
| GitLab URL | `cleo require https://gitlab.com/org/repo` |
| SSH URL | `cleo require git@github.com:acme/cleo-example.git` |
| Local path | `cleo require ./my-skills` |

**Symlink mode.** `cleo require <src> --symlink` links from the package cache instead of copying — handy when authoring a skill in another working tree (mirrors `npx skills --symlink`).

**Adopt skills already on disk.** If `.claude/skills/` has SKILL.md directories cleo doesn't yet track, `cleo update --adopt` registers them into `cleo.json` + `cleo.lock` so they survive a fresh clone. `--scope project|global|both` narrows the scan; `--dry-run` previews the diff.

### Commands

| Command | Description |
|---|---|
| `cleo init` | Scaffold a starter `cleo.json` |
| `cleo install` | Install from `cleo.json` (lock-strict when `cleo.lock` exists) |
| `cleo require <vendor/pkg> [--repo <url>]` | Add a package and install it |
| `cleo remove <vendor/pkg>` | Uninstall — removes files, MCP entries, hooks, manifest entry |
| `cleo update [<vendor/pkg>]` | Re-resolve within constraints, update lock |
| `cleo list` | Show installed packages |
| `cleo check` | Validate manifest, report missing files, detect on-disk drift |

**Claude Code slash commands** — same ops, inside a session: `/cleo-install` · `/cleo-require` · `/cleo-remove` · `/cleo-update` · `/cleo-list`. `/cleo-require` accepts `--repo <url>`.

### Where files land

Each package's content maps directly to Claude Code surfaces:

| In the package | Installed to | Claude Code concept |
|---|---|---|
| `rules/*.md` | `.claude/rules/` | [Memory rules](https://code.claude.com/docs/en/memory) |
| `skills/*/SKILL.md` | `.claude/skills/` | [Skills](https://code.claude.com/docs/en/skills) |
| `agents/*.md` | `.claude/agents/` | [Subagents](https://code.claude.com/docs/en/sub-agents) |
| `commands/*.md` | `.claude/commands/` | Slash commands (legacy form; merged into skills upstream) |
| `hooks/*.sh` | `.claude/hooks/` + `settings.json` | [Tool-event hooks](https://code.claude.com/docs/en/hooks) |
| `mcp.json` | `settings.json` → `mcpServers` | [MCP servers](https://code.claude.com/docs/en/mcp) |

cleo fetches into `~/.claude/cleo/packages/<vendor>/<name>/<version>/` (version-pinned cache), then copies into your project. Installed files are prefixed `cleo-<vendor>-<pkg>-` so they never collide with hand-written ones.

### Lock file (`cleo.lock`)

Generated automatically on `cleo install` / `cleo require`. Pins exact versions and commit SHAs so every developer and CI run gets identical output. **Commit it.** Full schema: [`spec/cleo-lock.md`](spec/cleo-lock.md).

```json
{
  "version": 1,
  "packages": {
    "acme/cleo-example": {
      "type": "bundle",
      "url": "https://github.com/acme/cleo-example",
      "version": "1.2.0",
      "commit": "abc123def456",
      "bucket": "project",
      "items": [...]
    }
  }
}
```

`cleo update` is the only command that changes pinned versions. Everything else honors the lock.

### Security

cleo fetches code from git and copies it into your project. Each install is validated for safe package names, symlinks, git refs, file types, hook size, and manifest shape. It does **not** sandbox installed artifacts, sign packages, or scan content — that's on you and on Claude Code's permission model.

Full threat model + what's out of scope: [`spec/security.md`](spec/security.md).

---

## Publish a cleo package

A cleo package is just a git repo with a semver tag and one or more artifact directories. No registry signup; the URL is the identity.

### Repo layout

```
my-pkg/                  ← git repository root
├── cleo.json            ← recommended: package metadata
├── rules/               ← whichever artifact dirs apply
│   └── my-rule.md
├── skills/
│   └── my-skill/SKILL.md
├── agents/my-agent.md
├── commands/my-command.md
├── hooks/PreToolUse.sh
├── mcp.json             ← only for mcp-server / mixed packages
└── README.md
```

Only the directories you actually need. A package with just `rules/` is valid.

### Your package's `cleo.json` (different from a consumer's)

A **consumer's** `cleo.json` lists what packages to install. A **package's** `cleo.json` describes the package itself.

```json
{
  "name": "myorg/cleo-example",
  "type": "bundle",
  "version": "1.0.0",
  "description": "Short description of what this package provides",
  "homepage": "https://github.com/myorg/cleo-example"
}
```

| Field | Required | Notes |
|---|---|---|
| `name` | recommended | `<vendor>/<name>` |
| `type` | recommended | `bundle` \| `mcp-server` \| `mixed`. Defaults to `bundle` if absent. `mcp-server` / `mixed` *require* this field. |
| `version` | recommended | Informational — cleo uses git tags as the source of truth |
| `description`, `homepage` | optional | Surface in `cleo list`, helps discoverability |

If you omit `cleo.json` entirely, cleo treats the repo as `type: bundle` and installs whatever artifact dirs it finds. `mcp.json` is ignored without an explicit `type`.

### Publish

1. Create a git repo (GitHub default, GitLab/private fine — consumers point `--repo` or `repositories` at it)
2. Add artifact directories + (recommended) a `cleo.json`
3. Tag a release: `git tag v1.0.0 && git push origin v1.0.0`

Done. Anyone can install with `cleo require myorg/cleo-example`.

Full package spec: [`spec/package-format.md`](spec/package-format.md). What cleo refuses to install (hook size limits, symlinks, manifest shape): [`spec/security.md`](spec/security.md).

---

## Versioning

Releases follow [Semantic Versioning](https://semver.org):

- **MAJOR** — breaking changes to `cleo.json` / `cleo.lock` format or CLI interface
- **MINOR** — new commands or package-type support, backward-compatible
- **PATCH** — bug fixes, no interface changes

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Requirements

- Python 3.9+
- `git`
- `pip install pyyaml`

---

## License

Copyright © 2026 Erik Wiesenthal.

Released under [GPL-3.0-or-later](LICENSE).
