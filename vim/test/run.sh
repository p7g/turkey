#!/bin/sh
# Headless tests for the Turkey Vim plugin. Usage: sh vim/test/run.sh
#
# For each editor found on PATH (vim, then nvim):
#   1. open test/fixture.gob, check the filetype is detected, reindent the
#      whole buffer (gg=G) and diff against the original -- the fixture is
#      written in the plugin's own style, so a correct indentexpr is a
#      no-op on it;
#   2. probe syntax groups at a few dozen positions (test/syntax.vim).
#
# If a Turkey compiler is found ($TURKEY_BOOT, else build/stages/stage2),
# also checks that the fixture lexes and parses. It need not type-check: it
# declares a `foreign` function, which only the standard library may.
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
plugin=$(dirname "$here")
tmp=$(mktemp -d "${TMPDIR:-/tmp}/turkey-vim-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

export TURKEY_VIM_DIR="$plugin"
export TURKEY_FIXTURE="$here/fixture.gob"

fail=0
for editor in vim nvim; do
    command -v "$editor" >/dev/null 2>&1 || continue
    out="$tmp/$editor"
    mkdir -p "$out"
    TURKEY_OUT="$out"
    export TURKEY_OUT
    failed=0

    if [ "$editor" = nvim ]; then
        nvim --headless -u NONE -i NONE -n -S "$here/indent.vim" </dev/null
        nvim --headless -u NONE -i NONE -n -S "$here/syntax.vim" </dev/null
    else
        vim -Nu NONE -n -es -S "$here/indent.vim" </dev/null
        vim -Nu NONE -n -es -S "$here/syntax.vim" </dev/null
    fi

    if ! diff -u "$TURKEY_FIXTURE" "$out/indent.out" > "$out/indent.diff"; then
        echo "FAIL [$editor]: reindenting the fixture changed it:"
        cat "$out/indent.diff"
        failed=1
    fi
    if grep -v '^ok' "$out/syntax.out" > "$out/syntax.fail"; then
        :
    fi
    if [ -s "$out/syntax.fail" ]; then
        echo "FAIL [$editor]: syntax probes:"
        cat "$out/syntax.fail"
        failed=1
    fi
    if [ "$failed" -eq 0 ]; then
        echo "ok [$editor]"
    else
        fail=1
    fi
done

root=$(dirname "$plugin")
boot=${TURKEY_BOOT:-$root/build/stages/stage2}
if [ -x "$boot" ]; then
    # Run from the repository root, where the compiler finds lib/.
    (cd "$root" && "$boot" check "$TURKEY_FIXTURE") > "$tmp/check.out" 2>&1
    if grep -q -E 'lex error|parse error' "$tmp/check.out"; then
        echo "FAIL [fixture parses]:"
        cat "$tmp/check.out"
        fail=1
    else
        echo "ok [fixture parses]"
    fi
else
    echo "note: no compiler at $boot; set TURKEY_BOOT to check that the fixture parses"
fi

exit "$fail"
