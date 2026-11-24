" Tests `:compiler turkey`: its 'makeprg', and its 'errorformat' on recorded
" `turkey check` output. With a compiler in $TURKEY_CMD, also runs `:make` on
" $TURKEY_LINT_FILE, a file with lint warnings. Results go to
" $TURKEY_OUT/compiler.out; run.sh fails on any line not starting with "ok".
execute 'set rtp+=' . fnameescape($TURKEY_VIM_DIR)
filetype plugin indent on
syntax on
if $TURKEY_CMD !=# ''
  let g:turkey_command = $TURKEY_CMD
endif
let s:out = []

function! s:Check(name, ok, ...) abort
  " One line per result: a detail's newlines become ` | `.
  let detail = a:ok || a:0 == 0 ? '' : ': ' . substitute(a:1, '\n', ' | ', 'g')
  call add(s:out, (a:ok ? 'ok ' : 'FAIL ') . a:name . detail)
endfunction

" Returns the quickfix entries as [type, lnum, col, text, file tail].
function! s:Entries() abort
  return map(getqflist(), {_, e -> [e.type, e.lnum, e.col, e.text,
        \ e.bufnr > 0 ? fnamemodify(bufname(e.bufnr), ':t') : '']})
endfunction

enew
compiler turkey
call s:Check('makeprg runs turkey check on the file, quoted',
      \ &l:makeprg ==# shellescape(get(g:, 'turkey_command', 'turkey')) . ' check %:S', &l:makeprg)

" `:cgetexpr` reads with the global 'errorformat'; `:make` with the local one.
let &g:errorformat = &l:errorformat
cgetexpr [
      \ 'Main.gob:6:22: warning: a bare ''return'' already means ''return ()''; remove ''{}'' [empty-return-value]',
      \ 'Main.gob:12:5: error: ''--@expect()'' names no check; name the checks it expects [expect]',
      \ 'Main.gob:4:1: parse error: expected an expression, found ''}''',
      \ 'Lib.gob:4:14: type error: a numeric literal cannot have type ''String''',
      \ '1 error, 1 warning',
      \ '3 warnings',
      \ ]
let s:want = [
      \ ['w', 6, 22, 'a bare ''return'' already means ''return ()''; remove ''{}'' [empty-return-value]', 'Main.gob'],
      \ ['e', 12, 5, '''--@expect()'' names no check; name the checks it expects [expect]', 'Main.gob'],
      \ ['e', 4, 1, 'expected an expression, found ''}''', 'Main.gob'],
      \ ['e', 4, 14, 'a numeric literal cannot have type ''String''', 'Lib.gob'],
      \ ]
call s:Check('errorformat reads warnings, errors and phase errors, and drops the count',
      \ s:Entries() ==# s:want, string(s:Entries()))

if $TURKEY_CMD !=# '' && $TURKEY_LINT_FILE !=# ''
  " A copy under a path with spaces, which the shell must see as one word.
  let s:dir = $TURKEY_OUT . '/sp ace'
  call mkdir(s:dir, 'p')
  let s:file = s:dir . '/lint file.gob'
  call writefile(readfile($TURKEY_LINT_FILE), s:file)
  execute 'edit! ' . fnameescape(s:file)
  compiler turkey
  silent make!
  let s:entries = s:Entries()
  let s:wrong = filter(copy(s:entries), {_, e -> e[0] !=# 'w' || e[1] <= 0 || e[2] <= 0
        \ || e[3] !~# ' \[empty-return-value\]$' || e[4] !=# 'lint file.gob'})
  call s:Check(':make fills the quickfix list from turkey check',
        \ len(s:entries) == 9 && empty(s:wrong), string(s:entries))
  call s:Check(':make reads the first warning''s position',
        \ !empty(s:entries) && s:entries[0][1:2] == [6, 22], string(s:entries[:0]))
endif

call writefile(s:out, $TURKEY_OUT . '/compiler.out')
qa!
