" Vim indent file for Turkey
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g
"
" Newlines in Turkey are significant by a token rule, not
" by layout, so indentation is purely cosmetic. The rules below follow the
" style of the compiler's own source in src/:
"
"   - a block `{` indents its body one shiftwidth from the line that opens
"     the statement (multi-line signatures and conditions measure from the
"     `fun`/`if` line, not from the continuation line the `{` ends);
"   - a record literal `Constructor {` measures its fields likewise;
"   - a `(` or `[` at end of line indents one shiftwidth, while one with
"     content after it aligns continuation lines under that content;
"   - argument lists of module/import/fun headers align the same way, but
"     call arguments after a trailing comma indent one level;
"   - a line that ends in an operator continues one shiftwidth in, or
"     aligns under a match arm's body or an if/while/for condition;
"   - a line that opens with `}`, `)` or `]` aligns with the opener's line;
"   - ADT alternatives (`= Ctor` / `| Ctor`) indent one level, and `else`
"     lines align with their `if`, even mid-expression.
"
" The bootstrap sources mix a couple of continuation styles in deeply
" nested constructor calls; this file picks one (see the rules above), so
" reindenting those spots with `gg=G` normalizes a handful of lines.

if exists("b:did_indent")
  finish
endif
let b:did_indent = 1

setlocal indentexpr=GetTurkeyIndent()
setlocal indentkeys=0{,0},0),0],!^F,o,O

let b:undo_indent = "setlocal indentexpr< indentkeys<"

if exists("*GetTurkeyIndent")
  finish
endif

let s:save_cpo = &cpo
set cpo&vim

" Skip over strings and comments when matching brackets.
let s:skip_expr = 'synIDattr(synID(line("."), col("."), 1), "name") =~? "comment\\|string\\|char"'

" How many lines a bracket scan may walk back before giving up.
let s:maxback = 400

function! s:CodePart(lnum) abort
  " The line with literals padded over and comments cut, so that trailing
  " characters can be tested without lookalikes inside `-- ...`, strings,
  " or char literals getting in the way. Literals pad with `x` rather than
  " truncate so that columns still line up with the real line, and with a
  " letter rather than spaces so a line ending in a literal does not look
  " like it ends with whatever came before it.
  let line = getline(a:lnum)
  let line = substitute(line, '''\([^''\\]\|\\.\)''', {m -> repeat('x', len(m[0]))}, 'g')
  let line = substitute(line, '"\([^"\\]\|\\.\)*"', {m -> repeat('x', len(m[0]))}, 'g')
  let line = substitute(line, '--.*$', '', '')
  let line = substitute(line, '{-.*$', '', '')
  return line
endfunction

function! s:IsComment(lnum) abort
  " The first non-blank character of the line is inside a comment.
  let line = getline(a:lnum)
  if line !~# '\S'
    return 0
  endif
  return synIDattr(synID(a:lnum, match(line, '\S') + 1, 1), "name") =~? 'comment'
endfunction

function! s:BlockComment(lnum) abort
  " That comment is (or is inside) a {- -} block comment rather than a
  " line comment: matched against the group names, never the line above.
  let line = getline(a:lnum)
  return synIDattr(synID(a:lnum, match(line, '\S') + 1, 1), "name") ==# 'turkeyBlockComment'
endfunction

function! s:ScanBack(lnum, col) abort
  " The innermost bracket opener left unmatched as of (a:lnum, a:col),
  " found by walking backward over `[](){}[]` with a stack. Returns
  " [lnum, col, char], or [0, 0, ''] when everything is balanced.
  " One combined pass is much cheaper than one searchpair() per bracket
  " kind. The scan gives up s:maxback lines up; beyond that the answer is
  " treated as "balanced", which only degrades indentation inside a block
  " that is hundreds of lines long.
  let save = winsaveview()
  call cursor(a:lnum, a:col)
  let stopline = a:lnum > s:maxback ? a:lnum - s:maxback : 0
  let stack = []
  let result = [0, 0, '']
  let flags = 'bcW'
  while 1
    let pos = searchpos('[](){}[]', flags, stopline, 0, s:skip_expr)
    if pos[0] == 0
      break
    endif
    let flags = 'bW'
    let ch = getline(pos[0])[pos[1] - 1]
    if ch =~# '[])}]'
      call add(stack, ch)
    elseif empty(stack)
      let result = [pos[0], pos[1], ch]
      break
    else
      call remove(stack, -1)
    endif
  endwhile
  call winrestview(save)
  return result
