" Autoloaded functions for the Turkey filetype plugin.
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g

let s:save_cpo = &cpo
set cpo&vim

" Returns the command that runs the compiler: `b:turkey_command`, else
" `g:turkey_command`, else `turkey` on 'PATH'. It is the executable's name or
" path alone, with no arguments: callers quote it as one word.
function! turkey#Command() abort
  return get(b:, "turkey_command", get(g:, "turkey_command", "turkey"))
endfunction

function! s:Error(lines) abort
  echohl ErrorMsg
  for line in a:lines
    echomsg line
  endfor
  echohl None
endfunction

" Formats the current buffer with `turkey format`, which reads it on standard
" input, so a buffer with no file and unsaved changes are formatted alike.
" Leaves the buffer as it was and reports the compiler's message if the
" buffer does not parse or the formatter refuses it. Otherwise changes only
" the lines that differ, as one undoable change, and keeps the view. Returns
" 1 if the buffer is formatted now and 0 if it could not be formatted. Does
" not write the file.
function! turkey#Format() abort
  let command = turkey#Command()
  if !executable(command)
    call s:Error(["TurkeyFormat: cannot run '" . command . "'; set g:turkey_command"])
    return 0
  endif

  " Name the buffer's file, so that messages name it as `turkey check`
  " would. A buffer with none is `<stdin>`.
  let cmdline = shellescape(command) . " format"
  if expand("%") !=# ""
    let cmdline .= " --stdin-name " . shellescape(expand("%"))
  endif
  let before = getline(1, "$")
  " system() gives standard output and standard error together. That is
  " safe because `turkey format -` writes to only one of them: the program
  " on success, and the reason otherwise.
  let output = system(cmdline . " -", join(before, "\n") . "\n")
  let status = v:shell_error
  if status != 0
    call s:Error(["TurkeyFormat: '" . command . " format' exited with status " . status]
          \ + split(output, "\n"))
    return 0
  endif
  let after = split(output, "\n", 1)[: -2]
  if after ==# before
    return 1
  endif

  " Replace only the lines between the unchanged first and last ones, so
  " that marks, folds and signs outside them stay put.
  let n = len(before)
  let m = len(after)
  let first = 0
  while first < n && first < m && before[first] ==# after[first]
    let first += 1
  endwhile
  let last = 0
  while last < n - first && last < m - first && before[n - 1 - last] ==# after[m - 1 - last]
    let last += 1
  endwhile
  let oldcount = n - first - last
  let newcount = m - first - last
  let common = min([oldcount, newcount])

  let view = winsaveview()
  if common > 0
    call setline(first + 1, after[first : first + common - 1])
  endif
  if newcount > oldcount
    call append(first + common, after[first + common : first + newcount - 1])
  elseif oldcount > newcount
    silent execute (first + common + 1) . "," . (first + oldcount) . "delete _"
  endif
  call winrestview(view)
  return 1
endfunction

" Formats for `gq` and the other users of 'formatexpr'. Formats the whole
" buffer when the lines to format hold any code: a range of a program is not
" a program, so the formatter cannot read it alone. Leaves lines that hold
" only comments, and text typed past 'textwidth', to Vim's own formatting,
" which wraps them. The formatter never rewraps a comment, so formatting the
" whole buffer, as `gggqG` does, leaves a long comment long. Without syntax
" highlighting, the interior lines of a `{- -}` comment read as code.
function! turkey#FormatExpr() abort
  if v:char !=# "" || mode() =~# '^[iR]'
    return 1
  endif
  for l in range(v:lnum, v:lnum + v:count - 1)
    if !s:CommentOnly(l)
      call turkey#Format()
      return 0
    endif
  endfor
  return 1
endfunction

" Returns whether line a:lnum is blank or holds only comments.
function! s:CommentOnly(lnum) abort
  let line = getline(a:lnum)
  if line =~# '^\s*\%(--.*\)\=$'
    return 1
  endif
  if get(b:, "current_syntax", "") ==# "turkey"
    " A line whose first and last characters are both in a comment, unless
    " code sits between two comments: `{- a -} x {- b -}`.
    let names = map([match(line, '\S') + 1, match(line, '\s*$')],
          \ {_, c -> synIDattr(synID(a:lnum, c, 1), "name")})
    return names[0] =~? 'comment' && names[1] =~? 'comment'
          \ && line !~# '-}.*\S.*{-'
  endif
  " A line that starts a block comment and has no code after its end.
  return line =~# '^\s*{-' && line !~# '-}\s*\S'
endfunction

" Formats the buffer before it is written when `b:turkey_format_on_save` or
" `g:turkey_format_on_save` is set. A buffer that cannot be formatted is
" written as it is, after the error.
function! turkey#FormatOnSave() abort
  if get(b:, "turkey_format_on_save", get(g:, "turkey_format_on_save", 0))
    call turkey#Format()
  endif
endfunction

let &cpo = s:save_cpo
unlet s:save_cpo
