" Reindents each file listed in $TURKEY_INDENT_LIST, one path per line, and
" writes the result to $TURKEY_OUT/indent/N.gob, where N is the file's
" number in the list, counting from 1. The files are `turkey format` output, so
" an indentexpr that agrees with the formatter leaves them unchanged; run.sh
" diffs each pair.
execute 'set rtp+=' . fnameescape($TURKEY_VIM_DIR)
filetype plugin indent on
" With TURKEY_SYNTAX=off, indent without syntax highlighting, into
" $TURKEY_OUT/indent-nosyntax instead.
let s:dir = $TURKEY_OUT . '/indent' . ($TURKEY_SYNTAX ==# 'off' ? '-nosyntax' : '')
if $TURKEY_SYNTAX !=# 'off'
  syntax on
endif
call mkdir(s:dir, 'p')
let s:n = 0
for s:path in readfile($TURKEY_INDENT_LIST)
  let s:n += 1
  execute 'edit! ' . fnameescape(s:path)
  if &filetype !=# 'turkey' || &expandtab
    call writefile(['FAIL: ' . s:path . ': filetype=' . &filetype . ' expandtab=' . &expandtab],
          \ $TURKEY_OUT . '/indent.fail', 'a')
  endif
  normal! gg=G
  execute 'write! ' . fnameescape(s:dir . '/' . s:n . '.gob')
  bwipeout!
endfor
qa!
