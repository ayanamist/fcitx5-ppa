#!/usr/bin/env bash
# 查询指定 PPA 中某源码包**所有历史版本**里, 匹配 <deb_version>~<series>1~ppa<N> 的最大 N.
# 用于避免与 Launchpad 里(即使已 superseded/deleted)的旧文件名冲突.
# 参数: <owner> <ppa_name> <source_package> <series> <deb_version>
# 输出: 最大 N (若无匹配则空)
set -euo pipefail

OWNER="$1"
PPA="$2"
PKG="$3"
SERIES="$4"
DEB_VERSION="$5"

ARCHIVE_URL="https://api.launchpad.net/1.0/~${OWNER}/+archive/ubuntu/${PPA}"
PATTERN="^$(printf '%s' "$DEB_VERSION" | sed 's/[.[\*^$()+?{|]/\\&/g')~${SERIES}1~ppa([0-9]+)$"

# getPublishedSources 不带 status 过滤则返回所有历史(Pending/Published/Superseded/Deleted/Obsolete)
# 分页处理: Launchpad 返回 next_collection_link
url="${ARCHIVE_URL}?ws.op=getPublishedSources&source_name=${PKG}&exact_match=true"
max_n=""

while [[ -n "$url" ]]; do
  resp="$(curl -fsSL "$url")"
  # 取所有版本, 匹配前缀
  versions="$(echo "$resp" | jq -r '.entries[]?.source_package_version // empty')"
  while IFS= read -r v; do
    [[ -z "$v" ]] && continue
    if [[ "$v" =~ $PATTERN ]]; then
      n="${BASH_REMATCH[1]}"
      if [[ -z "$max_n" ]] || (( n > max_n )); then
        max_n="$n"
      fi
    fi
  done <<< "$versions"
  url="$(echo "$resp" | jq -r '.next_collection_link // empty')"
done

echo "${max_n}"
