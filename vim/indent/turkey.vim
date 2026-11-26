" Vim indent file for Turkey
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g
"
" Newlines in Turkey are significant by a token rule, not by layout, so
" indentation is cosmetic. The rules below follow `turkey format`, so that a
" line typed in Vim starts where the formatter would put it, and `gg=G` on a
" formatted file changes nothing:
"
"   - one level is one tab (the ftplugin sets 'noexpandtab');
"   - after a bracket that ends a line, what follows indents one level from
"     that line, and the closing bracket goes back to that line's indent;
"   - a block `{` measures its body from the `if`, `while` or `for` line when
"     the condition runs over several lines, and from the line whose bracket a
"     leading `)` or `]` closes (`) -> T {`, `](x : a) -> T {`);
"   - a line after one that ends in `=` or `->` indents one level;
"   - a line that starts with a binary operator, or follows one that ends in
"     `-`, continues one level in, or two after an `if`, `while` or `for`
"     line, so that the condition does not line up with the body. A tighter
"     operator nests one level under a looser one, and operators of one
"     precedence line up;
"   - the conditions of an `if let` or `while let` list continue two levels in;
"   - a sum type's `= A` and `| B` lines indent one level, and an
"     or-pattern's `| B` lines line up with its first pattern;
"   - an own-line comment takes the indent of the code after it, or of a
"     block's contents before its `}`. A block comment's interior lines keep
"     their indent.
"
" Alignment padding (a match's `->`, a record's `:` and `=`, trailing
" comments), hugging and the choice of where to break are not indentation;
" `:TurkeyFormat` does those.

if exists("b:did_indent")
  finish
endif
let b:did_indent = 1

setlocal indentexpr=GetTurkeyIndent()
" Besides the closers, reindent when a line starts with `|`, `||`, `|>`, `&&`
" or `=`, which move it to a continuation or alternative's indent.
setlocal indentkeys=0{,0},0),0],!^F,o,O,0=\|,0=\|\|,0=\|>,0=&&,0==

" The buffer's own 'preserveindent', which GetTurkeyIndent switches on for a
" multi-line string's lines only.
let b:turkey_preserveindent = &l:preserveindent

let b:undo_indent = "setlocal indentexpr< indentkeys< preserveindent<"
      \ . " | unlet! b:turkey_preserveindent"

if exists("*GetTurkeyIndent")
  finish
endif

let s:save_cpo = &cpo
set cpo&vim

" How many lines a bracket scan may walk back before giving up. Beyond that
" the line reads as top level, which only degrades indentation inside a
" bracket that is thousands of lines long.
let s:maxback = 2000

" The characters an operator is made of.
let s:opchars = '[!#$%&*+/<=>@^|~-]'

" Binding strength of each built-in and library operator: higher binds
" tighter. The language's groups are a partial order, and two unrelated
" groups cannot share a chain without parentheses, so any linear extension
" orders the operators a chain can hold. An operator a module declares
" itself gets the middle rank.
let s:prec = {
      \ '|>': 1, '|>>': 1, '<&>': 1, '<$>': 1,
      \ '<|>': 2,
      \ '||': 3,
      \ '&&': 4,
      \ '==': 5, '!=': 5,
      \ '<': 6, '<=': 6, '>': 6, '>=': 6,
      \ '+': 7, '-': 7, '|': 7, '^': 7, '<>': 7,
      \ '*': 8, '/': 8, '%': 8, '&': 8,
      \ '<<': 9, '>>': 9,
      \ }

" A line's text with its literals and comments masked, by text. Masking is a
" function of the text alone, and each line is masked many times over as the
" lines below it are indented.
let s:masked = {}

function! s:Pad(m) abort
  return repeat('x', len(a:m[0]))
endfunction

