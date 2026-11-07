" Reindents $TURKEY_FIXTURE and writes it to $TURKEY_OUT/indent.out.
" The fixture is formatted in the plugin's own style, so a correct
" indentexpr leaves it unchanged; run.sh diffs the two.
execute 'set rtp+=' . fnameescape($TURKEY_VIM_DIR)
filetype plugin indent on
syntax on
set shiftwidth=4 expandtab
execute 'edit! ' . fnameescape($TURKEY_FIXTURE)
if &filetype !=# 'turkey'
  call writefile(['FAIL: filetype is ' . string(&filetype)], $TURKEY_OUT . '/syntax.out')
  cquit!
endif
normal! gg=G
execute 'write! ' . fnameescape($TURKEY_OUT . '/indent.out')
call writefile(['ok: filetype=turkey, indentexpr=' . &indentexpr], $TURKEY_OUT . '/indent.ok')
qa!
