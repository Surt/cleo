# cleo package format

Any git repository with at least one semver tag and a recognized artifact directory (`rules/`, `skills/`, `agents/`, `commands/`, `hooks/`, or `mcp.json`) is a cleo package. No registration required for URL-based installs.

A `cleo.json` at the root is **recommended** (declares `type`, enables `mcp-server` / `mixed` packages, and gives consumers metadata to read), but optional. Repos without one are treated as `type: skills-pack` with no extra metadata.

## Based on Claude Code's official surfaces

cleo doesn't invent its own artifact formats — it packages and installs the same `.claude/` content Claude Code already understands. Each directory maps to a first-class Claude Code concept; follow the linked docs for frontmatter fields, lifecycle, and runtime behavior.

| Directory   | Claude Code concept                                          | Docs                                                                  |
|-------------|--------------------------------------------------------------|-----------------------------------------------------------------------|
| `rules/`    | Memory rules (CLAUDE.md / `.claude/rules/`)                  | [Memory](https://code.claude.com/docs/en/memory)                      |
| `skills/`   | Skills (`SKILL.md` directories)                              | [Skills](https://code.claude.com/docs/en/skills)                      |
| `agents/`   | Subagents                                                    | [Subagents](https://code.claude.com/docs/en/sub-agents)               |
| `commands/` | Slash commands (legacy form; equivalent to skills in modern Claude Code) | [Skills](https://code.claude.com/docs/en/skills)         |
| `hooks/`    | Tool-event hooks                                             | [Hooks](https://code.claude.com/docs/en/hooks)                        |
| `mcp.json`  | MCP server config                                            | [MCP](https://code.claude.com/docs/en/mcp)                            |

cleo's job stops at "fetch from git, lay out under `.claude/` with collision-safe names". The semantics — what counts as a valid frontmatter field, when Claude loads a skill, how hooks fire — come from the upstream docs.

## Directory layout

```
my-package/                    ← git repository root
├── cleo.json                  ← recommended: package metadata
├── rules/
│   ├── my-rule.md             ← Claude rules (auto-installed to .claude/rules/)
│   └── another-rule.md
├── skills/
│   └── my-skill/              ← skill directory
│       └── SKILL.md
├── agents/
│   └── my-agent.md
├── commands/
│   └── my-command.md
├── hooks/
│   └── pre-tool-use.sh        ← registered in settings.json hooks config
├── mcp.json                   ← optional: MCP server config template
└── README.md
```

Only the directories you need. A package with only `rules/` is perfectly valid.

## Package `cleo.json`

```json
{
  "name": "vendor/my-package",
  "type": "skills-pack",
  "version": "1.0.0",
  "description": "Short description of what this package provides",
  "homepage": "https://github.com/vendor/my-package"
}
```

### Fields

| Field | Required | Description |
|---|---|---|
| `name` | recommended | `<vendor>/<name>` — must be unique across packages users install together |
| `type` | recommended | `skills-pack` \| `mcp-server` \| `mixed`. Defaults to `skills-pack` if absent. `mcp-server` / `mixed` require this field (cleo only wires MCP servers when `type` declares it). |
| `version` | recommended | Current semver version (informational — cleo uses git tags) |
| `description` | recommended | One-line description |
| `homepage` | optional | Link to docs or repo |

> **Note.** If `cleo.json` is missing, cleo treats the repo as `type: skills-pack` and installs whatever artifact directories it finds. Any `mcp.json` present will be ignored (no `type` to declare `mcp-server` / `mixed`).

## Versioning

Tag your releases with git semver tags:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Both `v1.0.0` and `1.0.0` formats are recognized. Use `v` prefix by convention.

## Rules format

Rules are `.md` files with YAML frontmatter:

```markdown
---
name: my-rule-name
description: What this rule does and when it applies (20–160 chars)
scope: generic
paths:
  - "**/*.php"
---

Rule body here. Be specific and actionable. Avoid vague directives
like "be careful" or "use good practices".
```

Frontmatter fields:
- `name` — kebab-case, unique within the package
- `description` — used in search and list output
- `scope` — `generic` | `lang-python` | `framework-django` | etc. (informational)
- `paths` — glob patterns limiting when the rule loads (optional)
- `requires` — list of other packages this rule needs, e.g. `["vendor/other-pkg"]`

## Skills format

Skill directories must contain `SKILL.md`:

```markdown
---
name: my-skill
description: >
  Use this skill when the user asks to scaffold a new resource.
  Trigger: scaffold, generate, create.
scope: generic
---

Skill instructions here.
```

## MCP server packages

If `type` is `mcp-server` or `mixed`, include an `mcp.json`:

```json
{
  "command": "npx",
  "args": ["-y", "@vendor/mcp-server"],
  "env": {
    "API_BASE_URL": "${API_BASE_URL}",
    "API_TOKEN": "${API_TOKEN}"
  }
}
```

`${VAR}` placeholders are prompted at install time. Vars already in the environment are used automatically.

## Hooks

Shell scripts in `hooks/` are copied to `.claude/hooks/cleo-<vendor>-<pkg>/` and registered in `settings.json`. The script filename (without `.sh`) is used as the hook event name.

```bash
hooks/
├── PreToolUse.sh       ← registered as PreToolUse hook
└── PostToolUse.sh      ← registered as PostToolUse hook
```

## Publishing

1. Create a public (or private) git repository
2. Add your artifacts following the layout above
3. Tag a release: `git tag v1.0.0 && git push origin v1.0.0`
4. Share the repo URL — users pass it via `--repo` or add it to their `repositories`

Future: register at `cleo.dev` so users can install by name without `--repo`.

## Authoring guidelines

- Keep rules **specific and actionable** — no "be careful", no "use good practices"
- Keep rule bodies under 200 lines
- Keep skill bodies under 300 lines
- No hardcoded credentials, user paths, or team-specific ticket references
- Use `paths:` to scope rules to relevant file types
- Declare cross-package dependencies in rule frontmatter `requires:`
