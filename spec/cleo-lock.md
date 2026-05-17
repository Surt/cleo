# cleo.lock format

`cleo.lock` is the generated lock file. It pins exact versions and commit SHAs so every developer and CI run gets identical output from `cleo install`. Commit this file alongside `cleo.json`.

## Schema

```json
{
  "version": 1,
  "generated": "2026-05-11T12:00:00Z",
  "packages": {
    "vendor/package": {
      "type": "bundle",
      "url": "https://github.com/vendor/package",
      "version": "1.2.0",
      "commit": "abc123def456abc123def456abc123def456abc1",
      "bucket": "project",
      "mcp_server_key": null,
      "items": [
        {
          "type": "rule",
          "name": "my-rule",
          "path": ".claude/rules/cleo-vendor-package-my-rule.md",
          "sha": "hexdigest..."
        }
      ]
    }
  }
}
```

## Fields

### `version` (integer)

Lock file format version. Currently always `1`.

### `generated` (string)

ISO 8601 timestamp of last `cleo install` or `cleo update` run.

### `packages` (object)

Keyed by `<vendor>/<name>`. Each entry:

#### `type`

`"bundle"` | `"mcp-server"` | `"mixed"` — matches the package's own `cleo.json`.

#### `url`

Git remote URL the package was fetched from.

#### `version`

Resolved semver version string (e.g. `"1.2.0"`).

#### `commit`

Full git commit SHA the version tag resolves to.

#### `bucket`

`"project"` | `"local"` | `"user"` — which install bucket was used.

#### `mcp_server_key`

For `mcp-server` / `mixed` packages: the key added to `mcpServers` in `settings.json`. `null` for skills-only packages.

#### `install_mode`

`"copy"` (default) | `"symlink"` — how artifacts were materialized into `.claude/...`:

- `"copy"` — files are copied from the local cache; cache updates do not affect the installed files until `cleo update` is run.
- `"symlink"` — files are symlinked from the local cache; cache updates are immediately live without re-running install.

**Backward compat:** omitted from lock files written by cleo versions before this field was introduced; treated as `"copy"` on read. Also omitted on write when the value equals the default `"copy"`.

**Override:** `cleo install --symlink` overrides the locked value for that run. The overriding mode is then written back to the lock so subsequent `cleo install` runs restore the same mode.

#### `items`

Array of installed artifacts:

- `type` — `"rule"` | `"skill"` | `"agent"` | `"command"` | `"hook"`
- `name` — item name (filename stem or skill directory name)
- `path` — absolute path where the item was materialized
- `sha` — SHA256 of the installed file/directory at install time (drift detection)

## Merge conflicts

Lock file entries are sorted alphabetically by package name. `commit` and `sha` are deterministic given the same upstream state. Conflicts are rare and resolved by accepting the newer version:

```bash
git checkout --theirs cleo.lock
cleo install --offline  # re-materialize from cached packages
```
