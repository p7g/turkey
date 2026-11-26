#!/bin/sh
# Assemble the browser playground into one directory a static server can serve.
#
#   scripts/playground-assemble.sh [WASM [LIBTAR [OUT [VERSION]]]]
#
# Defaults: WASM build/turkey.wasm, LIBTAR build/lib.tar.gz, OUT
# build/playground, VERSION empty. Build the inputs with
#
#   turkey build --target wasm32-wasi -o build/turkey.wasm src/Main.gob
#   sh scripts/lib-tar.sh
#
# OUT gets everything under playground/ (the page, its Workers and the
# vendored shim) plus the two inputs as turkey.wasm and lib.tar.gz, which the
# Workers fetch by those names. Paths are relative to the directory the script
# is run from.
#
# The page's picker of example programs reads the tour's lesson programs from
# OUT/../tour/examples.json, which `src/TourSite.gob` writes, so the tour's
# pages, under OUT/../tour, also use the scripts and style sheets copied here.
# Without the tour the picker stays disabled.
#
# A VERSION makes the compile Worker fetch the two inputs as
# `turkey.wasm?v=VERSION` and `lib.tar.gz?v=VERSION`, which a host that caches
# by URL (GitHub Pages) needs, so that a visitor never pairs one deploy's
# compiler with another's library. VERSION is a word of letters and digits.

set -eu

wasm=${1:-build/turkey.wasm}
libtar=${2:-build/lib.tar.gz}
out=${3:-build/playground}
version=${4:-}

case $version in
    *[!A-Za-z0-9]*)
        echo "playground-assemble.sh: VERSION '$version' is not letters and digits" >&2
        exit 1
        ;;
esac

for f in "$wasm" "$libtar"; do
    if [ ! -f "$f" ]; then
        echo "playground-assemble.sh: $f does not exist" >&2
        exit 1
    fi
done

root=$(cd "$(dirname "$0")/.." && pwd)
rm -rf "$out"
mkdir -p "$out"
cp -R "$root/playground/." "$out/"
cp "$wasm" "$out/turkey.wasm"
cp "$libtar" "$out/lib.tar.gz"

if [ -n "$version" ]; then
    worker="$out/compile-worker.js"
    sed "s/^const assetQuery = \"\";\$/const assetQuery = \"?v=$version\";/" \
        "$worker" > "$worker.tmp"
    if cmp -s "$worker" "$worker.tmp"; then
        echo "playground-assemble.sh: compile-worker.js has no assetQuery line" >&2
        exit 1
    fi
    mv "$worker.tmp" "$worker"
fi
echo "playground-assemble.sh: wrote $out" >&2
