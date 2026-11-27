"""`src/TourSite.gob`: the tour's lessons, `docs/tutorial/`, as static pages.

`tests/tour/` is a small repository of its own, with three lessons, a
reference chapter and a library module: one lesson whose program runs in the
browser, one whose program has a module file and runs there too, and one
that a `skip` directive keeps out of the browser. The tests that need a
broken lesson copy it and break the copy. The real tour is built too, so that a lesson whose
link or layout the site's build would refuse fails here, before the deploy.
"""

import html
import json
import re
import shutil
from pathlib import Path

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "TourSite.gob"
FIXTURE = REPO_ROOT / "tests" / "tour"


def _build(root: Path, out: Path) -> lang.Result:
    return lang.run(DRIVER, args=(str(root), str(out)), cwd=REPO_ROOT)


def _built(root: Path, out: Path) -> Path:
    result = _build(root, out)
    assert result.code == 0, result.stderr
    return out


def _broken(tmp_path: Path, lesson: str, old: str, new: str) -> lang.Result:
    """The fixture with `old` replaced by `new` in `lesson`, built."""
    root = tmp_path / "root"
    shutil.copytree(FIXTURE, root)
    path = root / "docs" / "tutorial" / lesson
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
    result = _build(root, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    return result


def test_a_lesson_page_holds_its_program_examples_and_links(tmp_path):
    page = (_built(FIXTURE, tmp_path / "out") / "01-first.html").read_text()
    # The program is in the editor, escaped, and its output is the recorded one.
    assert ('wrap="off">\nfun main() {\n\tprint(&quot;&lt;first&gt; &amp; '
            '\\&quot;last\\&quot;&quot;)\n}\n</textarea>') in page
    assert '<pre class="stdout">&lt;first&gt; &amp; &quot;last&quot;\n</pre>' in page
    # The other examples are highlighted where they stand, with what the
    # directive says of each, and the error and the panic can be tried.
    assert '<p class="verdict">The compiler rejects it: <code>cannot assign</code></p>' in page
    assert '<p class="verdict">It panics: <code>unwrapped None</code></p>' in page
    assert page.count('<button class="try"') == 2
    assert '<span class="keyword">fun</span> double(n)' in page
    assert '<pre class="plain"><code>plain text\n</code></pre>' in page
    # No directive is left, and the title's code span is rendered.
    assert "<!--" not in page
    assert "<h1>First <code>steps</code></h1>" in page
    assert "<title>First steps - A tour of Turkey</title>" in page
    links = re.findall(r'<a href="([^"]*)"', page.split("<main", 1)[1])
    assert links == [
        "https://github.com/p7g/turkey/blob/main/docs/ref/chapter.md#unwrapping-with-",
        "https://github.com/p7g/turkey/blob/main/docs/ref/chapter.md#notes-1",
        "https://github.com/p7g/turkey/blob/main/docs/ref/chapter.md",
        "02-second.html",
        "../Data.Thing.html",
        "https://example.com/a?b=1&amp;c=2",
        "https://github.com/p7g/turkey/blob/main/docs/ref/chapter.md#notes",
    ]


def test_the_program_stands_where_the_lesson_has_it(tmp_path):
    """The prose above the program's fence, the workspace, then the prose
    below it, so a narrow screen shows them in the lesson's own order."""
    page = (_built(FIXTURE, tmp_path / "out") / "01-first.html").read_text()
    above = page.index('<div class="prose above">')
    workspace = page.index('<section class="workspace"')
    below = page.index('<div class="prose below">')
    assert above < page.index("another lesson") < workspace < below
    assert below < page.index("The compiler rejects it") < page.index('<nav class="pager"')


def test_a_lesson_is_named_without_its_number(tmp_path):
    """The page's script keeps the reader's edits under `data-lesson`, which
    renumbering the lessons must not change."""
    out = _built(FIXTURE, tmp_path / "out")
    assert '<main class="lesson" data-lesson="first">' in (out / "01-first.html").read_text()
    root = tmp_path / "root"
    shutil.copytree(FIXTURE, root)
    tutorial = root / "docs" / "tutorial"
    shutil.copy(tutorial / "03-third.md", tutorial / "04-third.md")
    result = _build(root, tmp_path / "twice")
    assert result.code == 1
    assert ("docs/tutorial/04-third.md: the lesson '03-third' has the same name after its "
            "number, 'third'") in result.stderr


def test_a_program_of_several_files_gets_an_editor_for_each(tmp_path):
    """The entry, `main.gob`, comes first, then each module file, each in an
    editor named by `data-file`; the module's block is not shown again in
    the prose."""
    out = _built(FIXTURE, tmp_path / "out")
    second = (out / "02-second.html").read_text()
    assert 'data-static=""' not in second and 'id="run"' in second
    editors = re.findall(r'<div class="editor" data-file="([^"]*)">.*?wrap="off">\n(.*?)</textarea>',
                         second, re.S)
    assert [(name, html.unescape(text)) for name, text in editors] == [
        ("main.gob", 'import Greeting (greeting)\n\nfun main() {\n\tprint(greeting())\n}\n'),
        ("Greeting.gob", 'export (greeting)\n\nfun greeting() -> String = "hi"\n'),
    ]
    assert second.count('id="source"') == 1 and second.count('id="highlighted"') == 1
    assert '<div class="label">Greeting.gob</div>' not in second
    assert '<pre class="stdout">hi\n</pre>' in second


def test_a_module_file_the_page_would_refuse_fails_the_build(tmp_path):
    """The page takes a module file's name to be its module path, as the
    playground's tabs and share links do."""
    result = _broken(tmp_path, "02-second.md", "module: Greeting.gob", "module: greeting.gob")
    assert result.code == 1
    assert ("docs/tutorial/02-second.md: the program's module file 'greeting.gob' is not named "
            "as the page requires") in result.stderr


def test_a_program_the_browser_cannot_run_is_shown_with_the_reason(tmp_path):
    out = _built(FIXTURE, tmp_path / "out")
    third = (out / "03-third.html").read_text()
    assert 'data-static=""' in third and 'id="source"' not in third
    assert "does not run in the browser: it needs a second thread." in third
    assert 'id="run"' not in third


def test_the_contents_and_the_playgrounds_picker_list_the_lessons(tmp_path):
    out = _built(FIXTURE, tmp_path / "out")
    contents = (out / "index.html").read_text()
    assert re.findall(r'<li><a href="([^"]*)">', contents.split("<main", 1)[1]) == [
        "01-first.html", "02-second.html", "03-third.html"]
    assert not (out / "README.html").exists()
    # Only a program the browser runs goes in the picker, with its modules.
    assert json.loads((out / "examples.json").read_text()) == [
        {"name": "01-first", "title": "First steps", "file": "programs/01-first.gob"},
        {"name": "02-second", "title": "Second", "file": "programs/02-second.gob",
         "modules": [{"name": "Greeting.gob", "file": "programs/02-second/Greeting.gob"}]}]
    assert (out / "programs" / "01-first.gob").read_text() == (
        'fun main() {\n\tprint("<first> & \\"last\\"")\n}\n')
    assert (out / "programs" / "02-second" / "Greeting.gob").read_text() == (
        'export (greeting)\n\nfun greeting() -> String = "hi"\n')
    assert sorted(str(p.relative_to(out / "programs")) for p in (out / "programs").rglob("*")) == [
        "01-first.gob", "02-second", "02-second.gob", "02-second/Greeting.gob"]


def test_a_link_to_a_missing_heading_fails_the_build(tmp_path):
    result = _broken(tmp_path, "01-first.md", "chapter.md#notes-1", "chapter.md#notes-2")
    assert result.code == 1
    assert result.stderr == (
        "TourSite: docs/tutorial/01-first.md: the link to '../ref/chapter.md#notes-2' leads "
        "nowhere: docs/ref/chapter.md has no heading whose anchor is 'notes-2'\n")


def test_a_heading_inside_a_fence_is_not_a_heading(tmp_path):
    result = _broken(tmp_path, "01-first.md", "chapter.md#notes-1", "chapter.md#not-a-heading")
    assert result.code == 1
    assert "has no heading whose anchor is 'not-a-heading'" in result.stderr


def test_links_to_a_missing_chapter_lesson_or_module_fail_the_build(tmp_path):
    result = _broken(tmp_path, "01-first.md", "(02-second.md)", "(04-fourth.md)")
    assert result.code == 1
    assert "the link to '04-fourth.md' leads nowhere" in result.stderr
    result = _broken(tmp_path / "b", "01-first.md", "Data.Thing.html", "Data.Nothing.html")
    assert "lib/ has no module Data.Nothing" in result.stderr
    result = _broken(tmp_path / "c", "01-first.md", "../ref/chapter.md)", "../ref/other.md)")
    assert "there is no docs/ref/other.md" in result.stderr


def test_an_example_turkey_format_would_change_fails_the_build(tmp_path):
    result = _broken(tmp_path, "01-first.md", "fun double(n) = n * 2", "fun double(n) = n*2")
    assert result.code == 1
    assert result.stderr == (
        "TourSite: docs/tutorial/01-first.md:40: the example is not as 'turkey format' writes it\n")


def test_a_lesson_without_a_program_fails_the_build(tmp_path):
    result = _broken(tmp_path, "03-third.md", "<!-- run -->", "<!-- check -->")
    assert result.code == 1
    assert "docs/tutorial/03-third.md: a lesson needs a 'run' example, its program" in result.stderr


def test_the_tour_builds(tmp_path):
    """Every lesson of `docs/tutorial/` makes a page, and its program is the
    lesson's first `run` example, which the harness runs."""
    out = _built(REPO_ROOT, tmp_path / "out")
    lessons = sorted(p.stem for p in (REPO_ROOT / "docs" / "tutorial").glob("[0-9]*.md"))
    assert sorted(p.stem for p in out.glob("[0-9]*.html")) == lessons
    picker = json.loads((out / "examples.json").read_text())
    assert picker[0]["name"] == lessons[0] == "01-hello"
    for entry in picker:
        lesson = (REPO_ROOT / "docs" / "tutorial" / f"{entry['name']}.md").read_text()
        found = re.search(r"<!-- run -->\n```kotlin\n(.*?)```\n", lesson, re.S)
        assert found is not None, f"{entry['name']} has no '<!-- run -->' fence"
        program = found.group(1)
        assert (out / entry["file"]).read_text() == program
        page = (out / f"{entry['name']}.html").read_text()
        assert html.unescape(page.split('wrap="off">\n', 1)[1].split("</textarea>")[0]) == program
        for module in entry.get("modules", []):
            fence = f"<!-- module: {module['name']} -->\n```kotlin\n"
            assert fence in lesson
            assert (out / module["file"]).read_text() == lesson.split(fence, 1)[1].split("```\n")[0]
    # The Modules lesson, whose program has a module file, runs in the browser.
    assert "27-modules" in [entry["name"] for entry in picker]