function! s:PadLiterals(line) abort
  " Returns a:line with its characters, strings and one-line block comments
  " padded over with `x`. Pad a multi-line string's `"""` first, so that its
  " quotes do not pair up with those of a one-line string after it.
  let line = substitute(a:line, '"""', 'xxx', 'g')
  let line = substitute(line, '''\([^''\\]\|\\.\|\\u{\x\+}\)''', function('s:Pad'), 'g')
  let line = substitute(line, '"\([^"\\]\|\\.\)*"', function('s:Pad'), 'g')
  " Block comments nest, so pad the innermost pairs first.
  while 1
    let padded = substitute(line, '{-\%(\%({-\|-}\)\@!.\)*-}', function('s:Pad'), 'g')
    if padded ==# line
      return line
    endif
    let line = padded
  endwhile
endfunction

function! s:CommentEnd(line) abort
  " Returns the column just past a `-}` on a:line that ends a block comment
  " begun on a line above, or -1. Only a comment can hold an unpaired `-}`,
  " so everything before it is comment too.
  return matchend(s:PadLiterals(a:line), '.*-}')
endfunction

function! s:Mask(line) abort
  " Returns a:line with its literals and comments padded over with `x`, and
  " its line comment cut. Pad rather than cut the rest, so that columns
  " still line up with the real line, and with a letter, so that a line that
  " ends in a literal does not look like it ends in whatever came before it.
  let line = s:PadLiterals(a:line)
  let close = matchend(line, '.*-}')
  if close >= 0
    let line = repeat('x', close) . strpart(line, close)
  endif
  let line = substitute(line, '--.*$', '', '')
  return substitute(line, '{-.*$', '', '')
endfunction

function! s:CodePart(lnum) abort
  " Returns the line with literals and comments masked (see s:Mask), so
  " that its first and last characters, and its brackets, can be tested
  " without lookalikes inside them getting in the way.
  let line = getline(a:lnum)
  if !has_key(s:masked, line)
    if len(s:masked) > 20000
      let s:masked = {}
    endif
    let s:masked[line] = s:Mask(line)
  endif
  return s:masked[line]
endfunction

function! s:HasSyntax() abort
  return get(b:, "current_syntax", "") ==# "turkey"
endfunction

function! s:InBlockComment(lnum) abort
  " Returns whether the line starts inside a `{- -}` comment that began on
  " an earlier line. A line that starts with `{-` at column 1 begins one.
  " Without syntax highlighting, only a line that ends the comment is known
  " to be inside it: telling an interior line from code would mean reading
  " the file from its start.
  let line = getline(a:lnum)
  if line =~# '^{-'
    return 0
  endif
  if s:HasSyntax()
    return synIDattr(synID(a:lnum, 1, 1), "name") ==# 'turkeyBlockComment'
  endif
  return s:CommentEnd(line) >= 0
endfunction

function! s:InMultiString(lnum) abort
  " Returns whether the line starts inside a multi-line string: whether it is
  " one of the string's lines of text or its closing line. Without syntax
  " highlighting, reads back to the nearest line that settles it, at most
  " s:maxback lines. A line that ends in `"""` after other code opens a
  " string, and one that starts with `"""` and goes on closes one. A line of
  " only `"""` could do either, so each one in between flips the answer.
  if s:HasSyntax()
    " A line of text can start with a hole, which is innermost there.
    return index(map(synstack(a:lnum, 1), 'synIDattr(v:val, "name")'),
          \ 'turkeyMultiString') >= 0
  endif
  let flips = 0
  let l = a:lnum - 1
  let stop = a:lnum > s:maxback ? a:lnum - s:maxback : 1
  while l >= stop
    let line = getline(l)
    if line =~# '^\s*"""\s*$'
      let flips += 1
    elseif line =~# '^\s*"""'
      return flips % 2
    elseif line =~# '"""\s*$' && line !~# '\\"""\s*$'
      return 1 - flips % 2
    endif
    let l -= 1
  endwhile
  return flips % 2
endfunction

function! s:IsComment(lnum) abort
  " Returns whether the line's first non-blank character is in a comment.
  let line = getline(a:lnum)
  if line !~# '\S'
    return 0
  endif
  if s:HasSyntax()
    return synIDattr(synID(a:lnum, match(line, '\S') + 1, 1), "name") =~? 'comment\|directive'
  endif
  return line =~# '^\s*\%(--\|{-\)' || s:InBlockComment(a:lnum)
endfunction

function! s:ScanCode(lnum) abort
  " Returns the line as the bracket scan reads it: masked, and empty when
  " it lies wholly inside a block comment.
  if getline(a:lnum) !~# '-}' && s:InBlockComment(a:lnum)
    return ''
  endif
  " A multi-line string's lines of text hold no code. Its closing line can,
  " after the `"""`.
  if s:InMultiString(a:lnum) && getline(a:lnum) !~# '^\s*"""'
    return ''
  endif
  return s:CodePart(a:lnum)
endfunction

function! s:PrevCode(lnum) abort
  " Returns the nearest line above that holds code, or 0.
  let l = prevnonblank(a:lnum - 1)
  while l > 0 && s:IsComment(l)
    let l = prevnonblank(l - 1)
  endwhile
  return l
endfunction

function! s:NextCode(lnum) abort
  " Returns the nearest line below that holds code, or 0.
  let l = nextnonblank(a:lnum + 1)
  while l > 0 && s:IsComment(l)
    let l = nextnonblank(l + 1)
  endwhile
  return l
endfunction

function! s:ScanBack(lnum, col) abort
  " Returns [lnum, col, char] of the innermost bracket left open before
  " (a:lnum, a:col), or [0, 0, ''] when everything before it is balanced.
  " Brackets inside literals and comments are masked (see s:ScanCode), so
  " this needs no syntax highlighting. One count serves all three kinds:
  " the formatter's input parses, so its brackets pair up.
  let stop = a:lnum > s:maxback ? a:lnum - s:maxback : 1
  let depth = 0
  let l = a:lnum
  let code = strpart(s:ScanCode(l), 0, a:col - 1)
  while 1
    let brackets = substitute(code, '[^][(){}]', '', 'g')
    let i = len(brackets) - 1
    while i >= 0
      if brackets[i] =~# '[])}]'
        let depth += 1
      elseif depth > 0
        let depth -= 1
      else
        " The opener is the line's (i + 1)th bracket.
        return [l, match(code, '[][(){}]', 0, i + 1) + 1, brackets[i]]
      endif
      let i -= 1
    endwhile
    let l -= 1
    if l < stop
      return [0, 0, '']
    endif
    let code = s:ScanCode(l)
  endwhile
endfunction

function! s:LeavesOpen(code) abort
  " Returns whether the (comment- and literal-free) line leaves a bracket
  " open. A local check, which lets the caller skip a backward scan.
  let bs = substitute(a:code, '[^()\[\]{}]', '', 'g')
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

function! s:LastUnmatchedCloser(lnum) abort
  " Returns the column (1-based) of the last closing bracket on the line
  " whose opener is on an earlier line, or 0. Its opener is the outermost of
  " those the line closes.
  let code = s:CodePart(a:lnum)
  let depth = 0
  let found = 0
  for i in range(len(code))
    let c = code[i]
    if c =~# '[({\[]'
      let depth += 1
    elseif c =~# '[)}\]]'
      if depth > 0
        let depth -= 1
      else
        let found = i + 1
      endif
    endif
  endfor
  return found
endfunction

function! s:LogicalStart(lnum) abort
  " Returns the line that the expression ending on a:lnum starts on: the
  " line itself, unless it closes brackets opened above, in which case the
  " line of the outermost of those openers, and so on up. A multi-line
  " string's closing line continues the line the string opens on, whose
  " indent is the code's rather than the string's.
  let l = a:lnum
  let n = 0
  while n < s:maxback
    if getline(l) =~# '^\s*"""' && s:InMultiString(l)
      while l > 1 && s:InMultiString(l)
        let l -= 1
      endwhile
    endif
    let c = s:LastUnmatchedCloser(l)
    if c == 0
      return l
    endif
    let o = s:ScanBack(l, c)
    if o[0] == 0 || o[0] >= l
      return l
    endif
    let l = o[0]
    let n += 1
  endwhile
  return l
endfunction

function! s:LeadingOp(code) abort
  " Returns the binary operator the line starts with, or ''. `=` starts a
  " sum type's first alternative, and `-` and `!` a negation, so none of
  " them counts, and neither do `..` (a record pattern's rest) or `->`. A
  " lone `|` counts, though it may start an alternative or an or-pattern
  " rather than continue a bitwise or; the caller tells those apart.
  let op = matchstr(a:code, '^\s*\zs' . s:opchars . '\+\ze\%(\s\|$\)')
  return op =~# '^\%(=\|-\|!\|->\|\~\)$' ? '' : op
endfunction

function! s:TrailingOp(code) abort
  " Returns the operator the line ends with, `=` and `->` included, or ''.
  return matchstr(a:code, '\%(^\|\s\)\zs' . s:opchars . '\+\ze\s*$')
endfunction

function! s:OpOf(lnum) abort
  " Returns the binary operator that makes the line continue the one above:
  " the operator it starts with, or the one the code line above ends with
  " (only `-` is broken after), or '' when the line continues nothing.
  let op = s:LeadingOp(s:CodePart(a:lnum))
  if op !=# ''
    return op
  endif
  let p = s:PrevCode(a:lnum)
  if p == 0
    return ''
  endif
  let op = s:TrailingOp(s:CodePart(p))
  return op =~# '^\%(=\|->\)$' ? '' : op
endfunction

function! s:Outside(code) abort
  " Returns the line with the contents of its balanced brackets removed.
  let code = a:code
  while 1
    let shorter = substitute(code, '([^()]*)\|\[[^][]*\]\|{[^{}]*}', '', 'g')
    if shorter ==# code
      return code
    endif
    let code = shorter
  endwhile
endfunction

function! s:BitwiseOr(prev) abort
  " Returns whether a `| B` line after a:prev continues a bitwise or rather
  " than an or-pattern. Its first operand's line is a body when a `let`,
  " `var`, `return`, `=` or `->` comes before it, on that line outside
  " brackets or at the end of the line above, or when it is in a block that
  " is not a `match`'s, where no pattern can start a line. An or-pattern's
  " first line has none of these: its `->` comes after the last pattern.
  let k = s:LogicalStart(a:prev)
  let n = 0
  while n < s:maxback && s:CodePart(k) =~# '^\s*|\%([^|>]\|$\)'
    let p = s:PrevCode(k)
    if p == 0
      return 0
    endif
    let k = s:LogicalStart(p)
    let n += 1
  endwhile
  let outside = s:Outside(s:CodePart(k))
  if outside =~# '^\s*\%(let\|var\|return\)\>' || outside =~# '\s\%(=\|->\)\s'
    return 1
  endif
  let p = s:PrevCode(k)
  if p > 0 && s:TrailingOp(s:CodePart(p)) =~# '^\%(=\|->\)$'
    return 1
  endif
  let o = s:ScanBack(k, 1)
  return o[2] ==# '{' && strpart(s:CodePart(o[0]), 0, o[1] - 1) !~# '\<match\>'
endfunction

function! s:Prec(op) abort
  return get(s:prec, a:op, 7)
endfunction

function! s:IsCondHead(lnum) abort
  " Returns whether the line starts an `if`, `else if`, `while` or `for`.
  return s:CodePart(a:lnum) =~# '^\s*\%(}\s*\)\=\%(else\s\+\)\=\%(if\|while\|for\)\>'
endfunction

function! s:StatementHead(lnum) abort
  " Returns the first line of the statement a:lnum belongs to, walking up
  " over operator continuations and lines after a trailing comma, and
  " stopping early at an `if`, `while` or `for` line.
  let k = a:lnum
  let n = 0
  while n < s:maxback && !s:IsCondHead(k)
    let p = s:PrevCode(k)
    if p == 0
      break
    endif
    if s:OpOf(k) ==# '' && s:CodePart(p) !~# ',\s*$'
      break
    endif
    let k = s:LogicalStart(p)
    let n += 1
  endwhile
  return k
endfunction

function! s:BraceHead(open) abort
  " Returns the line a block `{` measures its body and its `}` from: the
  " statement's `if`, `while` or `for` line if it has one, so a multi-line
  " condition does not push the body in. Otherwise the brace's own line, or
  " the line of the opener a closer before the brace matches, as in
  " `) -> T {`. Any other `{` on a continuation line measures from that
  " line, the way the formatter nests it.
  let own = s:LogicalStart(a:open[0])
  let head = s:StatementHead(own)
  return s:IsCondHead(head) ? head : own
endfunction

function! s:OpIndent(prev, prec, open) abort
  " Returns the indent of an operator continuation at precedence a:prec
  " whose code line above is a:prev, inside the bracket a:open. Lines of one
  " precedence line up, a tighter operator nests one level under a looser
  " one, and the first break goes one level under the expression's first
  " line, or two under an `if`, `while` or `for` line. An expression that
  " starts after a bracket in mid-line, `&& (a`, starts on the bracket's
  " line, so its operators go one level under that line, whatever comes
  " before the bracket.
  let sw = shiftwidth()
  let k = s:LogicalStart(a:prev)
  let n = 0
  while n < s:maxback
    if k <= a:open[0]
      return indent(k) + sw
    endif
    let op = s:OpOf(k)
    if op ==# ''
      break
    endif
    if s:Prec(op) == a:prec
      return indent(k)
    elseif s:Prec(op) < a:prec
      return indent(k) + sw
    endif
    let p = s:PrevCode(k)
    if p == 0
      break
    endif
    let k = s:LogicalStart(p)
    let n += 1
  endwhile
  return indent(k) + (s:IsCondHead(k) ? 2 : 1) * sw
endfunction

function! s:CodeIndent(lnum) abort
  " Returns the indent of a line of code.
  let sw = shiftwidth()
  let code = s:CodePart(a:lnum)

  " A line that starts with a closer goes back to its opener's line.
  if code =~# '^\s*[]})]'
    let o = s:ScanBack(a:lnum, match(code, '\S') + 1)
    if o[0] > 0
      return indent(o[2] ==# '{' ? s:BraceHead(o) : o[0])
    endif
  endif

  let prev = s:PrevCode(a:lnum)
  if prev == 0
    return 0
  endif
  let pcode = s:CodePart(prev)

  " The innermost open bracket sets the base: one level in from its line.
  " A line above at column 0 that leaves nothing open is a complete
  " top-level line, so skip the scan, which would otherwise walk the whole
  " file above every declaration. A multi-line string's closing line is the
  " exception: its indent is the string's, not the code's.
  let open = [0, 0, '']
  if indent(prev) > 0 || s:LeavesOpen(pcode) || getline(prev) =~# '^\s*"""'
    let open = s:ScanBack(a:lnum, 1)
  endif
  if open[0] == 0
    let base = 0
  elseif open[2] ==# '{'
    let base = indent(s:BraceHead(open)) + sw
  else
    let base = indent(open[0]) + sw
  endif

  " `= A` and `| B` lines of a sum type, at top level, indent one level.
  " Inside brackets a `| B` continues a bitwise or if the chain's first line
  " is a body or holds one, and is otherwise an or-pattern, which lines up
  " with its first pattern.
  if code =~# '^\s*|\%([^|>]\|$\)'
    if open[0] == 0
      return sw
    endif
    return s:BitwiseOr(prev) ? s:OpIndent(prev, s:Prec('|'), open) : base
  endif
  if code =~# '^\s*=\%([^=]\|$\)' && open[0] == 0
    return sw
  endif

  " A body after `=` or `->` at the end of a line indents one level.
  let trailing = s:TrailingOp(pcode)
  if trailing ==# '=' || trailing ==# '->'
    return indent(s:LogicalStart(prev)) + sw
  endif

  let op = s:LeadingOp(code)
  if op ==# ''
    let op = trailing
  endif
  if op !=# ''
    return s:OpIndent(prev, s:Prec(op), open)
  endif

  " The conditions of an `if let` or `while let` list continue two levels
  " in. A comma at the end of a line inside parentheses or brackets only
  " separates items, which sit at the base.
  if pcode =~# ',\s*$' && open[2] !~# '[([]'
    let head = s:StatementHead(s:LogicalStart(prev))
    if s:IsCondHead(head)
      return indent(head) + 2 * sw
    endif
  endif

  return base
endfunction

function! GetTurkeyIndent() abort
  let lnum = v:lnum

  let &l:preserveindent = get(b:, "turkey_preserveindent", 0)

  " Keep a block comment's interior as written.
  if s:InBlockComment(lnum)
    return -1
  endif

  " Keep a multi-line string's lines as written: their indentation is part of
  " the string. So is its closing line's, which sets the string's. Vim
  " rewrites a kept indent as tabs, which would turn four spaces of the string
  " into a tab, unless 'preserveindent' is on. So switch it on for these lines
  " only. It stays on until the next line is indented, since nothing runs
  " after Vim sets this line's indent to switch it back.
  if s:InMultiString(lnum)
    let &l:preserveindent = 1
    return -1
  endif

  " An own-line comment takes the indent of the code after it, or one level
  " more when that code closes a bracket, so that a comment at the end of a
  " block sits with the block's contents. With no code after it, it is
  " indented as code would be.
  if getline(lnum) =~# '^\s*\%(--\|{-\)'
    let next = s:NextCode(lnum)
    if next > 0
      let i = s:CodeIndent(next)
      return s:CodePart(next) =~# '^\s*[]})]' ? i + shiftwidth() : i
    endif
  endif

  return s:CodeIndent(lnum)
endfunction

let &cpo = s:save_cpo
unlet s:save_cpo
