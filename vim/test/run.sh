#!/bin/sh
# Headless tests for the Turkey Vim plugin. Usage: sh vim/test/run.sh
#
# For each editor found on PATH (vim, then nvim):
#   1. reindent (gg=G) every formatter golden, tests/fmt/*.expected.gob, and
#      diff against the original: they are `turkey fmt` output, so an
#      indentexpr that agrees with the formatter changes nothing. Once lib/
#      is formatted (its files indent with tabs), a sample of it too
#      (test/indent.vim);
#   2. type each golden afresh, line by line without its indentation, and
#      require the same file (test/typing.vim);
#   3. probe syntax groups at a few dozen positions in test/fixture.gob
#      (test/syntax.vim);
#   4. check `:compiler turkey`'s errorformat on recorded output
#      (test/compiler.vim);
#   5. with a compiler, check :TurkeyFmt and format-on-save (test/fmt.vim)
#      and run `:make` on a file with lint warnings.
#
# Lines that steps 1 and 2 may get wrong are listed in test/indent.skip,
# with the reason, and printed at the end.
#
# The compiler is $TURKEY_BOOT, else build/stages/turkey, else `turkey` on
# PATH. With one, also checks that the fixture lexes and parses. It need not
# type-check: it declares a `foreign` function, which only the standard
# library may.
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
plugin=$(dirname "$here")
root=$(dirname "$plugin")
tmp=$(mktemp -d "${TMPDIR:-/tmp}/turkey-vim-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

boot=${TURKEY_BOOT:-$root/build/stages/turkey}
if [ ! -x "$boot" ]; then
    boot=$(command -v turkey 2>/dev/null || true)
fi

export TURKEY_VIM_DIR="$plugin"
export TURKEY_FIXTURE="$here/fixture.gob"
export TURKEY_FMT_DIR="$root/tests/fmt"
export TURKEY_CMD="$boot"
export TURKEY_LINT_FILE="$root/tests/lint/empty_return_value.gob"

# The files to type, and the files to reindent: those and the lib/ sample,
# once lib/ is in the formatter's layout.
typing="$tmp/typing.list"
ls "$root"/tests/fmt/*.expected.gob > "$typing"
list="$tmp/indent.list"
cp "$typing" "$list"
sample="lib/Data/Option.gob lib/Data/Map.gob lib/Iter.gob lib/Regex.gob lib/Turkey/Scan.gob"
lib_note="note: lib/ is not formatted yet, so only the goldens are reindented"
if grep -q '^	' "$root/lib/Iter.gob"; then
    for f in $sample; do
        echo "$root/$f" >> "$list"
    done
    lib_note=""
fi
export TURKEY_INDENT_LIST="$list" TURKEY_TYPING_LIST="$typing"

skips="$here/indent.skip"

check_out() { # NAME FILE: fail unless every line of FILE starts with "ok".
    if [ ! -f "$2" ]; then
        echo "FAIL [$editor]: $1: no results"
        failed=1
    elif grep -v '^ok' "$2" > "$2.fail"; then
        echo "FAIL [$editor]: $1:"
        cat "$2.fail"
        failed=1
    fi
}

compare() { # WHAT LIST DIR: diff each file in LIST with DIR/N.gob.
    n=0
    while read -r f; do
        n=$((n + 1))
        rel=${f#"$root"/}
        if [ ! -f "$3/$n.gob" ]; then
            echo "FAIL [$editor]: $1 $rel: no result"
            failed=1
            continue
        fi
        diff "$f" "$3/$n.gob" > "$out/compare.diff"
        # Only indentation can differ, so every hunk changes lines in place.
        # Report each changed line the skip list does not name.
        grep '^[0-9]' "$out/compare.diff" | while read -r hunk; do
            case $hunk in
                *c*) ;;
                *) echo "$rel: $hunk"; continue ;;
            esac
            first=${hunk%%c*}
            last=${first#*,}
            first=${first%,*}
            l=$first
            while [ "$l" -le "$last" ]; do
                grep -q "^$rel:$l " "$skips" || echo "$rel:$l"
                l=$((l + 1))
            done
        done > "$out/compare.unexpected"
        if [ -s "$out/compare.unexpected" ]; then
            echo "FAIL [$editor]: $1 $rel changed it:"
            diff -u "$f" "$3/$n.gob"
            failed=1
        fi
    done < "$2"
}

run_vim() { # SCRIPT: run it, keeping what the editor prints in a log.
    log="$out/$(basename "$1" .vim).log"
    if [ "$editor" = nvim ]; then
        nvim --headless -u NONE -i NONE -n -S "$1" </dev/null >"$log" 2>&1
    else
        vim -Nu NONE -i NONE -n -es -S "$1" </dev/null >"$log" 2>&1
    fi
}

fail=0
for editor in vim nvim; do
    command -v "$editor" >/dev/null 2>&1 || continue
    out="$tmp/$editor"
    mkdir -p "$out"
    TURKEY_OUT="$out"
    export TURKEY_OUT
    failed=0

    run_vim "$here/indent.vim"
    run_vim "$here/typing.vim"
    run_vim "$here/syntax.vim"
    run_vim "$here/compiler.vim"
    if [ -n "$boot" ]; then
        run_vim "$here/fmt.vim"
    fi

    if [ -s "$out/indent.fail" ]; then
        echo "FAIL [$editor]:"
        cat "$out/indent.fail"
        failed=1
    fi
    compare reindenting "$list" "$out/indent"
    compare typing "$typing" "$out/typing"
    check_out syntax "$out/syntax.out"
    check_out compiler "$out/compiler.out"
    if [ -n "$boot" ]; then
        check_out TurkeyFmt "$out/fmt.out"
    fi

    if [ "$failed" -eq 0 ]; then
        echo "ok [$editor]"
    else
        fail=1
    fi
done

if [ -n "$lib_note" ]; then
    echo "$lib_note"
fi
while read -r skip; do
    echo "skipped: $skip"
done < "$skips"

if [ -n "$boot" ]; then
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
    echo "note: no compiler; set TURKEY_BOOT to test :TurkeyFmt and that the fixture parses"
fi

exit "$fail"
