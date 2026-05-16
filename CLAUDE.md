# cleo

Dependency manager for the Claude ecosystem — rules, skills, agents, commands, hooks, MCP servers.

## Repo layout

```
.claude-plugin/marketplace.json   ← marketplace index (lists the cleo plugin)
plugin/                           ← the installable Claude Code plugin
  commands/                       ← /cleo-install /cleo-require /cleo-update /cleo-list /cleo-remove /cleo-find
  settings.json                   ← permission allowlists
tools/
  cleo.py                         ← standalone CLI engine
  lib/
    semver.py                     ← semver parsing + constraint resolution
    checks.py                     ← frontmatter validation
spec/                             ← format specs (cleo.json, cleo.lock, package-format)
cleo                              ← bash wrapper for standalone CLI
cleo.cmd                          ← Windows wrapper
```

## Running the engine

```bash
python3 tools/cleo.py install --project /path/to/project
python3 tools/cleo.py require vendor/pkg --repo https://... --project /path/to/project
python3 tools/cleo.py require vendor/pkg                        # github shorthand
python3 tools/cleo.py require ./local-skills                    # local path
python3 tools/cleo.py update --adopt                            # adopt skills installed by other tools
python3 tools/cleo.py list
```

Or via the wrapper (after making it executable / adding to PATH):

```bash
./cleo install
./cleo require vendor/pkg --repo https://...
./cleo add vendor/pkg                    # alias for require
./cleo ls                                # alias for list
./cleo rm vendor/pkg                     # alias for remove
./cleo find <query>                      # search installed packages
```

## Plugin commands

`/cleo-install`, `/cleo-require` (alias `/cleo-add`), `/cleo-update`, `/cleo-list` (alias `/cleo-ls`), `/cleo-remove` (alias `/cleo-rm`), `/cleo-find`

All delegate to `tools/cleo.py` in the marketplace clone (`~/.claude/plugins/marketplaces/cleo/`).

## Contributing

### Adding features to the engine

- `tools/cleo.py` contains all subcommand logic
- `tools/lib/semver.py` — no third-party deps; stdlib only
- `tools/lib/checks.py` — frontmatter validation (PyYAML required)

### Adding features to the commands

Commands are Markdown files with YAML frontmatter in `plugin/commands/`. They describe behavior — Claude Code interprets and executes them.

### Authoring rules for `plugin/settings.json`

Add permission allowlists for any new subprocess calls the engine makes (git, python3, npx). Format: `"Bash(command pattern*)"`.

## Requirements

- Python 3.9+
- `git`
- `pip install pyyaml`

No other runtime dependencies.