endfunction

function! s:UnmatchedOpenInLine(line) abort
  " Does the (comment- and literal-free) line leave a bracket open?
  " Local check only; lets the caller skip a backward scan entirely.
  let bs = substitute(a:line, '[^()\[\]{}]', '', 'g')
  let depth = 0
  for i in range(len(bs))
    if bs[i] =~# '[({\[]'
      let depth += 1
    elseif depth > 0
      let depth -= 1
    endif
  endfor
  return depth > 0
endfunction

function! s:IsHeaderOpen(open) abort
  " The opener is the list of a module header, an import, or a function
  " signature (text before it is just the keyword, or `fun` plus a name
  " with optional type parameters). Those align; call argument lists
  " indent one level instead.
  let prefix = strpart(s:CodePart(a:open[0]), 0, a:open[1] - 1)
  return prefix =~# '^\s*\(module\>\|import\>\|fun\s\+[A-Za-z_][A-Za-z0-9_'']*\(\[[^]]*\]\)\?\s*$\)'
endfunction

function! s:IsRecordBrace(open) abort
  " A `{` right after a constructor name is a record literal, whose fields
  " indent from the brace's own line. Any other `{` is a block, whose body
  " indents from the statement's first line. A type after `->` is a return
  " type, not a record: `-> Result {`, `-> Either Int Started {`,
  " `-> Option (Set.Set String) {`. Its words are separated by spaces, so
  " `-> Wrap(Point {` still reads as a record argument. The constructor
  " must start a word, or the digits of `x > 0xFFFF {` would read as one.
  let before = strpart(s:CodePart(a:open[0]), 0, a:open[1] - 1)
  if before =~# '->\s*[A-Z][A-Za-z0-9_''.]*\(\s\+[A-Za-z0-9_''.()]\+\)*\s*$'
    return 0
  endif
  return before =~# '\(^\|[^A-Za-z0-9_'']\)[A-Z][A-Za-z0-9_'']*\s*$'
endfunction

function! s:ContentIndent(open) abort
  " Column (0-based) of the first non-space after the opener.
  let line = getline(a:open[0])
  let i = a:open[1]
  while i < len(line) && line[i] =~# '\s'
    let i += 1
  endwhile
  return i
endfunction

