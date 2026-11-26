# A tour of Turkey

What Turkey is like to learn, one idea at a time, in order. Each lesson is a
page with a program that runs in the browser beside it, published at
<https://p7g.github.io/turkey/tour/>. The lessons are the files `NN-slug.md`
in this directory, in the order of their numbers.

The tour shows; [the reference](../ref/README.md) is what the language is.
A lesson says what a feature is for and shows it working, and ends with
links to the sections of the reference that state its rules.

## Writing a lesson

**One idea, and short.** A lesson is read on a phone beside its program, so
a few paragraphs, one program and at most two or three more examples. It
starts with `# Title`, and ends with `## In the reference`, a list of links
into `docs/ref/` by heading, or, for a lesson about the library rather than
the language, `## In the library`, a list of links to its modules' pages.

**The first `run` example is the program.** It goes in the page's editor,
and the `text` fence after it is shown as its output until the reader runs
it. Every other example is shown highlighted, with its recorded output or
the error it quotes, and the ones that compile alone can be loaded into the
editor. Write the prose so that it reads with the program beside it, not
inline.

**Examples are checked as the reference's are.** Every `kotlin` fence has a
directive, as [`docs/ref/README.md`](../ref/README.md#for-maintainers) lists,
and `sh scripts/test.sh docs/tutorial` runs them. The `run` and `panic`
examples also run with `--target wasm32-wasi`, since that is what the
browser runs. `<!-- skip: wasm32-wasi: REASON -->`, on the line before an
example's directive, skips it there, and the page shows that program with
its recorded output and the reason, with Run turned off. The `module`
files before the program go in the editor with it, a tab each, after the
program's own tab, `main.gob`.

**The programs are what `turkey format` writes.** The page's Format button
would otherwise change a lesson's program, and the site's build refuses a
lesson whose examples it would change.

**Idiomatic examples**, by the rules in the repository's `CLAUDE.md`: `let`
or a parameter pattern rather than a one-armed `match`, `if let` rather than
a `match` whose other arm does nothing, `for ... in` rather than an index
loop, `[]` rather than `Array.new`, and annotations only where they explain
an interface.

**Links** go to a heading in `docs/ref/` (`../ref/types.md#records`), to
another lesson (`06-records.md`), or to a library module's page
(`https://p7g.github.io/turkey/System.Task.html`). The site's build checks
each one, by GitHub's rule for a heading's anchor, and fails on a link to a
heading or module that does not exist. There are no tables, which the
renderer does not draw.

## Building the pages

`src/TourSite.gob` writes the site from these files, beside the playground,
whose page scripts the lessons share:

```sh
build/stages/turkey build -o build/toursite src/TourSite.gob
build/toursite . build/site/tour
```
