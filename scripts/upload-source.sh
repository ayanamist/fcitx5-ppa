#!/usr/bin/env bash
# Runs under the upload job's PPA lock; recheck history after waiting for it.
set -euo pipefail
PKG="${1:?}"
DEB_VERSION="${2:?}"
NEW_VERSION="${3:?}"
CHANGES="${4:?}"
: "${OWNER:?}"
: "${PPA:?}"
: "${SERIES:?}"
: "${GITHUB_WORKSPACE:?}"
NEW_N="${NEW_VERSION#"${DEB_VERSION}~${SERIES}1~ppa"}"
if [[ ! "$NEW_N" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::Invalid target source version."
  exit 1
fi
FINAL_HISTORY="$(python3 "${GITHUB_WORKSPACE}/scripts/ppa-state.py" "$OWNER" "$PPA" "$PKG" "$SERIES" "$DEB_VERSION")"
FINAL_STATE="$(jq -r '.state' <<< "$FINAL_HISTORY")"
if [[ "$FINAL_STATE" == "unknown" ]]; then
  echo "::error::Pre-upload PPA state is unknown; refusing upload."
  exit 1
fi
FINAL_ACTIVE="$(jq -r '.active_version' <<< "$FINAL_HISTORY")"
FINAL_MAX_N="$(jq -r '.max_n' <<< "$FINAL_HISTORY")"
if [[ -n "$FINAL_ACTIVE" ]]; then
  echo "::notice::Pre-upload check found active ${FINAL_ACTIVE}; skip duplicate upload."
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "uploaded_version=" >> "$GITHUB_OUTPUT"
    echo "skipped=true" >> "$GITHUB_OUTPUT"
  fi
  exit 0
elif [[ -n "$FINAL_MAX_N" ]] && (( FINAL_MAX_N >= NEW_N )); then
  echo "::error::Pre-upload check found target revision already occupied; rerun to allocate a new version."
  exit 1
fi
echo "Uploading ${CHANGES}"
dput "ppa:${OWNER}/${PPA}" "$CHANGES"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "uploaded_version=${NEW_VERSION}" >> "$GITHUB_OUTPUT"
  echo "skipped=false" >> "$GITHUB_OUTPUT"
fi
echo "::notice::Uploaded ${PKG} ${NEW_VERSION}"