function! s:LastArgIndent(open) abort
  " Column (0-based) of the argument after the last top-level comma on the
  " opener's line: where an operator-continuation of a call aligns
  " (`oops(p, "..."` / `+ describe(cur(p))` under the `"`).
  let kcode = s:CodePart(a:open[0])
  let rest = strpart(kcode, a:open[1])
  let depth = 0
  let lastcomma = -1
  for i in range(len(rest))
    let c = rest[i]
    if c =~# '[({\[]'
      let depth += 1
    elseif c =~# '[)}\]]'
      let depth -= 1
    elseif c ==# ',' && depth == 0
      let lastcomma = i
    endif
  endfor
  let line = getline(a:open[0])
  let i = a:open[1] + lastcomma + 1
  while i < len(line) && line[i] =~# '\s'
    let i += 1
  endwhile
  if i >= len(line)
    return s:ContentIndent(a:open)
  endif
  return i
endfunction

function! s:RecordBase(open) abort
  " The line a record literal `{` measures its fields from: its own line,
  " unless that line continues a declaration header, in which case the
  " header's first line:
  "   fun newLowerer(decls : DeclTable,
  "                  types : TypeTable) -> Lowerer = Lowerer {
  "       decls = decls, ...
  let l = a:open[0]
  while l > 1 && indent(l) > 0
    let r = s:ScanBack(l - 1, col([l - 1, '$']))
    if r[0] == 0 || r[2] ==# '{' || !s:IsHeaderOpen(r)
      return l
    endif
    let l = r[0]
  endwhile
  return l
endfunction

function! s:BraceBase(open) abort
  " The line a block `{` measures its body's indent from: the nearest line
  " of the statement chain (walking continuations and brackets) that opens
  " with a block keyword, else the brace's own line. The keyword rule is
  " what puts
  "   fun f(a,
  "         b) {
  " and a multi-line `if` condition back at the statement's indent, while
  "   CIf(c, t, e) -> CIf(m.expr(c), m.expr(t),
  "       match otherwise {
  " keeps the match body under the `match` line.
  let l = a:open[0]
  if indent(l) == 0
    return l
  endif
  while l > 1
    let code = s:CodePart(l)
    if code =~# '^\s*\(if\|else\|while\|for\|loop\|match\|fun\|do\)\>'
      return l
    endif
    let r = s:ScanBack(l - 1, col([l - 1, '$']))
    if r[0] > 0 && r[2] !=# '{'
      let l = r[0]
      continue
    endif
    let kleads = code =~# '^\s*\(->\|||\|==\|!=\|[+*/%<>&:.^]\)' && code !~# '^\s*\.\.'
    let above = s:CodePart(prevnonblank(l - 1))
    " A definition's `=` ends its line when the body starts on the next, so
    " that line begins a fresh expression and its `{` measures from it:
    "   fun optional(body) =
    "       Builder(fun(asm) {
    if !kleads && above =~# '\(^\|[^-=+*/%<>&|:~.!]\)=\s*$'
      return l
    endif
    if kleads || above =~# '[-=+*/%<>&|:~.!,^]\s*$'
      let l = prevnonblank(l - 1)
    else
      return l
    endif
  endwhile
  return l
endfunction

function! GetTurkeyIndent() abort
  let lnum = v:lnum
  let cline = getline(lnum)
  let sw = shiftwidth()

  " A line that opens with a closer aligns with the line of its opener
  " (`}` of a match block, `})` ending a call that took a fun literal). A
  " `{` opener's line is taken from its statement start, as above.
  let closer = matchstr(cline, '^\s*\zs[}\])]')
  if closer !=# ''
    let ccol = matchend(cline, '^\s*')   " chars before the closer
    if ccol > 0
      let r = s:ScanBack(lnum, ccol)
    elseif lnum > 1
      let r = s:ScanBack(lnum - 1, col([lnum - 1, '$']))
    else
      let r = [0, 0, '']
    endif
    let want = closer ==# '}' ? '{' : closer ==# ')' ? '(' : '['
    if r[0] > 0 && r[2] ==# want
      if r[2] ==# '{'
        return indent(s:IsRecordBrace(r) ? s:RecordBase(r) : s:BraceBase(r))
      endif
      return indent(r[0])
    endif
  endif

  let prevnum = prevnonblank(lnum - 1)
  if prevnum == 0
    return 0
  endif

  " A line that opens with `else` aligns with the column of the `if` whose
  " then-block ended on the previous code line, even when that `if` sits
  " mid-expression (`-> if n == 29 { "x29" }` / `else if ...`).
  if cline =~# '^\s*else\>' && s:CodePart(prevnum) =~# '}\s*$'
    let ob = s:ScanBack(prevnum, matchend(getline(prevnum), '.*}') - 1)
    " An `else if` line attaches to the chain's first `if`, not its own.
    " Its condition may run over several lines, so the `else` is looked for
    " on the first line of the brace's statement, not the brace's own line.
    while ob[0] > 0 && ob[2] ==# '{' && s:CodePart(s:BraceBase(ob)) =~# '^\s*else\>'
      let pl = prevnonblank(s:BraceBase(ob) - 1)
      if pl == 0 || s:CodePart(pl) !~# '}\s*$'
        let ob = [0, 0, '']
        break
      endif
      let ob = s:ScanBack(pl, matchend(getline(pl), '.*}') - 1)
    endwhile
    if ob[0] > 0 && ob[2] ==# '{'
      let save = winsaveview()
      call cursor(ob[0], ob[1])
      let ipos = searchpos('\<if\>', 'bnW', s:BraceBase(ob))
      call winrestview(save)
      if ipos[1] > 0
        return ipos[1] - 1
      endif
    endif
  endif

  " A line inside a block comment is reindented to column 0: with nothing
  " but comments on the lines above, there is no code to align it with,
  " and leaving the previous indent would glue it to an unrelated block.
  if s:BlockComment(lnum)
    return 0
  endif

  " A comment-only line among ADT alternatives keeps their indent. Whether
  " it is among them is told from what FOLLOWS: the next code line starts
  " with `=` or `|`. (A comment before a fresh declaration falls through
  " to the normal rules and gets the declaration's indent.)
  if s:IsComment(lnum)
    let m = nextnonblank(lnum + 1)
    while m > 0 && s:IsComment(m)
      let m = nextnonblank(m + 1)
    endwhile
    if m > 0 && s:CodePart(m) =~# '^\s*[=|]'
      let n = prevnum
      while n > 0 && s:IsComment(n)
        let n = prevnonblank(n - 1)
      endwhile
      " After the `type` line the alternatives are one level in; after an
      " alternative, with or without its own `=` or `|` (`type T =` puts the
      " first one on a line of its own), they keep its indent.
      if n > 0
        return indent(n) + (s:CodePart(n) =~# '^\s*type\>' ? sw : 0)
      endif
    endif
  endif

  " After a comment, whatever comes next keeps the comment's indent.
  if s:IsComment(prevnum)
    return indent(prevnum)
  endif

  " Base indent: governed by the innermost unmatched opener, if any. A
  " complete line at column 0 (balanced brackets, no continuation) means
  " the current line is at toplevel too -- skip the backward scan, which
  " would otherwise walk the whole file above every toplevel declaration.
  let pline = s:CodePart(prevnum)
  let open = [0, 0, '']
  if indent(prevnum) > 0 || s:UnmatchedOpenInLine(pline)
    let open = s:ScanBack(prevnum, col([prevnum, '$']))
  endif
  if open[0] == 0
    let base = 0
  elseif open[2] ==# '{'
    " A record literal's fields and a block's body indent one level from
    " their brace's base line (see s:RecordBase / s:BraceBase).
    let base = indent(s:IsRecordBrace(open) ? s:RecordBase(open) : s:BraceBase(open)) + sw
  else
    let rest = strpart(getline(open[0]), open[1])
    if rest =~# '^\s*\($\|--\|{-\)'
      " `(` or `[` at end of line: one level in.
      let base = indent(open[0]) + sw
    else
      " `(` or `[` with content after it: align under the content.
      let base = open[1]
    endif
  endif

  " ADT alternatives (`type Kind` / `= KInt` / `| KFloat`) indent one level,
  " except inside a `{ ... }` (e.g. `|` between match-arm patterns), where
  " the block's indent already governs.
  if cline =~# '^\s*[=|]' && open[2] !=# '{'
    return base + sw
  endif

  if pline =~# '[{(\[]\s*$'
    " An opener at end of line is already accounted for in `base`.
    return base
  endif

  " A continuation line: the previous line ends with an operator or comma
  " (neither can end a production), or this one opens with an operator it
  " cannot start one with. (`-` and `!` can start a statement, and `..`
  " can start a record pattern, so they are absent from the leading set.)
  " A return type wrapped onto its own line indents two levels from the
  " `fun`, so the signature does not line up with the body under it:
  "   fun start(argv : Array String,
  "             directory : Option String)
  "           -> Either Int Started {
  if cline =~# '^\s*->' && pline =~# ')\s*$'
    let save = winsaveview()
    call cursor(prevnum, matchend(pline, '.*)'))
    let po = searchpair('(', '', ')', 'bnW', s:skip_expr, max([1, prevnum - s:maxback]))
    call winrestview(save)
    if po > 0 && s:CodePart(po) =~# '^\s*\(foreign\s\+\S.*\)\=fun\>'
      return indent(po) + 2 * sw
    endif
  endif

  let leadsop = cline =~# '^\s*\(->\|||\|==\|!=\|[+*/%<>&:.^]\)' && cline !~# '^\s*\.\.'
  let trailsop = pline =~# '[-=+*/%<>&|:~.!,^]\s*$'
  if leadsop || trailsop
    if open[2] ==# '(' || open[2] ==# '['
      " Inside a bracketed list. A `+ ...`-style continuation aligns under
      " the last argument; a line after a trailing comma or `=` aligns for
      " headers and list literals but indents one level past the opener's
      " line for call arguments.
      if strpart(s:CodePart(open[0]), open[1]) =~# '^\s*$'
        " The opener ends its own line; `base` already covers this.
        return base
      endif
      if leadsop
        return s:LastArgIndent(open)
      endif
      if open[2] ==# '('
        if s:IsHeaderOpen(open)
          return s:ContentIndent(open)
        endif
        let before = strpart(s:CodePart(open[0]), 0, open[1] - 1)
        if before =~# '[(:,]\s*$'
          " A list that is itself a call argument indents one level.
          return indent(open[0]) + sw
        endif
        return s:ContentIndent(open)
      endif
      let before = strpart(s:CodePart(open[0]), 0, open[1] - 1)
      if before =~# '[(:,]\s*$'
        " A list that is itself a call argument indents one level.
        return indent(open[0]) + sw
      endif
      return s:ContentIndent(open)
    endif
    if open[2] ==# '{' && pline =~# ',\s*$'
      " Record fields sit at the block indent; the comma just separates.
      " When the first field shares the brace's line, the rest align under
      " it, as a call's arguments do.
      if s:IsRecordBrace(open) && strpart(s:CodePart(open[0]), open[1]) =~# '\S'
        return s:ContentIndent(open)
      endif
      return base
    endif
    " Governed by a { block or by nothing. If the statement is a match arm
    " the continuation aligns under the arm's body:
    "     MovZ(...) -> "movz " + showReg(d, W64)
    "                  + (if shift == 0 { "" }
    " A `->` followed by a definition `=` is a signature, not an arm.
    let k = prevnum
    while k > 1
      let kcode = s:CodePart(k)
      let kleads = kcode =~# '^\s*\(||\|==\|!=\|[+*/%<>&:.^]\)' && kcode !~# '^\s*\.\.'
      if !kleads && s:CodePart(k - 1) !~# '[-=+*/%<>&|:~.!^]\s*$'
        break
      endif
      let k -= 1
    endwhile
    let kcode = s:CodePart(k)
    let a = matchend(kcode, '.*->')
    if a >= 0 && cline !~# '^\s*->' && strpart(kcode, a) !~# '\([^=]\|^\)=\($\|[^=]\)'
      let kreal = getline(k)
      while a < len(kreal) && kreal[a] =~# '\s'
        let a += 1
      endwhile
      " Nothing after the arrow: the body starts fresh on the next line.
      if a < len(kreal)
        return a
      endif
    endif
    " A continued condition aligns under the condition, in an `else if` as
    " in an `if`:
    "   if !owns(ChStmt(s))
    "      && !(flow && ...) {
    "   } else if len(argv) > 1
    "             || len(first) == 0 {
    let c = matchend(kcode, '^\s*\(}\s*\)\=\(else\s\+\)\=\(if\|while\|for\)\s\+')
    if c >= 0
      return c
    endif
    return base + sw
  endif
  return base
endfunction

let &cpo = s:save_cpo
unlet s:save_cpo
