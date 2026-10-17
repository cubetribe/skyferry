---
agent: ci-security-guardian
date: 2026-10-17
sprint: 06
version: v0.1.0
status: BLOCKED → resolved by orchestrator
persisted_by: orchestrator (agent is read-only)
---

# Sprint 06: CI and security review before the public release

## Verdict

BLOCKED. There was a leak in the unpublished history, and the canary self-test could pass without testing anything. The workflows were otherwise sound.

## Findings and resolution

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | BLOCKER | The first local commit contained private infrastructure details in `plans/v0.1.0/PLAN.md` and `docs/EINRICHTUNG.md`: a LAN IP, the VPN provider, a personal volume name and a personal email. | The working tree was cleaned, `docs/EINRICHTUNG.md` was removed in favour of a generic `docs/de/EINRICHTUNG.md`, and the unpublished history was rebuilt from the clean tree before the first push. |
| 2 | BLOCKER | The old `docs/EINRICHTUNG.md` was still in the working tree. | Removed with `git rm`. |
| 3 | WARNING | The canary self-test passed on any non-zero exit, including config or flag errors, and covered only 2 of the 5 custom rules. | It now uses `--exit-code 42` with exact rc matching, a JSON report with a RuleID assertion per canary, all 5 rules covered and a negative control (clean file → rc 0). The real scan maps rc 0/42/other explicitly. Verified locally. |
| 4 | WARNING | A PR could loosen `.gitleaks.toml` for its own scan. | PR scans use the base branch's `.gitleaks.toml`. CODEOWNERS covers `/.github/`, `/.gitleaks.toml` and `/scripts/`. Fork PR workflows need approval (repo setting). |
| 5 | WARNING | `cancel-in-progress` could cancel scans on main. | It is now `${{ github.ref != 'refs/heads/main' }}` in both workflows. |
| 6 | WARNING | The hostname `homelab01`, the first name and the "2-TB disk" appear in plans and reports. | Accepted. A hostname and the author's name are not sensitive. |
| 7 | WARNING | The author identity in the history is public. | Accepted. It is the maintainer's chosen git identity. |
| 8 | INFO | trufflehog reported 3 "verified" hits in test method names (detector "Lob"). | False positives, nothing real. |
| 9 | SUGGESTION | Dependabot cannot bump the versions pinned in env vars. | A comment in `dependabot.yml` covers it, and cooldown and grouping were added. |

## Confirmed OK

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` = `refs/tags/v7.0.1`
- The SHA256 values for gitleaks 8.30.1 and rclone v1.75.2 match the upstream checksum files.
- `permissions: contents: read`, `persist-credentials: false`, no `pull_request_target`, no `${{ github.event.* }}` in `run:`, timeouts set.
- The pre-commit hook fails closed when gitleaks is missing.

## Ruleset (applied after the first CI run)

`protect-main` on the default branch:
- deletion and non_fast_forward blocked
- required status checks `gitleaks` and `unittest` (integration_id 15368, GitHub Actions)
- bypass for the repository Admin role (`actor_id 5`, `bypass_mode: always`) so the solo maintainer can still push directly; that path is covered by the local hook and GitHub push protection
