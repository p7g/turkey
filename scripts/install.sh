#!/bin/sh
# Put the compiler on PATH as `turkey`: a symbolic link in BINDIR to this
# checkout's build/stages/turkey, which scripts/build.sh writes.
#
# The compiler finds lib/ by resolving its own path through the link and
# looking above it, so the checkout is the installation. A rebuild, or an edit
# to lib/, changes what `turkey` compiles with; moving the checkout leaves the
# link dangling, and running this again repairs it.
#
# Usage: scripts/install.sh [BINDIR]
#
#   BINDIR  where the link goes (default ~/.local/bin); created if missing

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
bindir=${1:-$HOME/.local/bin}
compiler=$ROOT/build/stages/turkey
link=$bindir/turkey

if [ ! -x "$compiler" ]; then
    echo "install.sh: no compiler at $compiler; run scripts/build.sh first" >&2
    exit 1
fi
# Replace an earlier link to a Turkey compiler, from this checkout or another,
# under either name scripts/build.sh has given its result, but nothing else:
# not a file, and not a link to some other tool.
if [ -L "$link" ]; then
    case $(readlink "$link") in
        */build/stages/turkey|*/build/stages/stage2) ;;
        *)
            echo "install.sh: $link links to $(readlink "$link"), not a Turkey" \
                 "compiler; remove it first" >&2
            exit 1 ;;
    esac
elif [ -e "$link" ]; then
    echo "install.sh: $link exists and is not a symbolic link; remove it first" >&2
    exit 1
fi

mkdir -p "$bindir"
ln -sfn "$compiler" "$link"
echo "install.sh: $link -> $compiler"
case ":$PATH:" in
    *":$bindir:"*) ;;
    *) echo "install.sh: $bindir is not on PATH; add it to use 'turkey'" >&2 ;;
esac
