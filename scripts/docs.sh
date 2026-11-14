#!/bin/sh
# Build the standard library's API documentation as a static website:
#
#   scripts/docs.sh [OUT]       (default OUT: build/docs in the repository)
#
# writes the site `boot doc --html` generates into OUT: OUT/index.html, which
# lists the modules, a page per module, and OUT/docs.json, what `boot doc`
# says every library module exports. Every link in the site is relative, so
# it can be opened as files.
#
# OUT/.docs-files lists what the last run wrote, and the next run removes
# those files first, so that a module since deleted leaves no page behind.
# An OUT that holds anything else is refused rather than written into, since
# the script cannot tell which of its files are its own.
#
# lib/Turkey/ is the implementation, which no program may import, and is left
# out. The compiler is $TURKEY_BOOT, or scripts/build.sh's stage2, built first
# if it is not there.

set -eu

# OUT is relative to the directory the script is run from, and absolute from
# here on, since the script works from the repository root.
out=${1:-}
if [ -n "$out" ]; then
    mkdir -p "$out"
    out=$(cd "$out" && pwd)
fi
# So is $TURKEY_BOOT, when it is a path with a directory in it.
boot=${TURKEY_BOOT:-}
case $boot in
    */*) boot=$(cd "$(dirname "$boot")" && pwd)/$(basename "$boot") ;;
esac
cd "$(dirname "$0")/.."
out=${out:-$(pwd)/build/docs}
boot=${boot:-build/stages/stage2}
if [ ! -x "$boot" ]; then
    sh scripts/build.sh
fi

mkdir -p "$out"
written="$out/.docs-files"
if [ -f "$written" ]; then
    # No generated name has a space in it.
    # shellcheck disable=SC2046
    (cd "$out" && rm -f $(cat .docs-files) .docs-files)
fi
if [ -n "$(ls -A "$out")" ]; then
    echo "scripts/docs.sh: '$out' holds files this script did not write;" \
         "name a new or empty directory" >&2
    exit 2
fi
# shellcheck disable=SC2046  # one argument per file; no library path has a space
"$boot" doc --html "$out" $(find lib -name '*.gob' -not -path 'lib/Turkey/*' | sort)
(cd "$out" && ls) > "$written"

echo "$out/index.html"
