# A tour of Turkey

The tour teaches Turkey one idea at a time, in order. Each lesson is a page
with a program that runs in the browser beside it, published at
<https://p7g.github.io/turkey/tour/>. The lessons are the files `NN-slug.md`
in this directory, in the order of their numbers.

The tour shows the language working, and [the reference](../ref/README.md)
defines it. A lesson explains what a feature is for, shows it working, and
ends with links to the sections of the reference that state its rules.

## Writing a lesson

Write for a programmer who knows another language and is new to Turkey. A
lesson is a page of a language tour, not a section of the reference. Keep it
short and direct, and address the reader.

### The prose

**Name a thing with the term programmers already use.** Write "a task is a
lightweight thread", not "a task is a function that runs concurrently with
the code that started it". When Turkey has its own name for something, give
the familiar term beside it the first time: "a variant type is a sum type".

**Make the programmer the subject, or write an instruction.** Write "to
start a task, pass a function to `spawn`" and "you can only start tasks
inside `runScope`". Don't write "a task only starts inside `runScope`" or
"`spawn` is sequenced with `?`".

**Use the field's standard vocabulary, and don't invent phrasing.** Write
"access to shared data must be synchronized", not "tasks that change shared
data need to take turns". Write "applies a function to each element in
parallel", not "spreads a function over an array". Write "mutex", not
"guards a value".

**Explain the concept first, then show how to do it.** Say what the concept
is, briefly and in plain terms, before you name the functions or the syntax.
Explain structured concurrency (every task belongs to a scope and can't
outlive it) before you show `runScope` and `spawn`.

**Teach the language, not the library.** A library function can appear in a
program without the prose documenting it. Concurrency is the exception:
tasks are central to the runtime, so `runScope` and `spawn` belong in the
prose.

**Leave out implementation and target details.** How one target schedules
tasks belongs where that target is documented.

**Don't compare Turkey with other languages.** Use common vocabulary, but
don't write "unlike Java" or add a "Coming from Rust" note.

**Write short sentences with one idea each.** Define a term in the sentence
that first uses it. If a lesson needs a term from a later lesson, link to
that lesson.

**Use only what the reader has seen.** A program can use the features of
earlier lessons and the one its own lesson teaches. If it needs something
from a later lesson, change the program or move the lesson. A library
function that the program calls in passing needs no lesson.

**Name the code you mean.** The program is beside the prose on a wide
screen. On a narrow one it stands where the lesson has it, between the prose
above its fence and the prose below. So put the concept before the program
and the notes on the program after it, and name the function, or quote the
line, that a sentence is about.

### The page

**Keep to one idea, and keep it short.** Write a few paragraphs, one program
and at most two or three more examples. Start the lesson with `# Title`. End
it with `## In the reference`, a list of links to headings in `docs/ref/`.
For a lesson about the library, end it with `## In the library` instead, a
list of links to the pages of its modules.

**Name the file `NN-slug.md`.** The number orders the lessons. The slug names
the lesson in the reader's browser, where the page keeps their edits to its
program, so you can renumber a lesson freely, but renaming it drops those
edits. Two lessons can't share a slug.

**Make the first `run` example the program.** The page puts it in the
editor, and shows the `text` fence after it as the output until the reader
runs it. The page shows every other example highlighted, with its recorded
output or the error it quotes. The reader can load the ones that compile
alone into the editor.

**Give every `kotlin` fence a directive**, from the list in
[`docs/ref/README.md`](../ref/README.md#for-maintainers).
`sh scripts/test.sh docs/tutorial` runs the examples as it runs the
reference's. It also runs the `run` and `panic` examples with
`--target wasm32-wasi`, because that is what the browser runs. To skip an
example there, write `<!-- skip: wasm32-wasi: REASON -->` on the line before
its directive. The page then shows the program with its recorded output and
the reason, and turns Run off. The page puts a program's `module` files in
the editor with it, one tab each, after the program's own tab, `main.gob`.

**Format the programs with `turkey format`.** The page's Format button would
otherwise change a lesson's program, and the site's build rejects a lesson
whose examples the formatter would change.

**Write idiomatic examples**, by the rules in the repository's `CLAUDE.md`.
Use `let` or a parameter pattern instead of a one-armed `match`, `if let`
instead of a `match` whose other arm does nothing, `for ... in` instead of
an index loop, and `[]` instead of `Array.new`. Annotate only where the
annotation documents an interface.

**Link to a heading in `docs/ref/`** (`../ref/types.md#records`), to
another lesson (`08-records.md`), or to a library module's page
(`https://p7g.github.io/turkey/System.Task.html`). The site's build checks
every link, by GitHub's rule for a heading's anchor, and fails on a link to
a heading or a module that doesn't exist. Don't use tables, because the
renderer doesn't draw them.

## Building the pages

`src/TourSite.gob` writes the site from these files, beside the playground,
whose page scripts the lessons share:

```sh
build/stages/turkey build -o build/toursite src/TourSite.gob
build/toursite . build/site/tour
```
