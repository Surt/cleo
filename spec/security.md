# cleo security model

cleo fetches code from git and copies it into your project. This doc lists the checks that run on every install — and the ones cleo deliberately leaves to you.

If you've ever asked "is this safe to run on my laptop?", start here.

## What cleo guards against

| Risk | What cleo does |
|---|---|
| A package name like `../../etc/passwd` escaping into your filesystem | Names must match `vendor/name` with lowercase letters, digits, `._-` — nothing else |
| A package shipping a symlink that points at `/root/.ssh/id_rsa` | Every file's real path must stay inside the package; symlinks pointing outside are refused |
| A malicious tag or URL beginning with `-` tricking `git` into running a flag | Every value going to `git` is checked, and call sites use `--` to separate args |
| A package whose `cleo.json` is a symlink to one of your host files | Manifest files are refused if they're symlinks |
| A "package" that's actually empty or the wrong repo | Install fails if the package contributes no skills/rules/agents/commands/hooks/MCP config |
| A hook script the size of a binary blob | Hooks are capped at 64 KiB |
| A package manifest with an unknown `type` or malformed `name` | Manifest is rejected |
| Special files (FIFO, socket, device) hiding inside a package | Only regular files and directories are copied |

The full implementation is in [`tools/lib/security.py`](../tools/lib/security.py) — short, pure, no I/O beyond the paths the caller hands it. Each check raises a clear error; cleo turns it into a CLI message.

## What cleo does **not** do

Be honest about the edges so you can decide what to layer on top:

- **No runtime sandbox.** Once a skill, hook, agent, or MCP server is installed under `.claude/`, it runs under Claude Code's permission model — not cleo's. Review what you install the way you'd review a dependency's source.
- **No cryptographic signing.** cleo pins the commit SHA git reports at install time. It doesn't verify signatures against a separate trust root.
- **No content scanning.** cleo is a packager, not an antivirus. It doesn't grep skill or hook bodies for credentials, `eval`, shell escapes, or known-bad patterns.
- **No central registry.** Anyone can publish; the URL is the identity. There's no "verified author" badge, because there's no authority issuing one.
- **No network policy.** cleo shells out to `git`. Whether `git` can reach the remote (firewall, proxy, offline) is your environment's concern, not cleo's.

## What you trust when you `cleo require <pkg>`

Same trust model as `git clone` + `pip install`:

- **Identity is the URL.** `vendor/name` resolves to `https://github.com/vendor/name` by default. You can override with `--repo` or `repositories[]` in `cleo.json` (GitLab, private hosts, local paths).
- **Pinning is the commit SHA in `cleo.lock`.** Once a version is resolved, every subsequent `cleo install` checks out that exact commit. Lock drift requires `cleo update`, which re-resolves and rewrites the lock — visible in your diff.
- **Review is on you.** Before adding a package to `cleo.json`, read its `cleo.json`, hooks, and skills the same way you'd read a dependency's source. cleo will refuse the obvious traps; it won't read intent.

## Reporting a vulnerability

Found a way around any of the above? Open an issue at [github.com/Surt/cleo/issues](https://github.com/Surt/cleo/issues). For sensitive reports, email the maintainer listed in the repo directly.
