" Vim filetype plugin for Turkey
" Language:     Turkey (https://github.com/p7g/turkey)
" Maintainer:   p7g

if exists("b:did_ftplugin")
  finish
endif
let b:did_ftplugin = 1

" Comments: `--` line comments, `{- -}` block comments.
setlocal commentstring=--\ %s
setlocal comments=:--,s1:{-,mb:-,ex:-}
setlocal formatoptions+=ro

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

let b:undo_ftplugin = "setlocal commentstring< comments< formatoptions<"
      \ . " iskeyword< include< includeexpr< suffixesadd<"
      \ . " | unlet! b:match_words"
