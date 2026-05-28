#!/usr/bin/env bash
# Marathon bundle 9 (4c) — webm -> mp4 + gif encoder.
#
# Inputs:
#   $1  Playwright output dir (e.g. apps/frontend/tests/walkthroughs/.output)
#   $2  Target asset dir       (e.g. docs-site/static/img/walkthroughs)
#
# Behavior:
#   - For each "<test-id>/video.webm" file under $1, find the slug
#     declared by the spec via test.info().annotations (slug type).
#     Playwright drops a results JSON next to each test-output dir;
#     we parse it with jq.
#   - Run ffmpeg in an alpine container to encode:
#       <slug>.mp4 — h264 baseline, ~700kbps, no audio, web-optimized
#                    moov atom (faststart) so the docs <video> tag can
#                    start playback before the file finishes loading.
#       <slug>.gif — 12fps decimated palette gif, capped to 1440x810
#                    (slightly cropped vertically to drop the browser
#                    chrome). Sub-2MB for the 12-second flows we
#                    capture; the docs page links it as a click-to-
#                    play poster fallback.
#
# Idempotent: re-running overwrites existing output. Skips webm files
# whose slug annotation is missing (the test would fail capture, so a
# missing slug is treated as a soft warning rather than a hard error).
#
# Why ffmpeg in docker rather than apt-get install: most contributors
# do not have a system ffmpeg recent enough for libx264 baseline +
# faststart on the same invocation, and pinning the alpine image
# (jrottenberg/ffmpeg:7.1-alpine) gives byte-identical output across
# operator machines.

set -euo pipefail

raw_dir="${1:?missing raw playwright output dir}"
out_dir="${2:?missing target asset dir}"

if [ ! -d "$raw_dir" ]; then
  echo "[walkthroughs-encode] no $raw_dir — run 'make walkthroughs-capture' first" >&2
  exit 1
fi

mkdir -p "$out_dir"

# Each test drops a sidecar ``slug.txt`` into its outputDir via an
# ``afterEach`` hook (see walkthroughs.spec.ts). Playwright's auto-
# generated directory names truncate long test titles and strip our
# slug prefix, so the sidecar is the only reliable mapping.

shopt -s nullglob
encoded=0
for webm in "$raw_dir"/*/video.webm; do
  parent_dir="$(dirname "$webm")"
  parent="$(basename "$parent_dir")"
  if [ ! -f "$parent_dir/slug.txt" ]; then
    echo "[walkthroughs-encode] no slug.txt in $parent — skipping" >&2
    continue
  fi
  slug="$(tr -d '[:space:]' < "$parent_dir/slug.txt")"
  if ! printf '%s' "$slug" | grep -qE '^[a-z0-9-]+$'; then
    echo "[walkthroughs-encode] invalid slug '$slug' in $parent — skipping" >&2
    continue
  fi

  echo "[walkthroughs-encode] encoding $slug from $parent"
  mp4_out="$out_dir/$slug.mp4"
  gif_out="$out_dir/$slug.gif"

  # Single docker invocation per webm. The container mounts:
  #   - raw_dir read-only at /in
  #   - out_dir read-write at /out
  # The exact webm path inside the container is /in/<parent>/video.webm.
  docker run --rm \
    -v "$PWD/$raw_dir:/in:ro" \
    -v "$PWD/$out_dir:/out" \
    jrottenberg/ffmpeg:7.1-alpine \
    -hide_banner -loglevel error -y \
    -i "/in/$parent/video.webm" \
    -an \
    -movflags +faststart \
    -vcodec libx264 -profile:v baseline -level 3.1 \
    -preset slow -crf 28 \
    -pix_fmt yuv420p \
    "/out/$slug.mp4"

  # Two-pass gif: generate a palette first, then dither the frames
  # against it. Single-pass gifs banding hard on the dark navy chrome
  # of the portal, this two-pass is the standard ffmpeg recipe.
  #
  # The gif is the click-to-play preview / fallback for environments
  # where the docs <video> tag does not auto-play. Size matters more
  # than fidelity: scale to 720 (half the mp4 width) and decimate to
  # 8 fps. Typical output: 1–2 MB for a 12-second flow.
  docker run --rm \
    -v "$PWD/$raw_dir:/in:ro" \
    -v "$PWD/$out_dir:/out" \
    jrottenberg/ffmpeg:7.1-alpine \
    -hide_banner -loglevel error -y \
    -i "/in/$parent/video.webm" \
    -filter_complex \
      "fps=8,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=5" \
    "/out/$slug.gif"

  for f in "$mp4_out" "$gif_out"; do
    bytes="$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f" 2>/dev/null || echo 0)"
    printf '  %10d  %s\n' "$bytes" "$f"
  done
  encoded=$((encoded + 1))
done

if [ "$encoded" -eq 0 ]; then
  echo "[walkthroughs-encode] no webm files found under $raw_dir" >&2
  exit 1
fi

echo
echo "[walkthroughs-encode] done — encoded $encoded walkthroughs"
echo "[walkthroughs-encode] review with 'git diff --stat $out_dir/'"
