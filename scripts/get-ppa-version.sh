#!/usr/bin/env bash
# 查询指定PPA中某源码包的最新版本
# 参数: <owner> <ppa_name> <source_package> <series>
# 输出: 版本字符串 (无则空)
set -euo pipefail

OWNER="$1"
PPA="$2"
PKG="$3"
SERIES="$4"

DIST_URL="https://api.launchpad.net/1.0/ubuntu/${SERIES}"
ARCHIVE_URL="https://api.launchpad.net/1.0/~${OWNER}/+archive/ubuntu/${PPA}"

curl -fsSL --get \
  --data-urlencode "ws.op=getPublishedSources" \
  --data-urlencode "source_name=${PKG}" \
  --data-urlencode "exact_match=true" \
  --data-urlencode "distro_series=${DIST_URL}" \
  --data-urlencode "order_by_date=true" \
  "${ARCHIVE_URL}" \
  | jq -r '.entries[]?.source_package_version' \
  | head -n1
