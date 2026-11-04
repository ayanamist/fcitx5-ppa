#!/usr/bin/env bash
# 是否已有该发行版的 PPA 索引；仅明确的 404 表示首次发布前的空仓库。
set -euo pipefail
OWNER="${1:?}"
PPA="${2:?}"
SERIES="${3:?}"

STATUS="$(curl -sSL --connect-timeout 15 --max-time 60 --retry 3 --retry-delay 1 \
  --output /dev/null --write-out '%{http_code}' \
  "https://ppa.launchpadcontent.net/${OWNER}/${PPA}/ubuntu/dists/${SERIES}/Release")"
case "$STATUS" in
  200) echo true ;;
  404)
    echo "::notice::PPA has no ${SERIES} index yet; using Ubuntu and local upstream debs only." >&2
    echo false
    ;;
  *)
    echo "::error::Cannot check PPA ${SERIES} index (HTTP ${STATUS}); refusing to assume an empty repository." >&2
    exit 1
    ;;
esac
