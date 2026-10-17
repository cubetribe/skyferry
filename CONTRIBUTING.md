# Contributing to skyferry

Thank you for considering a contribution. skyferry moves people's data, so it is deliberately conservative. The rules below keep it that way and keep reviews fast.

## Ways to help

- **Sync.com target.** The biggest open piece. Ideas and experiments for Plan A (headless Windows client under Wine) and Plan B (web-panel automation) are very welcome, even as a write-up before any code.
- **English localisation.** CLI output and notification texts are currently German. Good first issue.
- **Real-world reports.** Google Docs listing shape, folder-ID preservation on rename, and `--fast-list` memory at terabyte scale are still unverified. Reports with anonymised numbers help a lot.
- **Bugs and docs.** Anything unclear in the README or the setup guides is a bug too.

For anything larger than a small fix, please open an issue first so we can agree on the approach.

## Development setup

You need:

- Python 3.12
- rclone 1.75 or newer on your `PATH`
- Git, and the secret-scan hook described below

```bash
git clone https://github.com/cubetribe/skyferry.git
cd skyferry
python3 -m skyferry --help        # quick smoke test, runs from the repository root
```

skyferry has no runtime dependencies to install. Do not add any (see the code style below).

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

The integration tests call the real rclone binary with a local folder standing in for Google Drive. They need `rclone` on your `PATH`. Without it, those tests are skipped.

The suite covers the engine end to end without network access, a kill-and-resume run, corruption detection, configuration validation, notifications and the rclone safety net. If you change behaviour, add a test that would have failed before your change.

## Code style

- **Standard library only.** No third-party packages in `skyferry/`. Discuss in an issue before proposing an exception.
- **Type hints** on all function signatures, using `from __future__ import annotations`.
- **English identifiers**, comments and docstrings. Existing user-facing strings are German for now; new user-facing strings may be written in English if they fit the localisation work.
- Keep functions small and side effects visible. Destructive operations belong behind the existing guards (`Buffer.remove_dir`, `Rclone.check`), not beside them.
- Keep the diff focused. One concern per pull request.

If you touch the safety net in `skyferry/rclone.py`, the buffer guard in `skyferry/buffer.py`, or the rename logic in `skyferry/source.py`, expect a closer review. Those are the parts that protect people's data.

## Secret scanning (mandatory)

Secrets must never reach the repository. Credentials, tokens, ntfy topics and healthchecks URLs all count. CI scans every push with gitleaks, and GitHub push protection is enabled, but catching a secret before it is committed is far better than rewriting history afterwards.

Install the local pre-commit hook once after cloning:

```bash
scripts/install-git-hooks.sh
```

The script installs a gitleaks pre-commit hook. Install [gitleaks](https://github.com/gitleaks/gitleaks) first, the script tells you if it is missing. Do not bypass the hook with `--no-verify`. If the hook reports a false positive, open an issue or add a narrowly scoped allowlist entry in the same pull request and explain it.

## Pull requests

- Fork, branch from `main`, and open a pull request against `main`.
- **CI must be green.** The required checks are `tests` (the unit and integration suite) and `secret-scan` (gitleaks).
- **Merges to `main` are blocked** unless both checks pass. This is enforced by branch protection, not just by convention.
- Describe what changed and why, and how you tested it. For behaviour changes in the state machine, say which states and transitions are affected.
- Keep the README and the setup guides accurate. If you change a command, flag or configuration key, update `docs/SETUP.md`, `docs/de/EINRICHTUNG.md` and the README in the same pull request.
- Add a line to the `[Unreleased]` section of `CHANGELOG.md` for user-visible changes.

## Commit messages

Short imperative titles are appreciated, for example `Add retry for transfer limits`. Conventional Commit prefixes such as `fix:` or `feat:` are welcome too.

## Conduct

Be kind, be specific, and assume good faith. Criticise code, not people.
