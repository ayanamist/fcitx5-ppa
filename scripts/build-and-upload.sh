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

NEW_N=1
if [[ -n "$PPA_VER" ]]; then
  if [[ "$PPA_VER" == "${EXPECTED_PREFIX}"* ]]; then
    N="${PPA_VER##"${EXPECTED_PREFIX}"}"
    if [[ "$N" =~ ^[0-9]+$ ]]; then
      NEW_N=$((N + 1))
    fi
  else
    if dpkg --compare-versions "$PPA_VER" ge "${EXPECTED_PREFIX}1"; then
      echo "::notice::PPA has newer/equal version (${PPA_VER}); skip."
      if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        echo "uploaded_version=" >> "$GITHUB_OUTPUT"
        echo "skipped=true" >> "$GITHUB_OUTPUT"
      fi
      exit 0
    fi
  fi
fi

NEW_VERSION="${EXPECTED_PREFIX}${NEW_N}"
echo "Target PPA version: ${NEW_VERSION}"

export DEBEMAIL="${DEBEMAIL:?}"
export DEBFULLNAME="${DEBFULLNAME:?}"
dch --force-distribution --force-bad-version \
    -v "$NEW_VERSION" \
    -D "$SERIES" \
    "Automated rebuild for ${SERIES} PPA (from Debian sid ${DEB_VERSION})."

# 1) 生成源码包(签名). -d 跳过 build-dep 检查(host 不装依赖,pbuilder 里才装)
debuild -S -sa -d -k"${GPG_KEY_ID}"

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

# 生成 pbuilder D-hook: 装完 build-dep 后校验 -dev 版本
HOOKDIR="$WORKDIR/pbuilder-hooks"
mkdir -p "$HOOKDIR"
HOOK="$HOOKDIR/D50verify-deps"
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

sudo -E pbuilder update --override-config \
  --distribution "$SERIES" \
  --basetgz "${PBUILDER_BASE:-/var/cache/pbuilder/base-${SERIES}.tgz}" \
  --othermirror "$OTHER_MIRROR" \
  "${UNTRUSTED_ARGS[@]}"
sudo -E pbuilder build \
  --distribution "$SERIES" \
  --basetgz "${PBUILDER_BASE:-/var/cache/pbuilder/base-${SERIES}.tgz}" \
  --buildresult "$BUILDRESULT" \
  --logfile "$WORKDIR/pbuilder-${PKG}.log" \
  --hookdir "$HOOKDIR" \
  "$DSC"
echo "::endgroup::"
echo "pbuilder OK; artifacts:"
ls -la "$BUILDRESULT"

# 4) 上传源码包到 PPA
echo "Uploading ${CHANGES}"
dput "ppa:${OWNER}/${PPA}" "$CHANGES"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "uploaded_version=${NEW_VERSION}" >> "$GITHUB_OUTPUT"
  echo "skipped=false" >> "$GITHUB_OUTPUT"
fi
echo "::notice::Uploaded ${PKG} ${NEW_VERSION}"
