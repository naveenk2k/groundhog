#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "usage: $0 VERSION SOURCE_SHA256 PYTHON_RESOURCES_FILE [OUTPUT]" >&2
  exit 64
}

[[ $# -ge 3 && $# -le 4 ]] || usage

version="$1"
source_sha256="$2"
resources_file="$3"
output="${4:-packaging/homebrew/dist/Formula/groundhog.rb}"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "VERSION must be a semantic version such as 0.1.0" >&2
  exit 65
}

[[ "$source_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "SOURCE_SHA256 must be 64 lowercase hexadecimal characters" >&2
  exit 65
}

[[ -s "$resources_file" ]] || {
  echo "Python resource file is missing or empty: $resources_file" >&2
  exit 66
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
template="$repo_root/packaging/homebrew/Formula/groundhog.rb.in"

if [[ "$output" != /* ]]; then
  output="$repo_root/$output"
fi

mkdir -p "$(dirname "$output")"

SOURCE_URL="https://github.com/naveenk2k/groundhog/releases/download/v${version}/groundhog-companion-${version}.tar.gz" \
SOURCE_SHA256="$source_sha256" \
TEMPLATE="$template" \
RESOURCES_FILE="$resources_file" \
OUTPUT="$output" \
ruby <<'RUBY'
template = File.read(ENV.fetch("TEMPLATE"))
resources = File.read(ENV.fetch("RESOURCES_FILE")).rstrip

rendered = template
  .sub("@@SOURCE_URL@@", ENV.fetch("SOURCE_URL"))
  .sub("@@SOURCE_SHA256@@", ENV.fetch("SOURCE_SHA256"))
  .sub("@@PYTHON_RESOURCES@@", resources.lines.map { |line| "  #{line}" }.join.rstrip)

abort "unresolved formula template token" if rendered.include?("@@")
File.write(ENV.fetch("OUTPUT"), rendered)
RUBY

ruby -c "$output"
echo "Wrote $output"
