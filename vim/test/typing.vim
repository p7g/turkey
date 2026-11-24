" Types each file listed in $TURKEY_TYPING_LIST into an empty buffer, one
" line at a time with its indentation stripped, so that every line starts
" where the indentexpr puts it as the line is typed: after <CR>, and again
" when a closer or another key in 'indentkeys' starts it. Writes the result to
" $TURKEY_OUT/typing/N.gob, where N is the file's number in the list. The
" files are `turkey format` output; run.sh diffs each pair.
execute 'set rtp+=' . fnameescape($TURKEY_VIM_DIR)
filetype plugin indent on
" With TURKEY_SYNTAX=off, indent without syntax highlighting, into
" $TURKEY_OUT/typing-nosyntax instead.
let s:dir = $TURKEY_OUT . '/typing' . ($TURKEY_SYNTAX ==# 'off' ? '-nosyntax' : '')
if $TURKEY_SYNTAX !=# 'off'
  syntax on
endif
call mkdir(s:dir, 'p')
let s:n = 0
for s:path in readfile($TURKEY_TYPING_LIST)
  let s:n += 1
  enew!
  setfiletype turkey
  " Keep comment leaders and wrapping out of what is typed.
  setlocal formatoptions= textwidth=0
  let s:lines = map(readfile(s:path), {_, l -> substitute(l, '^\s\+', '', '')})
  for s:i in range(len(s:lines))
    " `o` opens the line below with its indent; a blank line is typed as
    " one, which drops the indent `o` gave it.
    if s:i == 0
      execute "normal! i" . s:lines[0] . "\<Esc>"
    else
      execute "normal! o" . s:lines[s:i] . "\<Esc>"
    endif
  endfor
  execute 'write! ' . fnameescape(s:dir . '/' . s:n . '.gob')
endfor
qa!
