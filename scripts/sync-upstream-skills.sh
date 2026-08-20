#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM_REPO="$(python -c "import json; print(json.load(open('$ROOT/vendor/upstream.json'))['repo'])")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "$UPSTREAM_REPO" "$TMP/upstream"
rsync -a --delete --exclude fonts --exclude '.git' "$TMP/upstream/skills/" "$ROOT/skills/"
SHA="$(git -C "$TMP/upstream" rev-parse HEAD)"
python - "$ROOT/vendor/upstream.json" "$SHA" <<'PY'
import json, sys
path, sha = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
data["skills_synced_sha"] = sha
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(file=open(path, "a", encoding="utf-8"))
PY
echo "skills synced to $SHA"
