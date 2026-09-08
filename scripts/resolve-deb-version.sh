#!/usr/bin/env bash
# 查询 Debian sid 源码包版本, 输出到 GITHUB_OUTPUT
# 参数: <package>
set -euo pipefail

PKG="$1"

# showsrc 返回所有版本, 第一条不一定最新. 必须与 apt-get source 默认
# 选择最新版的行为一致, 否则旧 cache key 命中后不会保存本次新编的 deb.
# 完整消费输出, 避免提前退出在 pipefail 下导致 apt-cache SIGPIPE.
VERSIONS="$(apt-cache --only-source showsrc "$PKG" | awk '/^Version:/{print $2}' | sort -u)"
VER=""
while IFS= read -r candidate; do
  [[ -n "$candidate" ]] || continue
  if [[ -z "$VER" ]] || dpkg --compare-versions "$candidate" gt "$VER"; then
    VER="$candidate"
  fi
done <<< "$VERSIONS"
if [[ -z "$VER" ]]; then
  echo "::error::cannot resolve deb version for ${PKG}"
  exit 1
fi

echo "deb_version=${VER}"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "deb_version=${VER}" >> "$GITHUB_OUTPUT"
fi
