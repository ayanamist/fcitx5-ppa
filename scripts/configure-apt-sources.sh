#!/usr/bin/env bash
# Limit the disposable runner's host APT to Ubuntu and our Debian source entry.
# Preserve the image's Ubuntu mirrors, suites and signature configuration.
set -euo pipefail
APT_DIR="${1:-/etc/apt}"
test -s "$APT_DIR/sources.list.d/ubuntu.sources"
install -d "$APT_DIR/ppa-sources.list.d"
cp "$APT_DIR/sources.list.d/ubuntu.sources" "$APT_DIR/ppa-sources.list.d/ubuntu.sources"
cat > "$APT_DIR/apt.conf.d/99-ppa-sources" <<'EOF'
Dir::Etc::sourcelist "/dev/null";
Dir::Etc::sourceparts "ppa-sources.list.d";
EOF
