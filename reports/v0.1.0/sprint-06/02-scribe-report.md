---
agent: scribe
date: 2026-10-17
sprint: 06
version: v0.1.0
topic: open-source documentation for skyferry
status: DONE (one manual action pending, see below)
---

# Scribe report: open-source documentation

## Summary

The public-facing documentation for the GitHub release (`cubetribe/skyferry`, MIT) was written in English, with a German setup guide. All statements were checked against the code: CLI subcommands and flags in `skyferry/cli.py`, configuration keys in `skyferry/config.py` and `config.example.toml`, state names in `skyferry/state.py`, the safety net in `skyferry/rclone.py`, the rename logic in `skyferry/source.py`, notification behaviour in `skyferry/notify.py`, and the setup scripts in `setup/` and `deploy/`.

No personal data is in the new files: no e-mail addresses, IP addresses, VPN names, device or volume names, or hostnames. The server is always `<server>`, and the German guide does not address anyone.

## Pending manual action (required before publishing)

- **`docs/EINRICHTUNG.md` must be removed with `git rm docs/EINRICHTUNG.md`.** I had no shell and could not run it, and I did not overwrite the file. The file still contains personal data (an e-mail address, a VPN provider, a volume name and a hostname). It must not be published. Its replacement is `docs/de/EINRICHTUNG.md`.

## Files changed

| File | Action |
|---|---|
| `README.md` | Replaced: English, badges, Mermaid pipeline, safety guarantees, quickstart, CLI table, configuration highlights, notifications and privacy, architecture module map, status and roadmap, FAQ, contributing, security, license |
| `docs/SETUP.md` | Created: English step-by-step server setup, including the OAuth "publish app" warning, ntfy, healthchecks (period 5 min, grace 30 min), buffer disk, Selective Sync, troubleshooting |
| `docs/de/EINRICHTUNG.md` | Created: German version of the setup guide, generic |
| `docs/EINRICHTUNG.md` | Not deleted. Manual `git rm` required (see above) |
| `CONTRIBUTING.md` | Created: dev setup, tests (`python3 -m unittest discover -s tests -v`), code style, mandatory gitleaks hook via `scripts/install-git-hooks.sh`, PR rules (`tests` and `secret-scan` required, merges blocked otherwise) |
| `SECURITY.md` | Created: private reporting through GitHub Security Advisories, scope, secret handling (`rclone.conf`, `config.toml` at mode 0600, ntfy topic, healthchecks URL) |
| `CHANGELOG.md` | Converted to English, Keep a Changelog format, `[Unreleased]` kept. Rewritten entry for skyferry (formerly `gdrive2sync`), and an entry for the open-source release files |

Not touched, as requested: `skyferry/**`, `tests/**`, `deploy/**`, `setup/**`, `spike/**`, `.github/**`, `scripts/**`, `LICENSE`, `plans/**`, `reports/**` (except this report), `config.example.toml`. No git commits were made.

## Findings for the Orchestrator

1. **`setup/buffer-disk.sh` header is wrong.** It names `puffer-disk.sh` and copies `setup/` to `~/skyferry-setup/`. The docs use the correct file name `buffer-disk.sh`. Please fix the header in the script (out of my scope).
2. **Python version mismatch.** `deploy/install.sh` accepts Python 3.11 or newer, while the docs state 3.12, the tested version. Either tighten the check to 3.12 or leave it and keep the docs as they are.
3. **Files linked but not yet in the repository.** `LICENSE`, `CONTRIBUTING.md` (now written), `SECURITY.md` (now written), `scripts/install-git-hooks.sh`, `.github/workflows/tests.yml` and `secret-scan.yml`, and the `docs/SETUP.md` link all need to exist at publish time. The README badges point at the two workflow files.
4. **Test count.** The README says 48 tests. I counted 48 `def test_` occurrences in `tests/`, which matches.
5. **Old wording removed.** The old guide referenced `~/.config/gdrive2sync/ntfy-topic`. That path does not exist in the code, so it is no longer mentioned. The topic lives only in `config.toml`.

## Verification performed

- All commands in the README quickstart were checked against `cli.py` and the setup scripts. Subcommands and flags: `plan --dry-run`, `run --once --max-units`, `status --json`, `units --status`, `retry <id> | --failed`, `skip <id> --reason`, `events --unit --tail`, `verify <id> --stage local|remote | --all-done`, `notify-test`, `notify-alarm`. Global `--config` and `-v`.
- Exit codes in the README were checked against `main()` and `run()`: 0 success, 1 rclone or safety error, 2 configuration error or buffer guard with `--once`, 3 instance lock.
- Log and error strings quoted in the troubleshooting tables were checked against `buffer.py`, `runner.py` and `config.py`.
- Badge URLs are exactly the ones given in the brief.

## Verdict

STATUS: DONE
- Docs complete in English and German, verified against the code. No personal data in the new files.
- Manual action required: `git rm docs/EINRICHTUNG.md`. The old file still contains personal data and must not be published.
- `setup/buffer-disk.sh` header names the wrong file. The install script checks for Python 3.11 while the docs say 3.12.
