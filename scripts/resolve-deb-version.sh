#!/usr/bin/env bash
# 查询 Debian sid 源码包版本, 输出到 GITHUB_OUTPUT
# 参数: <package>
set -euo pipefail

PKG="$1"

VER="$(apt-cache showsrc "$PKG" 2>/dev/null | awk '/^Version:/{print $2; exit}')"
if [[ -z "$VER" ]]; then
  echo "::error::cannot resolve deb version for ${PKG}"
  exit 1
fi

echo "deb_version=${VER}"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "deb_version=${VER}" >> "$GITHUB_OUTPUT"
fi
