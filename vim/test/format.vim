" Tests :TurkeyFormat and format-on-save against the compiler $TURKEY_CMD, on
" the formatter goldens in $TURKEY_FORMAT_DIR. Works on copies in
" $TURKEY_OUT/format. Results go to $TURKEY_OUT/format.out; run.sh fails on any
" line not starting with "ok".
execute 'set rtp+=' . fnameescape($TURKEY_VIM_DIR)
filetype plugin indent on
syntax on
let g:turkey_command = $TURKEY_CMD
let s:dir = $TURKEY_OUT . '/format'
call mkdir(s:dir, 'p')
let s:out = []

function! s:Check(name, ok, ...) abort
  " One line per result: a detail's newlines become ` | `.
  let detail = a:ok || a:0 == 0 ? '' : ': ' . substitute(a:1, '\n', ' | ', 'g')
  call add(s:out, (a:ok ? 'ok ' : 'FAIL ') . a:name . detail)
endfunction

" Returns the path of a fresh copy of the golden input NAME.gob.
function! s:Copy(name, as) abort
  let path = s:dir . '/' . a:as
  call writefile(readfile($TURKEY_FORMAT_DIR . '/' . a:name . '.gob'), path)
  return path
endfunction

function! s:Expected(name) abort
  return readfile($TURKEY_FORMAT_DIR . '/' . a:name . '.expected.gob')
endfunction

" :TurkeyFormat formats the buffer, leaves the file alone, and undoes in one step.
let s:path = s:Copy('match_arms', 'match_arms.gob')
let s:before = readfile(s:path)
execute 'edit! ' . fnameescape(s:path)
call cursor(5, 3)
TurkeyFormat
call s:Check('TurkeyFormat formats the buffer', getline(1, '$') ==# s:Expected('match_arms'),
      \ string(getline(1, '$')))
call s:Check('TurkeyFormat does not write the file', readfile(s:path) ==# s:before && &modified)
call s:Check('TurkeyFormat keeps the cursor line', line('.') == 5, 'line ' . line('.'))
undo
call s:Check('one undo restores the buffer', getline(1, '$') ==# s:before)
redo
TurkeyFormat
call s:Check('TurkeyFormat on a formatted buffer changes nothing', changenr() == 1,
      \ 'changenr ' . changenr())
bwipeout!

" The buffer's lines are what is formatted, not the file on disk.
let s:path = s:Copy('blocks', 'unsaved.gob')
execute 'edit! ' . fnameescape(s:path)
silent %delete _
call setline(1, readfile($TURKEY_FORMAT_DIR . '/if_else.gob'))
TurkeyFormat
call s:Check('TurkeyFormat formats unsaved changes', getline(1, '$') ==# s:Expected('if_else'))
bwipeout!

" A buffer that does not parse is left alone, and the error names its file.
let s:path = s:dir . '/broken.gob'
call writefile(['fun main() {', '    print(', '}'], s:path)
execute 'edit! ' . fnameescape(s:path)
messages clear
TurkeyFormat
let s:messages = execute('messages')
call s:Check('a parse error leaves the buffer alone',
      \ getline(1, '$') ==# ['fun main() {', '    print(', '}'] && !&modified)
call s:Check('a parse error is reported', s:messages =~# 'status 3' && s:messages =~# 'parse error',
      \ s:messages)
call s:Check('the error names the buffer''s file',
      \ s:messages =~# '\V' . escape(expand('%'), '\') . ':3:1: parse error', s:messages)
bwipeout!

" A buffer with no file is formatted too, and its errors name `<stdin>`.
enew!
setfiletype turkey
call setline(1, readfile($TURKEY_FORMAT_DIR . '/blocks.gob'))
TurkeyFormat
call s:Check('TurkeyFormat formats a buffer with no file', getline(1, '$') ==# s:Expected('blocks'),
      \ string(getline(1, '$')))
enew!
setfiletype turkey
call setline(1, ['fun main() {', '    print(', '}'])
messages clear
TurkeyFormat
let s:messages = execute('messages')
call s:Check('a parse error leaves a buffer with no file alone',
      \ getline(1, '$') ==# ['fun main() {', '    print(', '}'])
call s:Check('the error names a buffer with no file <stdin>',
      \ s:messages =~# '<stdin>:3:1: parse error', s:messages)
bwipeout!

" `gq` on lines that hold code formats the whole buffer, in one undo step.
let s:path = s:Copy('if_else', 'gq.gob')
let s:before = readfile(s:path)
execute 'edit! ' . fnameescape(s:path)
call s:Check('formatexpr is set', &l:formatexpr ==# 'turkey#FormatExpr()', &l:formatexpr)
normal! 3Ggqq
call s:Check('gq on one line of code formats the whole buffer',
      \ getline(1, '$') ==# s:Expected('if_else'), string(getline(1, '$')))
undo
call s:Check('one undo restores the buffer after gq', getline(1, '$') ==# s:before)
normal! gggqG
call s:Check('gq over the whole buffer formats it', getline(1, '$') ==# s:Expected('if_else'))
bwipeout!

" `gq` on a comment wraps it at 'textwidth', with Vim's own formatting, and
" leaves the code around it alone.
let s:path = s:dir . '/gq-comment.gob'
let s:words = repeat('word ', 30)
call writefile(['fun main() {', '    -- ' . s:words, '    print(1)', '}'], s:path)
execute 'edit! ' . fnameescape(s:path)
normal! 2Ggqq
let s:lines = getline(1, '$')
call s:Check('gq on a comment wraps it with its leader',
      \ len(s:lines) == 5 && s:lines[1] =~# '^\t-- word' && s:lines[2] =~# '^\t-- word'
      \ && len(s:lines[1]) <= 100 && s:lines[3] ==# '    print(1)', string(s:lines))
bwipeout!

" A command that cannot run is reported and leaves the buffer alone.
let s:path = s:Copy('blocks', 'nocommand.gob')
execute 'edit! ' . fnameescape(s:path)
let b:turkey_command = s:dir . '/no-such-turkey'
messages clear
TurkeyFormat
call s:Check('a missing command is reported', execute('messages') =~# 'cannot run',
      \ execute('messages'))
call s:Check('a missing command leaves the buffer alone', !&modified)
bwipeout!

" Format-on-save is off by default, and formats before writing when on.
let s:path = s:Copy('loops', 'onsave.gob')
let s:before = readfile(s:path)
execute 'edit! ' . fnameescape(s:path)
set modified
write
call s:Check('format-on-save is off by default', readfile(s:path) ==# s:before)
let g:turkey_format_on_save = 1
set modified
write
call s:Check('format-on-save formats the written file', readfile(s:path) ==# s:Expected('loops'))
let g:turkey_format_on_save = 0
bwipeout!

let s:path = s:dir . '/broken-onsave.gob'
call writefile(['fun main() {', '    print(', '}'], s:path)
execute 'edit! ' . fnameescape(s:path)
let b:turkey_format_on_save = 1
call setline(2, '    print(1')
silent! write
call s:Check('a buffer that does not parse is still written',
      \ readfile(s:path) ==# ['fun main() {', '    print(1', '}'])
bwipeout!

call writefile(s:out, $TURKEY_OUT . '/format.out')
qa!
