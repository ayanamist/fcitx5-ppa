#!/usr/bin/env bash
# 查询指定 PPA 中某源码包**所有历史版本**里, 匹配 <deb_version>~<series>1~ppa<N> 的最大 N.
# 用于避免与 Launchpad 里(即使已 superseded/deleted)的旧文件名冲突.
# 参数: <owner> <ppa_name> <source_package> <series> <deb_version>
# 可选第六参数 --json: 输出 max_n 和 active_version, 保留有效历史版本供复用
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
active_version=""

while [[ -n "$url" ]]; do
  resp="$(curl -fsSL -H 'Cache-Control: no-cache' "$url")"
  # 取所有版本, 匹配前缀
  versions="$(echo "$resp" | jq -r '.entries[] | [.source_package_version, .status, .distro_series_link] | @tsv')"
  while IFS=$'\t' read -r v status series_url; do
    [[ -z "$v" ]] && continue
    if [[ "$v" =~ $PATTERN ]]; then
      n="${BASH_REMATCH[1]}"
      if [[ "$series_url" == "https://api.launchpad.net/1.0/ubuntu/${SERIES}" ]] &&
         [[ "$status" == "Pending" || "$status" == "Published" ]]; then
        if [[ -z "$active_version" ]] || dpkg --compare-versions "$v" gt "$active_version"; then
          active_version="$v"
        fi
      fi
      if [[ -z "$max_n" ]] || (( n > max_n )); then
        max_n="$n"
      fi
    fi
  done <<< "$versions"
  url="$(echo "$resp" | jq -r '.next_collection_link // empty')"
done

if [[ "${6:-}" == "--json" ]]; then
  jq -n --arg max_n "$max_n" --arg active_version "$active_version" \
    '{max_n: $max_n, active_version: $active_version}'
else
  echo "${max_n}"
fi
