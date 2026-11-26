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
# The page's picker of example programs is built from the recorded programs
# tests/programs/playground_NAME/Main.gob, so the suite and the page run the
# same source. OUT/examples/NAME.gob is each one's source, title line kept,
# without its `--%` suite directives and the blank line before them, so line
# numbers match the goldens. OUT/examples/index.json lists them
# as [{"name": NAME, "title": TITLE, "file": "examples/NAME.gob"}], `hello`
# first and the rest sorted by name.
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

examples=$root/tests/programs
mkdir -p "$out/examples"
names=$(cd "$examples" && for d in playground_*/; do d=${d%/}; echo "${d#playground_}"; done |
    awk '$0 == "hello" { print "0 " $0; next } { print "1 " $0 }' |
    LC_ALL=C sort | cut -d' ' -f2)
printf '[' > "$out/examples/index.json"
sep=
for name in $names; do
    src=$examples/playground_$name/Main.gob
    title=$(sed -n '1s/^-- //p' "$src")
    case $title in
        "" | *\\* | *\"*)
            echo "playground-assemble.sh: $src must start with a '-- TITLE' line, without quote or backslash" >&2
            exit 1
            ;;
    esac
    # Drop the directives, and the blank lines left before them.
    sed -e '/^--%/d' "$src" |
        awk '{ lines[NR] = $0 } END { n = NR; while (n > 0 && lines[n] == "") n--; for (i = 1; i <= n; i++) print lines[i] }' \
        > "$out/examples/$name.gob"
    printf '%s\n  {"name": "%s", "title": "%s", "file": "examples/%s.gob"}' "$sep" "$name" "$title" "$name" \
        >> "$out/examples/index.json"
    sep=,
done
printf '\n]\n' >> "$out/examples/index.json"

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
