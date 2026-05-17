# Changelog

All notable changes to cleo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] — 2026-05-17

### Added
- `cleo publish` — refresh a package's `cleo.json`, validate it, and optionally `--bump` / `--commit` / `--tag` / `--push` / `--release`
- `/cleo-publish` slash command
- `cleo update --adopt` — register skills installed by other tools (via `file://` or git remote) into `cleo.json` + `cleo.lock`
- `cleo update --scope` — limit adoption scan to a single bucket
- `cleo find <query>` — local substring search over installed packages (name, items, descriptions)
- `/cleo-find` slash command
- `add` / `ls` / `rm` aliases for `require` / `list` / `remove` (CLI + slash commands)
- `--symlink` flag on `install` and `require` — install artifacts as symlinks into the package cache; atomic replacement; copy fallback when symlinks unsupported
- `install_mode` field in `cleo.lock` (`copy` default, `symlink` when chosen)
- Local-path source form for `require` (no clone)
- Subdirectory source form via git sparse-checkout
- Positional `source` argument on `require` (URL or shorthand)
- Source-form parser supporting six shapes (vendor/pkg, full URL, local path, subdir, etc.)
- Security spec at `spec/security.md`, linked from README

### Changed
- Package type renamed `package` → `bundle`
- Mixed-case package refs accepted at every entry point; normalized to lowercase
- README rewrites surface full artifact + scope coverage upfront; vercel-labs/skills migration section added

### Fixed
- `cleo update`: preserve `install_mode` when re-resolving a package
- `cleo update --adopt`: validate git remote URL before persisting to lock
- `cleo update --adopt`: strip leading dashes/dots from synthesized package names
- `cleo require --dry-run`: honored for the local-path source form
- `cleo install`: atomic symlink replacement when a symlink already exists at the destination
- `cleo publish`: derive `homepage` host from the git remote URL

## [0.2.0] — 2026-05-16

Phase 1 security firewall. Every package ref, manifest, artifact path, and hook script now passes through hard gates before any filesystem write.

### Added — security gates
- `validate_package_ref` — reject path traversal, leading dashes, and invalid characters at every CLI and manifest entry point
- `validate_dest_item_name` — path-escape guard for destination filenames
- `validate_package_manifest` — reject malformed `cleo.json` at install time
- `validate_git_ref` — block argument injection in git invocations; `--` separators added to all git calls
- Symlink-escape rejection — refuse symlinks pointing outside the package cache (covers hooks too)
- Symlinked `cleo.json` and `mcp.json` refused
- Non-regular artifact sources rejected (FIFOs, devices, etc.)
- 64 KiB cap on hook scripts
- Packages shipping zero artifacts rejected
- Four hard gates documented in `spec/security.md`

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
- `cleo.json` at package root is recommended but optional — repos without one default to `type: bundle` (`mcp-server` / `mixed` packages still need `cleo.json` to declare `type`)

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

[Unreleased]: https://github.com/Surt/cleo/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Surt/cleo/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Surt/cleo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Surt/cleo/releases/tag/v0.1.0
