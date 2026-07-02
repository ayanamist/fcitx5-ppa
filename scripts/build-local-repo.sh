#!/usr/bin/env bash
# 从 <artifacts_root> 目录 (含各上游包 debs-*/子目录) 建 flat apt repo.
# 输出 Packages / Packages.gz / Release, 供 pbuilder OTHERMIRROR 挂载 (无签名).
# 用法: build-local-repo.sh <artifacts_root> <output_repo_dir>
set -euo pipefail

SRC="$1"
DST="$2"

mkdir -p "$SRC" "$DST"
# 收集所有 .deb 到 $DST
find "$SRC" -type f -name '*.deb' -exec cp -v {} "$DST/" \;

if ! ls "$DST"/*.deb >/dev/null 2>&1; then
  echo "::warning::no .deb collected from $SRC"
  # 仍生成空 Packages, pbuilder 加空 mirror 无害
fi

cd "$DST"
apt-ftparchive packages . > Packages
gzip -kf Packages
apt-ftparchive release . > Release

echo "local repo ready at: $DST"
ls -la "$DST"
