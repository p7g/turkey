" Probes syntax groups at positions in $TURKEY_FIXTURE, located by
" searching forward for a pattern (an empty pattern reuses the previous
" match's position). Results go to $TURKEY_OUT/syntax.out; run.sh fails
" on any line not starting with "ok".
execute 'set rtp+=' . fnameescape($TURKEY_VIM_DIR)
filetype plugin indent on
syntax on
set shiftwidth=4 expandtab
execute 'edit! ' . fnameescape($TURKEY_FIXTURE)

let s:out = []
let s:prev = ''
function! s:Probe(pat, off, want) abort
  " Probes walk forward through the file in order, never backward, landing
  " on the match's first character. A pattern equal to the previous one
  " (or the empty pattern, which reuses the position) first advances past
  " it, so paired probes -- the opening quote, then the escape -- work.
  if a:pat !=# ''
    if a:pat ==# s:prev
      call cursor(line('.'), col('.') + 1)
    endif
    let s:prev = a:pat
    if search(a:pat, 'Wc') == 0
      call add(s:out, 'FAIL not-found: ' . a:pat . ' (want ' . a:want . ')')
      return
    endif
  elseif s:prev !=# ''
    call cursor(line('.'), col('.') + 1)
    let s:prev = ''
  endif
  let name = synIDattr(synID(line('.'), col('.') + a:off, 1), 'name')
  if name ==# a:want
    call add(s:out, 'ok ' . a:want . ' @ ' . (a:pat ==# '' ? '<same>' : a:pat))
  else
    call add(s:out, printf('FAIL %s+%d @%d: got %s want %s',
          \ a:pat, a:off, line('.'), empty(name) ? '<none>' : name, a:want))
  endif
endfunction

call cursor(1, 1)
call s:Probe('^-- A line', 0, 'turkeyLineComment')
call s:Probe('True', 0, 'turkeyLineComment')   " keywords stay comments in comments
call s:Probe('\<TODO\>', 0, 'turkeyTodo')
call s:Probe('not a string', 0, 'turkeyLineComment')
call s:Probe('^--- A doc', 0, 'turkeyDocComment')
call s:Probe('^---- A rule', 0, 'turkeyLineComment')
call s:Probe('^{-', 0, 'turkeyBlockComment')
call s:Probe('nested', 0, 'turkeyBlockComment')
call s:Probe('part$', 0, 'turkeyBlockComment') " after the inner -}, still comment
call s:Probe('code-looking', 0, 'turkeyBlockComment')
call s:Probe('^export', 0, 'turkeyModule')
call s:Probe('<+>', 0, 'turkeyOperator')
call s:Probe('^import', 0, 'turkeyModule')
call s:Probe('\<as\>', 0, 'turkeyModule')
call s:Probe('\.\.', 0, 'turkeyOperator')
call s:Probe('^infix', 0, 'turkeyKeyword')
call s:Probe('Addition', 0, 'turkeyConstructor')
call s:Probe('<+>', 0, 'turkeyOperator')
call s:Probe('^foreign', 0, 'turkeyKeyword')
call s:Probe('"printf"', 0, 'turkeyString')
call s:Probe('\.\.\.', 2, 'turkeyOperator')
call s:Probe('^type Shape', 0, 'turkeyStructure')
call s:Probe('-- The round', 0, 'turkeyLineComment')
call s:Probe('Circle', 0, 'turkeyConstructor')
call s:Probe('\<var\>', 0, 'turkeyKeyword')    " a mutable record field
call s:Probe('Shown\[', 0, 'turkeyConstructor')
call s:Probe('^class', 0, 'turkeyStructure')
call s:Probe('^    type Elem', 4, 'turkeyStructure')
call s:Probe('^instance', 0, 'turkeyStructure')
call s:Probe('\~', 0, 'turkeyOperator')
call s:Probe('^fun inc', 0, 'turkeyKeyword')
call s:Probe('->', 0, 'turkeyOperator')
call s:Probe('match s', 0, 'turkeyConditional')
call s:Probe('3\.14', 0, 'turkeyFloat')
call s:Probe('0\.0$', 0, 'turkeyFloat')
call s:Probe('let Some', 0, 'turkeyKeyword')
call s:Probe('\<else\> {$', 0, 'turkeyConditional')
call s:Probe('0xFFFF_FFFF', 10, 'turkeyNumber')
call s:Probe('0b1010', 5, 'turkeyNumber')
call s:Probe('0o17', 3, 'turkeyNumber')
call s:Probe('\^', 0, 'turkeyOperator')
call s:Probe('1_000 ', 4, 'turkeyNumber')
call s:Probe('0xFFFF ', 5, 'turkeyNumber')
call s:Probe('||', 0, 'turkeyOperator')
call s:Probe('^fun main', 0, 'turkeyKeyword')
call s:Probe('\<var\>', 0, 'turkeyKeyword')
call s:Probe('\<let\>', 0, 'turkeyKeyword')
call s:Probe('pi', 2, 'turkeyIdentifier')      " the prime in pi'
call s:Probe('3\.14', 0, 'turkeyFloat')
call s:Probe('1\.5e-3', 5, 'turkeyFloat')
call s:Probe('1e3', 2, 'turkeyFloat')
call s:Probe('2\.5E-2', 5, 'turkeyFloat')
call s:Probe('1_000\.000_1', 10, 'turkeyFloat')
call s:Probe('\.0\.1', 1, 'turkeyNumber')      " projection, not a float
call s:Probe('', 2, 'turkeyNumber')
call s:Probe('\<hiding\>', 0, 'turkeyIdentifier') " no longer import syntax
call s:Probe('\<infix\>', 0, 'turkeyIdentifier')  " a keyword only at top level
call s:Probe('''a''', 0, 'turkeyChar')
call s:Probe('''\\n''', 0, 'turkeyChar')
call s:Probe('', 1, 'turkeyEscape')
call s:Probe('u{1F600}', -2, 'turkeyChar')
call s:Probe('escapes', -1, 'turkeyString')
call s:Probe('u{1F600}', 0, 'turkeyEscape')    " the string's escape this time
call s:Probe('}{', 0, 'turkeyString')          " braces inside a string
call s:Probe('not a comment', -6, 'turkeyString')
call s:Probe('\\_', 0, 'turkeyOperator')       " a placeholder function
call s:Probe('\<for\>', 0, 'turkeyRepeat')
call s:Probe('\<in\>', 0, 'turkeyRepeat')
call s:Probe('\<var\> i', 0, 'turkeyKeyword')
call s:Probe('\<if\>', 0, 'turkeyConditional')
call s:Probe('\<else\>', 0, 'turkeyConditional')
call s:Probe('\<while\>', 0, 'turkeyRepeat')
call s:Probe('\<True\>', 0, 'turkeyBoolean')
call s:Probe('\<break\>', 0, 'turkeyStatement')
call s:Probe('\<loop\>', 0, 'turkeyRepeat')
call s:Probe('\<return\>', 0, 'turkeyStatement')
call s:Probe('\<None\>', 0, 'turkeyConstructor')
call s:Probe('\<Some\>', 0, 'turkeyConstructor')
call s:Probe('\.\.', 0, 'turkeyOperator')      " record pattern rest
call s:Probe('+--', 0, 'turkeyOperator')       " a run stops before --
call s:Probe('', 0, 'turkeyLineComment')
call s:Probe('\<do\>', 0, 'turkeyKeyword')
call s:Probe(')?$', 1, 'turkeyOperator')       " the ? in Some(41)?
call s:Probe('fun(x)', 0, 'turkeyKeyword')
call s:Probe('--- after code', 0, 'turkeyLineComment')

call writefile(s:out, $TURKEY_OUT . '/syntax.out')
qa!
