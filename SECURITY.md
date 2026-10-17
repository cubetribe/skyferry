# Security policy

## Reporting a vulnerability

Please **do not** open a public issue, pull request or discussion for a security problem.

Report it privately through GitHub's private vulnerability reporting:

1. Go to the [Security tab](https://github.com/cubetribe/skyferry/security) of the repository.
2. Choose **Report a vulnerability** (Security Advisories).
3. Describe the problem, the affected version or commit, and steps to reproduce. A proof of concept is helpful, but please do not include real credentials or real data in it.

The maintainer receives the report privately. We will confirm receipt, discuss the fix with you in the advisory, and credit you in the release notes if you wish.

## Scope

In scope:

- The code in this repository: the rclone wrapper and its safety net, the buffer guard, the rename logic, verification, state handling, notifications and the deployment scripts.
- Anything that could cause data loss on the source (Google Drive) or the buffer, or leak secrets or folder and file names.

Out of scope:

- Vulnerabilities in rclone, Google Drive, ntfy, healthchecks.io, Wine or other third-party software. Please report those to their maintainers.
- Problems that need an attacker who already has shell access to the server with the same user account.
- Misconfiguration on your own machine, such as a world-readable config file you created yourself.

## Supported versions

skyferry is in alpha (v0.1). Only the latest release and the `main` branch receive security fixes.

## How secrets are handled

skyferry is designed so that credentials never enter the repository and never leave the server unless you configure it.

- **rclone remote and token:** stored only on the server in `~/.config/rclone/rclone.conf`, mode `0600`.
- **skyferry configuration:** `~/.config/skyferry/config.toml`, mode `0600`. It contains the ntfy topic (or token) and the healthchecks.io ping URL. The file is created with these permissions by `deploy/install.sh`.
- **Git:** `config.toml`, `rclone.conf`, `.env`, `*.token`, state databases and logs are listed in `.gitignore`.
- **Notifications:** by default (`notify.include_paths = false`) no folder or file names are sent to ntfy or healthchecks.io. Only unit numbers, sizes, statuses and error classes are sent.
- **Repository guards:** a gitleaks scan runs in CI on every push (`secret-scan` workflow). GitHub push protection is enabled. A local gitleaks pre-commit hook is installed with `scripts/install-git-hooks.sh` (see [CONTRIBUTING.md](CONTRIBUTING.md)).

Treat the ntfy topic and the healthchecks.io ping URL as secrets. Anyone who knows the ntfy topic can read your notifications, and anyone who knows the ping URL can send false heartbeats. If you believe either leaked, choose a new random ntfy topic, create a new healthchecks.io check, and update the configuration on your server.

If you accidentally commit a secret, do not just delete it in a new commit. Revoke or rotate it first, then remove it from the history.
