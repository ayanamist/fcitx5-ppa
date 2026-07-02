#!/usr/bin/env bash
# 拉Debian sid源码,pbuilder本地测试编译,dput上传
# 参数: <package>
# 环境: OWNER, PPA, SERIES, GPG_KEY_ID, GITHUB_WORKSPACE, PBUILDER_BASE (可选)
# 输出到 $GITHUB_OUTPUT: uploaded_version=<ver> (若跳过则空)
set -euo pipefail

PKG="$1"
: "${OWNER:?}"
: "${PPA:?}"
: "${SERIES:?}"
: "${GPG_KEY_ID:?}"
: "${GITHUB_WORKSPACE:?}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

echo "::group::apt-get source ${PKG}"
apt-get source "${PKG}"
echo "::endgroup::"

SRCDIR="$(find . -maxdepth 1 -mindepth 1 -type d -name "${PKG}-*" -printf '%f\n' | sort | head -n1)"
if [[ -z "$SRCDIR" ]]; then
  echo "::error::source directory not found for ${PKG}"
  exit 1
fi
cd "$SRCDIR"

DEB_VERSION="$(dpkg-parsechangelog -SVersion)"
echo "Debian source version: ${DEB_VERSION}"

PPA_VER="$("${GITHUB_WORKSPACE}/scripts/get-ppa-version.sh" "$OWNER" "$PPA" "$PKG" "$SERIES" || true)"
echo "PPA current version: ${PPA_VER:-none}"

BASE_SUFFIX="~${SERIES}1~ppa"
EXPECTED_PREFIX="${DEB_VERSION}${BASE_SUFFIX}"

# 若 PPA 已发布版本 >= 期望首个 (~ppa1),说明 upstream 未涨或涨得更慢, 跳过
if [[ -n "$PPA_VER" ]]; then
  if dpkg --compare-versions "$PPA_VER" ge "${EXPECTED_PREFIX}1"; then
    echo "::notice::PPA has version ${PPA_VER} >= ${EXPECTED_PREFIX}1; skip."
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
      echo "uploaded_version=" >> "$GITHUB_OUTPUT"
      echo "skipped=true" >> "$GITHUB_OUTPUT"
    fi
    exit 0
  fi
fi

# 查历史所有匹配 <DEB_VERSION>~noble1~ppaN 的最大 N (含已 superseded/deleted)
# Launchpad 拒收重复文件名, 即使 obsolete 也不许再传
MAX_HIST_N="$("${GITHUB_WORKSPACE}/scripts/get-max-ppa-n.sh" "$OWNER" "$PPA" "$PKG" "$SERIES" "$DEB_VERSION" || true)"
echo "PPA historical max ~ppaN for ${DEB_VERSION}: ${MAX_HIST_N:-none}"

NEW_N=1
if [[ -n "$MAX_HIST_N" ]]; then
  NEW_N=$((MAX_HIST_N + 1))
fi

NEW_VERSION="${EXPECTED_PREFIX}${NEW_N}"
echo "Target PPA version: ${NEW_VERSION}"

# 放宽 build-dep 版本约束: sid 里 (>= X) / (>> X) / (= X) 若 X 高于 noble,
# 且 API 兼容, 用 perl 改写 debian/control. 表在 scripts/relax-deps.map
# spec 语法: name        — 删版本约束
#            name=VER    — 改成 (= VER)
RELAX_MAP="${GITHUB_WORKSPACE}/scripts/relax-deps.map"
if [[ -f "$RELAX_MAP" ]]; then
  RELAX_LIST="$(awk -v p="$PKG" '$1==p {$1=""; sub(/^ /,""); print; exit}' "$RELAX_MAP")"
  if [[ -n "$RELAX_LIST" ]]; then
    echo "::group::relax build-deps: ${RELAX_LIST}"
    for spec in $RELAX_LIST; do
      if [[ "$spec" == *"="* ]]; then
        dep="${spec%%=*}"
        newver="${spec#*=}"
        # "<dep> (op X)" -> "<dep> (= newver)"
        perl -i -pe "s/\Q${dep}\E\s*\([^)]*\)/${dep} (= ${newver})/g" debian/control
        echo "  rewrote: ${dep} → (= ${newver})"
      else
        # "<dep> (op X)" -> "<dep>"
        perl -i -pe "s/\Q${spec}\E\s*\([^)]*\)/${spec}/g" debian/control
        echo "  relaxed: ${spec}"
      fi
    done
    echo "::endgroup::"
  fi
