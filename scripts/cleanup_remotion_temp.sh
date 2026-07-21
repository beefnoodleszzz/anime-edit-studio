#!/bin/zsh
# Remove only Remotion's abandoned macOS temporary bundles. Never touch outputs,
# source media, the SQLite library, or non-Remotion system temp directories.
set -euo pipefail
setopt null_glob

if pgrep -fl 'render-shots\.mjs|remotion.*render' >/dev/null; then
  print -u2 'Refusing cleanup: a Remotion render is still active.'
  exit 1
fi

count=0
for bundle in "$TMPDIR"/remotion-webpack-bundle-*; do
  [[ -d "$bundle" ]] || continue
  find "$bundle" -depth -delete
  ((count += 1))
done

print "Removed $count abandoned Remotion temporary bundle(s)."
