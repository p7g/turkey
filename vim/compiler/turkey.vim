" Vim compiler file for Turkey
" Compiler:     turkey check
" Maintainer:   p7g
"
" `:compiler turkey` then `:make` runs `turkey check` on the current file and
" fills the quickfix list with its errors and lint warnings. Arguments to
" `:make` go after the file: `:make --deny-warnings`. The command is
" `b:turkey_command`, else `g:turkey_command`, else `turkey` on 'PATH', as
" set when `:compiler turkey` runs; set it before then, or run `:compiler
" turkey` again after changing it.

if exists("current_compiler")
  finish
endif
let current_compiler = "turkey"

if exists(":CompilerSet") != 2
  command -nargs=* CompilerSet setlocal <args>
endif

let s:save_cpo = &cpo
set cpo&vim

" `%:S` quotes the file name for the shell, so a path with spaces is one word.
execute "CompilerSet makeprg=" . escape(shellescape(turkey#Command()) . " check %:S", ' \|"')

" A diagnostic is `FILE:LINE:COL: KIND: MESSAGE`, where KIND is `warning`,
" `error`, or a phase's error (`parse error`, `type error`), and a lint's
" message ends in `[check-name]`, which stays in the text. The line that
" counts them (`1 error, 2 warnings`) is dropped.
CompilerSet errorformat=
      \%f:%l:%c:\ %tarning:\ %m,
      \%f:%l:%c:\ %trror:\ %m,
      \%f:%l:%c:\ %*[a-z]\ %trror:\ %m,
      \%-G%\\d%\\+\ error%.%#,
      \%-G%\\d%\\+\ warning%.%#

let &cpo = s:save_cpo
unlet s:save_cpo
