#!/bin/sh
# Write the standard library as one compressed tar, for the browser playground.
#
#   scripts/lib-tar.sh [OUT]       (default OUT: build/lib.tar.gz)
#
# The compiler reads lib/*.gob at run time through TURKEY_LIB, and a page has
# no file system, so the page unpacks this archive into an in-memory directory
# and points the compiler at it. OUT holds exactly the .gob files under lib/,
# and nothing else the compiler does not read. A member's path is its path in
# the repository, `lib/Data/Map.gob`, so unpacking into a directory D makes
# D/lib the tree to name in TURKEY_LIB. There are no directory members: the
# unpacker makes each member's parents.
#
# The archive is deterministic. Members are in byte order of their names, every
# member has mode 0644, uid and gid 0, no owner names and mtime 0, and the
# gzip header carries no name or time (gzip -n). The tar is written here, in
# sh, and not by the host's tar, because BSD and GNU tar take different flags
# and write different bytes; so the uncompressed tar is the same on every host.
# The compressed bytes are not: GNU gzip and macOS's compress the same input
# differently, so two hosts agree on the tar and may disagree on OUT. Two runs
# on one host, from one tree, write the same OUT.
#
# The format is POSIX ustar, which has no way to store a path over 100 bytes;
# the script refuses one. It needs only sh, find, sort, od, awk, wc, dd and
# gzip, and runs from the repository root whatever the working directory is.
# OUT is relative to the directory the script is run from.

set -eu

out=${1:-build/lib.tar.gz}
case $out in
    /*) ;;
    *) out=$(pwd)/$out ;;
esac
mkdir -p "$(dirname "$out")"

cd "$(dirname "$0")/.."

LC_ALL=C
export LC_ALL

# Prints $1 NUL bytes. head -c 0 is an error on macOS, so 0 is handled here.
zeros() {
    if [ "$1" -gt 0 ]; then
        dd if=/dev/zero bs="$1" count=1 2> /dev/null
    fi
}

# Prints one 512-byte header for member $1 of $2 bytes. $3 is the checksum, or
# empty for the 8 spaces the checksum is computed over.
header() {
    {
        printf '%s' "$1"
        zeros $((100 - ${#1}))
        printf '0000644\0'          # mode
        printf '0000000\0'          # uid
        printf '0000000\0'          # gid
        printf '%011o\0' "$2"       # size
        printf '00000000000\0'      # mtime
        if [ -n "$3" ]; then        # checksum
            printf '%06o\0 ' "$3"
        else
            printf '        '
        fi
        printf '0'                  # regular file
        zeros 100       # link name
        printf 'ustar\00000'        # magic and version
        zeros 32        # uname
        zeros 32        # gname
        printf '0000000\0'          # device major
        printf '0000000\0'          # device minor
        zeros 155       # path prefix
        zeros 12        # padding to 512
    }
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

find lib -type f -name '*.gob' | sort > "$tmp/names"
if [ ! -s "$tmp/names" ]; then
    echo "lib-tar.sh: no .gob files under lib/" >&2
    exit 1
fi

while IFS= read -r name; do
    if [ ${#name} -gt 100 ]; then
        echo "lib-tar.sh: '$name' is longer than 100 bytes, which ustar cannot store" >&2
        exit 1
    fi
    size=$(wc -c < "$name" | tr -d ' ')
    header "$name" "$size" '' > "$tmp/header"
    sum=$(od -An -v -tu1 "$tmp/header" | awk '{ for (i = 1; i <= NF; i++) s += $i } END { print s }')
    header "$name" "$size" "$sum" > "$tmp/header"
    cat "$tmp/header" "$name"
    zeros $(( (512 - size % 512) % 512 ))
done < "$tmp/names" > "$tmp/lib.tar"
zeros 1024 >> "$tmp/lib.tar"

gzip -9 -n -c "$tmp/lib.tar" > "$tmp/lib.tar.gz"
mv "$tmp/lib.tar.gz" "$out"
echo "lib-tar.sh: wrote $out ($(wc -c < "$out" | tr -d ' ') bytes," \
     "$(wc -l < "$tmp/names" | tr -d ' ') files)" >&2
