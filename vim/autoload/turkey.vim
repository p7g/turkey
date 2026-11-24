" Autoloaded functions for the Turkey filetype plugin.
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g

let s:save_cpo = &cpo
set cpo&vim

" Returns the command that runs the compiler: `b:turkey_command`, else
" `g:turkey_command`, else `turkey` on 'PATH'.
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

" Formats the current buffer with `turkey fmt`. Leaves the buffer as it was
" and reports the compiler's message if the buffer does not parse or the
" formatter refuses it. Otherwise changes only the lines that differ, as one
" undoable change, and keeps the view. Returns 1 if the buffer is formatted
" now and 0 if it could not be formatted. Does not write the file.
function! turkey#Format() abort
  let command = turkey#Command()
  if !executable(command)
    call s:Error(["TurkeyFmt: cannot run '" . command . "'; set g:turkey_command"])
    return 0
  endif

  " `turkey fmt` rewrites files in place and does not read standard input,
  " so format a copy of the buffer in a directory of its own. The copy keeps
  " the buffer's file name, so that the compiler's messages name it.
  let dir = tempname()
  call mkdir(dir, "p")
  let name = expand("%:t")
  let copy = dir . "/" . (name ==# "" ? "buffer.gob" : name)
  let before = getline(1, "$")
  call writefile(before, copy)
  let output = system(shellescape(command) . " fmt " . shellescape(copy) . " 2>&1")
  let status = v:shell_error
  let after = status == 0 ? readfile(copy) : []
  call delete(dir, "rf")

  if status != 0
    " Name the buffer's file in the messages, not the copy.
    let shown = expand("%") ==# "" ? "[No Name]" : expand("%")
    let lines = map(split(output, "\n"), {_, l -> substitute(l, '\V' . escape(copy, '\'), escape(shown, '\&~'), 'g')})
    call s:Error(["TurkeyFmt: '" . command . " fmt' exited with status " . status] + lines)
    return 0
  endif
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

" Formats the buffer before it is written when `b:turkey_fmt_on_save` or
" `g:turkey_fmt_on_save` is set. A buffer that cannot be formatted is
" written as it is, after the error.
function! turkey#FormatOnSave() abort
  if get(b:, "turkey_fmt_on_save", get(g:, "turkey_fmt_on_save", 0))
    call turkey#Format()
  endif
endfunction

let &cpo = s:save_cpo
unlet s:save_cpo
