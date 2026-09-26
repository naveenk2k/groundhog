#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

resources="$work_dir/resources.rb"
output="$work_dir/Formula/groundhog.rb"

cat > "$resources" <<'RUBY'
resource "example" do
  url "https://files.pythonhosted.org/example-1.0.tar.gz"
  sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
end
RUBY

"$repo_root/packaging/homebrew/prepare-formula.sh" \
  0.1.0 \
  bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  "$resources" \
  "$output"

grep -Fq 'groundhog-companion-0.1.0.tar.gz' "$output"
grep -Fq 'resource "example" do' "$output"
grep -Fq 'GROUNDHOG_SECRET_FILE' "$output"
grep -Fq 'GROUNDHOG_CORPUS_DB' "$output"
grep -Fq 'XDG_CACHE_HOME' "$output"

if "$repo_root/packaging/homebrew/prepare-formula.sh" 0.1.0 invalid "$resources" "$output" 2>/dev/null; then
  echo "prepare-formula accepted an invalid source checksum" >&2
  exit 1
fi

echo "Homebrew packaging checks passed"
