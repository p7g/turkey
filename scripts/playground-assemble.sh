#!/bin/sh
# Assemble the browser playground into one directory a static server can serve.
#
#   scripts/playground-assemble.sh [WASM [LIBTAR [OUT]]]
#
# Defaults: WASM build/turkey.wasm, LIBTAR build/lib.tar.gz, OUT
# build/playground. Build the inputs with
#
#   turkey build --target wasm32-wasi -o build/turkey.wasm src/Main.gob
#   sh scripts/lib-tar.sh
#
# OUT gets everything under playground/ (the page, its Workers and the
# vendored shim) plus the two inputs as turkey.wasm and lib.tar.gz, which the
# Workers fetch by those names. Paths are relative to the directory the script
# is run from.

set -eu

wasm=${1:-build/turkey.wasm}
libtar=${2:-build/lib.tar.gz}
out=${3:-build/playground}

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
echo "playground-assemble.sh: wrote $out" >&2
