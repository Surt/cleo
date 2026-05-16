# Changelog

All notable changes to cleo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-16

First public release.

### Commands
- `cleo init` — scaffold a starter `cleo.json`
- `cleo install` — install from `cleo.json` (lock-strict when `cleo.lock` exists)
- `cleo require <vendor/pkg>` — add a package and install it; `--repo` for non-GitHub hosts
- `cleo remove <vendor/pkg>` — uninstall and clean up files, MCP entries, hooks, manifest
- `cleo update [<vendor/pkg>]` — re-resolve within constraints, update lock
- `cleo list` — show installed packages
- `cleo check` — validate manifest, report missing files, detect on-disk drift

### Resolution + buckets
- Semver constraint resolution (`*`, exact, `^`, `~`, `>=`, `<=`, `>`, `<`, ranges)
- Three install buckets:
  - `require` — project, committed, team-wide
  - `require-local` — gitignored, single repo (all artifact types nest under `.claude/<type>/local/`)
  - `require-user` — installed into `~/.claude/`, applies across all your repos (rejects hook-containing packages with a clear error)
- GitHub convention: `vendor/name` resolves to `github.com/vendor/name` automatically
- `repositories` block override for GitLab, private hosts, local paths
- `cleo.json` at package root is recommended but optional — repos without one default to `type: skills-pack` (`mcp-server` / `mixed` packages still need `cleo.json` to declare `type`)

### Lock + cache
- `cleo.lock` — exact version + commit SHA pinning; written on install/require
- Package cache at `~/.claude/cleo/packages/<vendor>/<name>/<version>/`
- Cache integrity: verifies cached `HEAD` against lock commit and re-clones on mismatch

### Wiring
- MCP server wiring: reads `mcp.json` template, merges into `settings.json` `mcpServers`
- Hook registration: copies scripts to `.claude/hooks/`, registers in `settings.json`
- File collision prefix: `cleo-<vendor>-<pkg>-<item>` so managed files never collide with hand-written ones

### Plugin
- Claude Code plugin with slash commands: `/cleo-install`, `/cleo-require`, `/cleo-remove`, `/cleo-update`, `/cleo-list`

### Cross-platform
- UTF-8 stdout/stderr forced at startup so non-ASCII status chars don't crash cp1252 consoles on Windows
- `--project` flag accepted either before or after the subcommand
- `shutil.rmtree` retries with `chmod +w` on read-only `.git/` files during cache invalidation

### Tests + CI
- 90 tests covering semver, manifest I/O, frontmatter checks, and full CLI regression suite (`tests/test_cli.py`)
- CI matrix: Python 3.9 / 3.11 / 3.12 × Linux / Windows

### License
- GPL-3.0-or-later. SPDX identifier in `tools/cleo.py`. Copyright © 2026 Erik Wiesenthal.

[Unreleased]: https://github.com/Surt/cleo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Surt/cleo/releases/tag/v0.1.0
