#!/usr/bin/env bash
# Build a harness-pinned EVMBench image from an audit image.
#
# Installs the exact harness pins, optionally bakes the pinned Yudai venv, and
# writes /etc/evmbench/harness-pins.json for provenance/preflight checks.
#
# Usage:
#   scripts/build_harness_image.sh \
#     --base evmbench/audit:2024-03-gitcoin \
#     --output ghcr.io/acme/evmbench/audit:2024-03-gitcoin \
#     [--yudai] [--yudai-commit SHA] [--no-cache]
set -euo pipefail

OPENCODE_VERSION="1.19.4"
CODEX_VERSION="0.164.0"
YUDAI_COMMIT="5a5541251a48da685a5b0bc2a00596771c7ded07"
YUDAI_REPO="pranay5255/yudai-swe-agent"
YUDAI_VENV="/opt/yudai/venv"

BASE_IMAGE=""
OUTPUT_IMAGE=""
WITH_YUDAI=0
NO_CACHE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) BASE_IMAGE="$2"; shift 2 ;;
    --output) OUTPUT_IMAGE="$2"; shift 2 ;;
    --yudai) WITH_YUDAI=1; shift ;;
    --yudai-commit) YUDAI_COMMIT="$2"; shift 2 ;;
    --no-cache) NO_CACHE=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$BASE_IMAGE" || -z "$OUTPUT_IMAGE" ]]; then
  echo "--base and --output are required" >&2
  exit 2
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/evmbench-image.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

{
  echo "FROM ${BASE_IMAGE}"
  echo "RUN npm install -g opencode-ai@${OPENCODE_VERSION} @openai/codex@${CODEX_VERSION} \\"
  echo " && rm -f /usr/local/bin/codex \\"
  echo " && ln -s /usr/bin/codex /usr/local/bin/codex \\"
  echo " && opencode --version && codex --version"
} > "$WORKDIR/Dockerfile"

PIN_JSON="{\"opencode\":\"${OPENCODE_VERSION}\",\"codex\":\"${CODEX_VERSION}\",\"yudai_commit\":\"${YUDAI_COMMIT}\",\"yudai_repo\":\"https://github.com/${YUDAI_REPO}\",\"yudai_installed\":false"

if [[ "$WITH_YUDAI" == "1" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "--yudai requires the GitHub CLI (gh) for the pinned tarball" >&2
    exit 2
  fi
  mkdir -p "$WORKDIR/yudai-src"
  gh api "repos/${YUDAI_REPO}/tarball/${YUDAI_COMMIT}" > "$WORKDIR/yudai.tar.gz"
  tar -xzf "$WORKDIR/yudai.tar.gz" -C "$WORKDIR/yudai-src" --strip-components=1
  if ! grep -q "__version__" "$WORKDIR/yudai-src/src/minisweagent/__init__.py"; then
    echo "downloaded Yudai tarball is missing src/minisweagent" >&2
    exit 1
  fi
  YUDAI_VERSION="$(sed -n 's/.*__version__ = "\([^"]*\)".*/\1/p' "$WORKDIR/yudai-src/src/minisweagent/__init__.py" | head -1)"
  PIN_JSON="${PIN_JSON},\"yudai_package_version\":\"${YUDAI_VERSION}\",\"yudai_installed\":true"
  {
    echo "COPY yudai-src /tmp/yudai-src"
    echo "RUN python3 -m venv ${YUDAI_VENV} \\"
    echo " && ${YUDAI_VENV}/bin/pip install --no-cache-dir /tmp/yudai-src 'mcp>=1.0' \\"
    echo " && ${YUDAI_VENV}/bin/python -c 'import minisweagent, litellm' \\"
    echo " && mkdir -p /etc/evmbench \\"
    echo " && ${YUDAI_VENV}/bin/pip freeze > /etc/evmbench/yudai-pip-freeze.txt \\"
    echo " && rm -rf /tmp/yudai-src"
    echo "ENV PATH=\"${YUDAI_VENV}/bin:\${PATH}\""
  } >> "$WORKDIR/Dockerfile"
fi

PIN_JSON="${PIN_JSON}}"
{
  echo "RUN mkdir -p /etc/evmbench \\"
  echo " && printf '%s\\n' '${PIN_JSON}' > /etc/evmbench/harness-pins.json \\"
  echo " && cat /etc/evmbench/harness-pins.json"
} >> "$WORKDIR/Dockerfile"

BUILD_ARGS=()
if [[ "$NO_CACHE" == "1" ]]; then
  BUILD_ARGS+=(--no-cache)
fi

echo "Building ${OUTPUT_IMAGE} from ${BASE_IMAGE}"
echo "Pins: ${PIN_JSON}"
docker build "${BUILD_ARGS[@]}" -t "$OUTPUT_IMAGE" -f "$WORKDIR/Dockerfile" "$WORKDIR"
