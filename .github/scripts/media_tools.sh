#!/usr/bin/env bash
# Install checksum-pinned static ffmpeg/ffprobe for Backend CI and put them on PATH.
# Archives restored from the Actions cache are verified again before use; a mismatch is
# discarded and downloaded afresh, and a download that does not match fails the job.
set -euo pipefail

: "${MEDIA_TOOLS_RELEASE:?}" "${FFMPEG_GZ_SHA256:?}" "${FFPROBE_GZ_SHA256:?}" "${GITHUB_PATH:?}"
cache="${MEDIA_TOOLS_CACHE:-$HOME/.cache/ovrly-media-tools}"
bin="${RUNNER_TEMP:-/tmp}/ovrly-media-tools/bin"
base="https://github.com/eugeneware/ffmpeg-static/releases/download/${MEDIA_TOOLS_RELEASE}"
mkdir -p "$cache" "$bin"

install_tool() {
  local tool=$1 sum=$2
  local archive="$cache/$tool-linux-x64.gz"
  if [[ -f "$archive" ]] && echo "$sum  $archive" | sha256sum --check --status; then
    echo "$tool: cached archive verified"
  else
    rm -f "$archive" "$archive.part"
    curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
      --connect-timeout 20 --max-time 120 --output "$archive.part" "$base/$tool-linux-x64.gz"
    if ! echo "$sum  $archive.part" | sha256sum --check --status; then
      rm -f "$archive.part"
      echo "::error::$tool archive from $MEDIA_TOOLS_RELEASE does not match its pinned SHA-256"
      exit 1
    fi
    mv "$archive.part" "$archive"
    echo "$tool: downloaded and verified"
  fi
  gunzip -c "$archive" > "$bin/$tool"
  chmod 0755 "$bin/$tool"
}

install_tool ffmpeg "$FFMPEG_GZ_SHA256"
install_tool ffprobe "$FFPROBE_GZ_SHA256"
if ! command -v prlimit > /dev/null; then
  echo "::error::prlimit (util-linux) is missing from the runner"
  exit 1
fi
"$bin/ffmpeg" -hide_banner -version | sed -n 1p
"$bin/ffprobe" -hide_banner -version | sed -n 1p
echo "$bin" >> "$GITHUB_PATH"
