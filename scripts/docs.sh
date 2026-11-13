#!/bin/sh
# Build the standard library's API documentation as one page:
#
#   scripts/docs.sh [OUT]       (default OUT: build/docs)
#
# writes OUT/docs.json, what `boot doc` says every library module exports, and
# OUT/index.html, scripts/docs.html with that JSON spliced into it. The page
# needs nothing else: it renders the modules and searches names, types and
# comments in the browser. Open it as a file.
#
# lib/Turkey/ is the implementation, which no program may import, and is left
# out. The compiler is $TURKEY_BOOT, or scripts/build.sh's stage2, built first
# if it is not there.

set -eu
cd "$(dirname "$0")/.."

out=${1:-build/docs}
boot=${TURKEY_BOOT:-build/stages/stage2}
if [ ! -x "$boot" ]; then
    sh scripts/build.sh
fi

mkdir -p "$out"
# shellcheck disable=SC2046  # one argument per file; no library path has a space
"$boot" doc $(find lib -name '*.gob' -not -path 'lib/Turkey/*' | sort) > "$out/docs.json"

# The JSON escapes every `<`, so it cannot end the <script> element it lands in.
awk -v data="$out/docs.json" '
    /@DOCS_JSON@/ { while ((getline line < data) > 0) print line; next }
    { print }
' scripts/docs.html > "$out/index.html"

echo "$out/index.html"