fi

# 注入 patches/<pkg>/ 里的 patch 文件到 debian/patches/series (quilt 格式)
# dpkg-source --before-build 会 quilt push -a 应用这些 patch,
# pbuilder dpkg-source -x 时同理. 不在此处手动 patch -p1 (避免二次 apply).
PATCH_DIR="${GITHUB_WORKSPACE}/patches/${PKG}"
if [[ -d "$PATCH_DIR" ]]; then
  for p in "$PATCH_DIR"/*.patch; do
    [[ -f "$p" ]] || continue
    echo "Injecting patch $(basename "$p") into debian/patches"
    mkdir -p debian/patches
    cp "$p" debian/patches/
    basename "$p" >> debian/patches/series
  done
fi

export DEBEMAIL="${DEBEMAIL:?}"
export DEBFULLNAME="${DEBFULLNAME:?}"
dch --force-distribution --allow-lower-version '.*' \
    -v "$NEW_VERSION" \
    -D "$SERIES" \
    "Automated rebuild for ${SERIES} PPA (from Debian sid ${DEB_VERSION})."

# 1) 生成源码包(签名). -d 跳过 build-dep 检查; -nc 跳过 debian/rules clean 避免 dh addon 依赖
debuild -S -sa -d -nc -k"${GPG_KEY_ID}"

cd ..
UPLOAD_VERSION_NOEPOCH="${NEW_VERSION#*:}"
DSC="${PKG}_${UPLOAD_VERSION_NOEPOCH}.dsc"
CHANGES="${PKG}_${UPLOAD_VERSION_NOEPOCH}_source.changes"

if [[ ! -f "$DSC" || ! -f "$CHANGES" ]]; then
  echo "::error::expected artifacts missing: ${DSC} / ${CHANGES}"
  ls -la
  exit 1
fi

# 2) 依赖版本一致性校验准备: 查依赖上游 -dev 包在 PPA 的最新版本
DEPS_MAP="${GITHUB_WORKSPACE}/scripts/deps.map"
declare -A EXPECTED_DEV_VERSIONS   # dev-pkg -> min version
if [[ -f "$DEPS_MAP" ]]; then
  DEP_LINE="$(awk -v p="$PKG" '$1==p {$1=""; sub(/^ /,""); print; exit}' "$DEPS_MAP")"
  if [[ -n "$DEP_LINE" ]]; then
    for pair in $DEP_LINE; do
      DEV_PKG="${pair%%:*}"
      SRC_PKG="${pair##*:}"
      SRC_VER="$("${GITHUB_WORKSPACE}/scripts/get-ppa-version.sh" "$OWNER" "$PPA" "$SRC_PKG" "$SERIES" || true)"
      if [[ -z "$SRC_VER" ]]; then
        echo "::warning::${DEV_PKG} (from ${SRC_PKG}) not in PPA yet; skip strict check for it"
        continue
      fi
      # 二进制dev包的Version与源码包相同,noepoch用作apt比较
      DEV_VER="${SRC_VER#*:}"
      EXPECTED_DEV_VERSIONS["$DEV_PKG"]="$DEV_VER"
      echo "expect ${DEV_PKG} >= ${DEV_VER} (from ${SRC_PKG} ${SRC_VER})"
    done
  fi
fi

# 生成 pbuilder A-hook: 装完 build-dep 后 (build 前) 校验 -dev 版本
# (D-hook 在 build-dep 装之前触发, dpkg-query 全空 → 校验失效)
HOOKDIR="$WORKDIR/pbuilder-hooks"
mkdir -p "$HOOKDIR"
HOOK="$HOOKDIR/A50verify-deps"
{
  cat <<'HDR'
#!/bin/bash
set -euo pipefail
echo "=== verify-deps hook ==="
fail=0
check() {
  local dev="$1" want="$2" have
  have="$(dpkg-query -W -f='${Version}' "$dev" 2>/dev/null || true)"
  if [[ -z "$have" ]]; then
    echo "::warning::$dev not installed by build-deps; skip"
    return
  fi
  if dpkg --compare-versions "$have" lt "$want"; then
    echo "::error::$dev installed=$have expected>=$want"
    fail=1
  else
    echo "OK: $dev $have >= $want"
  fi
}
HDR
  for dev in "${!EXPECTED_DEV_VERSIONS[@]}"; do
    want="${EXPECTED_DEV_VERSIONS[$dev]}"
    printf 'check %q %q\n' "$dev" "$want"
  done
  echo 'exit $fail'
} > "$HOOK"
chmod +x "$HOOK"

# 3) pbuilder 本地测试编译
echo "::group::pbuilder test build ${PKG} ${NEW_VERSION}"
BUILDRESULT="$WORKDIR/buildresult"
mkdir -p "$BUILDRESULT"

if [[ "${PPA_ALLOW_UNTRUSTED:-yes}" == "no" && -n "${PPA_KEYRING:-}" ]]; then
  OTHER_MIRROR="deb [signed-by=${PPA_KEYRING}] http://ppa.launchpad.net/${OWNER}/${PPA}/ubuntu ${SERIES} main"
  UNTRUSTED_ARGS=()
else
  OTHER_MIRROR="deb http://ppa.launchpad.net/${OWNER}/${PPA}/ubuntu ${SERIES} main"
  UNTRUSTED_ARGS=(--allow-untrusted)
fi

# 若 LOCAL_REPO_DIR 设置(上游 stage 的 pbuilder 产物聚合),
# 加进 pbuilder OTHERMIRROR, 让本地编译先看到最新 -dev, 无需等 Launchpad publish
if [[ -n "${LOCAL_REPO_DIR:-}" && -f "${LOCAL_REPO_DIR}/Packages" ]]; then
  echo "using local repo: ${LOCAL_REPO_DIR}"
  OTHER_MIRROR="${OTHER_MIRROR} | deb [trusted=yes] file://${LOCAL_REPO_DIR} ./"
  # 未签名 local repo 需 allow-untrusted 或 [trusted=yes]
  UNTRUSTED_ARGS+=(--allow-untrusted)
  # bindmount 让 chroot 内可访问 host 上的 repo 目录
  BIND_ARG=(--bindmounts "${LOCAL_REPO_DIR}")
else
  BIND_ARG=()
fi

sudo -E pbuilder update --override-config \
  --distribution "$SERIES" \
  --basetgz "${PBUILDER_BASE:-/var/cache/pbuilder/base-${SERIES}.tgz}" \
  --othermirror "$OTHER_MIRROR" \
  "${BIND_ARG[@]}" \
  "${UNTRUSTED_ARGS[@]}"
sudo -E pbuilder build \
  --distribution "$SERIES" \
  --basetgz "${PBUILDER_BASE:-/var/cache/pbuilder/base-${SERIES}.tgz}" \
  --buildresult "$BUILDRESULT" \
  --logfile "$WORKDIR/pbuilder-${PKG}.log" \
  --hookdir "$HOOKDIR" \
  "${BIND_ARG[@]}" \
  "$DSC"
echo "::endgroup::"
echo "pbuilder OK; artifacts:"
ls -la "$BUILDRESULT"

# 若 ARTIFACT_DIR 设置了, copy 所有产物 (source + binary deb) 供 workflow 上传
if [[ -n "${ARTIFACT_DIR:-}" ]]; then
  mkdir -p "$ARTIFACT_DIR"
  cp -v "$BUILDRESULT"/* "$ARTIFACT_DIR/" 2>/dev/null || true
  # source 包相关文件从 $WORKDIR copy (pbuilder 输出只含 binary deb)
  find "$WORKDIR" -maxdepth 1 -type f \( \
       -name "${PKG}_${UPLOAD_VERSION_NOEPOCH}.dsc" \
    -o -name "${PKG}_${UPLOAD_VERSION_NOEPOCH}*.tar.*" \
    -o -name "${PKG}_${UPLOAD_VERSION_NOEPOCH}_source.changes" \
    -o -name "${PKG}_${UPLOAD_VERSION_NOEPOCH}_source.buildinfo" \
    -o -name "pbuilder-${PKG}.log" \
    \) -exec cp -v {} "$ARTIFACT_DIR/" \;
fi

# 4) 上传源码包到 PPA
echo "Uploading ${CHANGES}"
dput "ppa:${OWNER}/${PPA}" "$CHANGES"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "uploaded_version=${NEW_VERSION}" >> "$GITHUB_OUTPUT"
  echo "skipped=false" >> "$GITHUB_OUTPUT"
fi
echo "::notice::Uploaded ${PKG} ${NEW_VERSION}"
