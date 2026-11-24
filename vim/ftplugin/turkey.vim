" Vim filetype plugin for Turkey
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g

if exists("b:did_ftplugin")
  finish
endif
let b:did_ftplugin = 1

" Comments: `--` line comments, `---` doc comments, `{- -}` block comments.
" `---` comes first so that a doc comment continues as one.
setlocal commentstring=--\ %s
setlocal comments=:---,:--,s1:{-,mb:-,ex:-}
setlocal formatoptions-=t formatoptions+=croql

" The layout `turkey format` writes: a tab per level, and lines that fit in 100
" columns counting a tab as 4. 'textwidth' wraps comments only ('formatoptions'
" has no `t`), since the formatter breaks code. Set
" `g:turkey_recommended_style = 0` to keep your own settings.
if get(g:, "turkey_recommended_style", 1)
  setlocal noexpandtab tabstop=4 shiftwidth=0 softtabstop=0 textwidth=100
endif

" Reindenting keeps a line's tabs and spaces where its indent does not change.
" A multi-line string's lines keep their indent, which is part of the string,
" and without this Vim would rewrite four spaces of it as a tab.
setlocal preserveindent

" Identifiers may end in primes (`x'`).
setlocal iskeyword+='

" `gf` on `import Turkey.Ssa` looks for Turkey/Ssa.gob; set 'path' to the
" module search roots for your project.
setlocal include=^\\s*import\\>
setlocal includeexpr=substitute(v:fname,'\\.','/','g')
setlocal suffixesadd=.gob

if exists("loaded_matchit")
  let b:match_words = '\<if\>:\<else\>'
endif

" `:TurkeyFormat` formats the buffer with `turkey format`; see autoload/turkey.vim.
command! -buffer -bar TurkeyFormat call turkey#Format()

augroup turkey_format_on_save
  autocmd! * <buffer>
  autocmd BufWritePre <buffer> call turkey#FormatOnSave()
augroup END

let b:undo_ftplugin = "setlocal commentstring< comments< formatoptions<"
      \ . " preserveindent< iskeyword< include< includeexpr< suffixesadd<"
      \ . " expandtab< tabstop< shiftwidth< softtabstop< textwidth<"
      \ . " | unlet! b:match_words"
      \ . " | silent! delcommand -buffer TurkeyFormat"
      \ . " | execute 'autocmd! turkey_format_on_save * <buffer>'"
