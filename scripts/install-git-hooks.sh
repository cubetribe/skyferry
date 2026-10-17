#!/usr/bin/env bash
# Installs a pre-commit hook that refuses commits containing secrets (gitleaks).
# Run once after cloning:  ./scripts/install-git-hooks.sh
set -euo pipefail

root=$(git rev-parse --show-toplevel)
hooks_dir=$(git rev-parse --git-path hooks)
hook="$hooks_dir/pre-commit"

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks is not installed." >&2
  echo "  macOS:  brew install gitleaks" >&2
  echo "  other:  https://github.com/gitleaks/gitleaks#installing" >&2
  exit 1
fi

mkdir -p "$hooks_dir"
if [ -e "$hook" ] && ! grep -q 'skyferry-secret-scan' "$hook"; then
  backup="$hook.bak-$(date +%Y%m%d-%H%M%S)"
  mv "$hook" "$backup"
  echo "Existing pre-commit hook moved to $backup"
fi

cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
# skyferry-secret-scan: block commits that contain secrets.
set -euo pipefail
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "pre-commit: gitleaks not found - refusing to commit. Install it, then retry." >&2
  exit 1
fi
root=$(git rev-parse --show-toplevel)
if ! gitleaks git --pre-commit --staged --config "$root/.gitleaks.toml" --redact --no-banner --exit-code 1 "$root"; then
  echo "pre-commit: possible secret in staged changes - commit blocked." >&2
  echo "Remove it (and rotate it if it was real), then commit again." >&2
  exit 1
fi
HOOK
chmod +x "$hook"
echo "Installed secret-scanning pre-commit hook: $hook"
echo "(gitleaks $(gitleaks version), config: $root/.gitleaks.toml)"
