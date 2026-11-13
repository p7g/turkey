" Vim syntax file for Turkey
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g
" Filenames:    *.gob
"
" The keyword list mirrors `keywords` in src/Turkey/Lexer.gob.

if exists("b:current_syntax")
  finish
endif

syn case match

" Identifiers may contain a trailing prime (`x'`); including ' in the
" keyword characters keeps `if` from matching inside the identifier `if'`
" and makes the word boundaries below agree with the lexer.
syn iskeyword @,48-57,_,192-255,'

" Operators. An operator is any run of `!#$%&*+-/<=>@^|~`, declared or not,
" and `:`, `?` and `\` (a placeholder function, `\_ + 1`) are punctuation
" that reads as one. A bare `.` (field access) is left alone; `..` and
" `...` get their own match. A run stops before `--`, which begins a
" comment, so `x +-- note` is `x +` and a comment.
syn match turkeyOperator "\(-\(-\)\@!\|[!#$%&*+/<=>@^|~]\)\+"
syn match turkeyOperator "\.\.\.\="
syn match turkeyOperator "[:?\\]"

" Comments: `--` to end of line, and `{- -}` block comments, which nest.
syn keyword turkeyTodo contained TODO FIXME XXX NOTE
syn match turkeyLineComment "--.*$" contains=turkeyTodo
" A doc comment: exactly `---`, then a space or the end of the line. Defined
" after the line comment so that it wins where both match.
syn match turkeyDocComment "---\%( .*\)\=$" contains=turkeyTodo
syn region turkeyBlockComment start="{-" end="-}" contains=turkeyBlockComment,turkeyTodo

" Strings and characters. No interpolation, and a string
" cannot span lines. Escapes are exactly \n \t \r \0 \\ \" \' and
" \u{H...H}; anything else after a backslash is an error.
syn match turkeyEscapeError contained "\\."
syn match turkeyEscape contained "\\\(u{\x\{1,6}}\|[ntr0\\"']\)"
syn region turkeyString start=+"+ skip=+\\"+ end=+"+ oneline contains=turkeyEscape,turkeyEscapeError
syn region turkeyChar start=+'+ skip=+\\'+ end=+'+ oneline contains=turkeyEscape,turkeyEscapeError

" Numbers: decimal, `0x`, `0o` and `0b` integers, and floats with a
" fraction, an exponent or both (`1.5`, `1e3`, `2.5E-2`). Any run of digits
" may be split by single `_`s (`1_000`, `0xFFFF_FFFF`). A digit run right
" after a `.` is a projection selector (`x.0.1`), not a number, and `1..10`
" is INT `..` INT, so the float match must not start after a dot. A numeral
" glued to a letter (`12abc`) is a lex error and is left unhighlighted.
syn match turkeyNumber "\<\(0x\x\+\(_\x\+\)*\|0o\o\+\(_\o\+\)*\|0b[01]\+\(_[01]\+\)*\|\d\+\(_\d\+\)*\)\>"
syn match turkeyFloat "\.\@<!\<\d\+\(_\d\+\)*\(\.\d\+\(_\d\+\)*\([eE][-+]\=\d\+\(_\d\+\)*\)\=\|[eE][-+]\=\d\+\(_\d\+\)*\)\>"

" Keywords. `module` is reserved, though it begins no declaration.
syn keyword turkeyConditional if else match
syn keyword turkeyRepeat while for in loop
syn keyword turkeyStatement return break continue
syn keyword turkeyKeyword fun let var do foreign
syn keyword turkeyStructure type class instance
syn keyword turkeyModule module import export as

" Naming convention: uppercase-leading identifiers are type or value
" constructors, lowercase are variables. `True` and `False`
" are the Bool constructors, so they read as literals.
syn keyword turkeyBoolean True False
syn match turkeyConstructor "\<[A-Z][A-Za-z0-9_']*"
" Lowercase identifiers get a group so the char region above cannot start
" at the prime in `x'`; deliberately unhighlighted by default.
syn match turkeyIdentifier "\<[a-z_][A-Za-z0-9_']*"
" `infix` is an ordinary name except where it begins an operator
" declaration: at the start of a top-level item, before a precedence group.
syn match turkeyKeyword "^infix\ze\s\+[A-Z]"

" Optional folding over brace blocks: `:let g:turkey_fold = 1` and use
" foldmethod=syntax.
if get(g:, "turkey_fold", 0)
  syn region turkeyFold start="{" end="}" transparent fold
endif

syn sync fromstart

hi def link turkeyConditional Conditional
hi def link turkeyRepeat Repeat
hi def link turkeyStatement Statement
hi def link turkeyKeyword Keyword
hi def link turkeyStructure Structure
hi def link turkeyModule Include
hi def link turkeyBoolean Boolean
hi def link turkeyConstructor Type
hi def link turkeyOperator Operator
hi def link turkeyNumber Number
hi def link turkeyFloat Float
hi def link turkeyString String
hi def link turkeyChar Character
hi def link turkeyEscape SpecialChar
hi def link turkeyEscapeError Error
hi def link turkeyLineComment Comment
hi def link turkeyDocComment SpecialComment
hi def link turkeyBlockComment Comment
hi def link turkeyTodo Todo

let b:current_syntax = "turkey"
