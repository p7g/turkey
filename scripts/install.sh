#!/bin/sh
# Put the compiler on PATH as `turkey`: a symbolic link in BINDIR to this
# checkout's build/stages/stage2, which scripts/build.sh writes.
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
compiler=$ROOT/build/stages/stage2
link=$bindir/turkey

if [ ! -x "$compiler" ]; then
    echo "install.sh: no compiler at $compiler; run scripts/build.sh first" >&2
    exit 1
fi
# Replace an earlier link, but never a file someone put there.
if [ -e "$link" ] && [ ! -L "$link" ]; then
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
