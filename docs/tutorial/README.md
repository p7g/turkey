# A tour of Turkey

The tour teaches Turkey one idea at a time, in order. Each lesson is a page
with a program that runs in the browser beside it, published at
<https://p7g.github.io/turkey/tour/>. The lessons are the files `NN-slug.md`
in this directory, in the order of their numbers.

The tour shows the language working, and [the reference](../ref/README.md)
defines it. A lesson explains what a feature is for, shows it working, and
ends with links to the sections of the reference that state its rules.

## Writing a lesson

**Keep to one idea, and keep it short.** A lesson is read on a phone beside
its program, so write a few paragraphs, one program and at most two or three
more examples. Start it with `# Title`, and end it with
`## In the reference`, a list of links into `docs/ref/` by heading, or, for
a lesson about the library rather than the language, `## In the library`, a
list of links to its modules' pages.

**Make the first `run` example the program.** It goes in the page's editor,
and the `text` fence after it is shown as its output until the reader runs
it. Every other example is shown highlighted, with its recorded output or
the error it quotes, and the ones that compile alone can be loaded into the
editor. Write the prose so that it reads with the program beside it, not
inline.

**Give every `kotlin` fence a directive**, from the list in
[`docs/ref/README.md`](../ref/README.md#for-maintainers).
`sh scripts/test.sh docs/tutorial` runs them as it runs the reference's,
and the `run` and `panic` examples also run with `--target wasm32-wasi`,
since that is what the browser runs. `<!-- skip: wasm32-wasi: REASON -->`,
on the line before an example's directive, skips it there, and the page
shows that program with its recorded output and the reason, with Run turned
off. A program with
`module` files is shown the same way, since the browser compiles one file.

**Write the programs as `turkey format` does.** The page's Format button
would otherwise change a lesson's program, and the site's build refuses a
lesson whose examples it would change.

**Write idiomatic examples**, by the rules in the repository's
`CLAUDE.md`: `let` or a parameter pattern rather than a one-armed `match`,
`if let` rather than a `match` whose other arm does nothing, `for ... in`
rather than an index loop, `[]` rather than `Array.new`, and annotations
only where they explain an interface.

**Link to a heading in `docs/ref/`** (`../ref/types.md#records`), to
another lesson (`06-records.md`), or to a library module's page
(`https://p7g.github.io/turkey/System.Task.html`). The site's build checks
each link, by GitHub's rule for a heading's anchor, and fails on a link to a
heading or module that does not exist. Don't use tables, since the renderer
does not draw them.

## Building the pages

`src/TourSite.gob` writes the site from these files, beside the playground,
whose page scripts the lessons share:

```sh
build/stages/turkey build -o build/toursite src/TourSite.gob
build/toursite . build/site/tour
```
