#!/usr/bin/env bash
# 轮询 Launchpad 直到源码包及其所有二进制发布完成
# 参数: <owner> <ppa> <package> <version> <series>
# 环境: TIMEOUT_SEC (默认 7200 = 120min), POLL_INTERVAL (默认 60s)
set -euo pipefail

OWNER="$1"
PPA="$2"
PKG="$3"
VERSION="$4"
SERIES="$5"

TIMEOUT_SEC="${TIMEOUT_SEC:-7200}"
POLL_INTERVAL="${POLL_INTERVAL:-60}"

DIST_URL="https://api.launchpad.net/1.0/ubuntu/${SERIES}"
ARCHIVE_URL="https://api.launchpad.net/1.0/~${OWNER}/+archive/ubuntu/${PPA}"

start=$(date +%s)
echo "Waiting for ${PKG} ${VERSION} in ppa:${OWNER}/${PPA}/${SERIES} (timeout ${TIMEOUT_SEC}s)"

get_source_pub() {
  curl -fsSL --get \
    --data-urlencode "ws.op=getPublishedSources" \
    --data-urlencode "source_name=${PKG}" \
    --data-urlencode "version=${VERSION}" \
    --data-urlencode "exact_match=true" \
    --data-urlencode "distro_series=${DIST_URL}" \
    "${ARCHIVE_URL}"
}

while true; do
  now=$(date +%s)
  elapsed=$((now - start))
  if (( elapsed > TIMEOUT_SEC )); then
    echo "::error::Timeout after ${elapsed}s waiting for ${PKG} ${VERSION}"
    exit 1
  fi

  src_json="$(get_source_pub || echo '{"entries":[]}')"
  entries="$(echo "$src_json" | jq '.entries | length')"

  if [[ "$entries" == "0" ]]; then
    echo "[${elapsed}s] source not yet visible; wait ${POLL_INTERVAL}s"
    sleep "$POLL_INTERVAL"
    continue
  fi

  status="$(echo "$src_json" | jq -r '.entries[0].status')"
  self_link="$(echo "$src_json" | jq -r '.entries[0].self_link')"

  if [[ "$status" != "Published" ]]; then
    echo "[${elapsed}s] source status=${status}; wait ${POLL_INTERVAL}s"
    sleep "$POLL_INTERVAL"
    continue
  fi

  # 检查所有构建
  builds_json="$(curl -fsSL --get \
    --data-urlencode "ws.op=getBuilds" \
    "${self_link}" || echo '{"entries":[]}')"
  build_states="$(echo "$builds_json" | jq -r '.entries[].buildstate')"

  if [[ -z "$build_states" ]]; then
    echo "[${elapsed}s] builds not yet scheduled; wait ${POLL_INTERVAL}s"
    sleep "$POLL_INTERVAL"
    continue
  fi

  fail_state="$(echo "$build_states" | grep -Ev '^(Successfully built|Currently building|Needs building|Uploading build|Dependency wait|Chroot problem)$' || true)"
  building="$(echo "$build_states" | grep -Ev '^Successfully built$' || true)"

  # 有失败态直接退出
  bad="$(echo "$build_states" | grep -E '^(Failed to build|Chroot problem|Failed to upload|Build for superseded Source|Cancelled build)$' || true)"
  if [[ -n "$bad" ]]; then
    echo "::error::Build failed for ${PKG} ${VERSION}:"
    echo "$builds_json" | jq -r '.entries[] | "  \(.arch_tag): \(.buildstate) — \(.web_link)"'
    exit 1
  fi

  if [[ -n "$building" ]]; then
    echo "[${elapsed}s] builds in progress:"
    echo "$builds_json" | jq -r '.entries[] | "  \(.arch_tag): \(.buildstate)"'
    sleep "$POLL_INTERVAL"
    continue
  fi

  # 全部构建成功,检查二进制是否已发布
  bins_json="$(curl -fsSL --get \
    --data-urlencode "ws.op=getPublishedBinaries" \
    "${self_link}" || echo '{"entries":[]}')"
  bin_count="$(echo "$bins_json" | jq '.entries | length')"
  bin_pending="$(echo "$bins_json" | jq -r '.entries[] | select(.status != "Published") | .binary_package_name' || true)"

  if [[ "$bin_count" == "0" ]] || [[ -n "$bin_pending" ]]; then
    echo "[${elapsed}s] binaries not fully published (count=${bin_count}, pending=${bin_pending:-none}); wait ${POLL_INTERVAL}s"
    sleep "$POLL_INTERVAL"
    continue
  fi

  echo "::notice::${PKG} ${VERSION} fully published (${bin_count} binaries) after ${elapsed}s"
  exit 0
done
