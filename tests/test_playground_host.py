"""`playground/host.js`: the wasm compiler and what it writes, run under node.

The host is the layer the playground's page calls. It runs `turkey.wasm` over
browser_wasi_shim with `lib/` unpacked from `lib.tar.gz`, and runs the module
the compiler writes, and checks, highlights and formats source for the
editor. The page's pure modules, the share links and the editor's overlay
patch, are checked by `tests/playground_share.test.mjs`, the marks made
from `check`'s diagnostics by `tests/playground_diagnostics.test.mjs`, and
the Types view and the keyboard shortcuts by
`tests/playground_types.test.mjs`.
`tests/playground_host.mjs` drives the host under node; the
same module runs in a browser's Worker. These tests need node
(`toolchain.needs_node`), and skip without it unless `$TURKEY_REQUIRE_NODE`
says they must not.

`turkey.wasm` is stage2's build of the compiler for wasm32-wasi, the one the
bootstrap shard's cross-target test also writes, cached on disk.
"""

import hashlib
import html
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "tests" / "playground_host.mjs"
VENDOR = REPO_ROOT / "playground" / "vendor" / "browser_wasi_shim"

# V8's engine stack for node, in KiB. V8 first runs each function in Liftoff,
# its baseline tier, which gives every wasm local a stack slot, and what a
# compile needs depends on when TurboFan replaces the hot functions. Compiled
# by Liftoff alone, hello world needs about 100 KiB and a 211-line program
# about 140 KiB, against a default of 864 KiB on arm64 macOS. 4 MiB leaves a
# deeper compile room, and fits under the 8 MiB main-thread stack of both CI
# and macOS.
STACK_KIB = 4096


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """`turkey.wasm` and `lib.tar.gz`, as the page fetches them."""
    # Skip before building either, since building the compiler takes minutes.
    toolchain.needs_node()
    archive = tmp_path_factory.mktemp("playground") / "lib.tar.gz"
    subprocess.run(["sh", str(REPO_ROOT / "scripts" / "lib-tar.sh"), str(archive)],
                   check=True, capture_output=True, timeout=300)
    return bootc.written_compiler("turkey-wasm", "--target", "wasm32-wasi"), archive


def _host(inputs: tuple[Path, Path], tmp_path: Path, source: str | dict[str, str],
          stdin: str | None = None) -> dict:
    """Compiles `source`, one file or several by name with `main.gob` the
    entry, and runs what the compiler writes."""
    node = toolchain.needs_node()
    compiler, archive = inputs
    if isinstance(source, str):
        entry = tmp_path / "main.gob"
        entry.write_text(source)
    else:
        entry = tmp_path / "program"
        for name, text in source.items():
            (entry / name).parent.mkdir(parents=True, exist_ok=True)
            (entry / name).write_text(text)
    argv = [node, f"--stack-size={STACK_KIB}", str(DRIVER), str(compiler), str(archive), str(entry)]
    if stdin is not None:
        (tmp_path / "stdin").write_text(stdin)
        argv.append(str(tmp_path / "stdin"))
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)


def test_diagnostics_filter():
    """The page keeps a refused-functions warning only after a trap, and marks
    the spans `check` reports at the right UTF-16 offsets."""
    node = toolchain.needs_node()
    result = subprocess.run([node, str(REPO_ROOT / "tests" / "playground_diagnostics.test.mjs")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]


def test_share_links_and_the_editor_patch():
    """A share link restores its text exactly and refuses a damaged one, and
    the editor's overlay patch finds the stretch an edit changed."""
    node = toolchain.needs_node()
    result = subprocess.run([node, str(REPO_ROOT / "tests" / "playground_share.test.mjs")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]


def test_the_types_view_and_the_shortcuts():
    """The Types view lists a program's bindings by file and finds each name
    in its text, and the shortcuts ask for Run and Format."""
    node = toolchain.needs_node()
    result = subprocess.run([node, str(REPO_ROOT / "tests" / "playground_types.test.mjs")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]


def _tools(inputs: tuple[Path, Path], tmp_path: Path, source: str) -> dict:
    node = toolchain.needs_node()
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    argv = [node, f"--stack-size={STACK_KIB}", str(DRIVER), "--tools", str(inputs[0]), str(entry)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)


def test_highlighting_escapes_what_the_page_sets_as_html(inputs, tmp_path):
    """The editor sets `highlight`'s output as HTML, so markup in the source
    must come out escaped wherever it stands, and the text must be the
    source exactly."""
    source = ('fun main() {\n\tprint("<img src=x onerror=alert(1)> & \\"q\\"")'
              " -- </span><b>&amp;</b>\n}\n{- <script> -}\n")
    highlighted = _tools(inputs, tmp_path, source)["highlighted"]
    assert highlighted["status"] == 0, highlighted["diagnostics"]
    out = highlighted["html"]
    assert re.findall(r"<(?!span class=\"[a-z-]+\">|/span>)", out) == []
    assert '"' not in re.sub(r'<span class="[a-z-]+">', "", out)
    assert html.unescape(re.sub(r"<[^>]*>", "", out)) == source
    assert '<span class="keyword">fun</span>' in out


def test_format_lays_out_the_source_or_reports_why_not(inputs, tmp_path):
    formatted = _tools(inputs, tmp_path, "fun main() {print(1)}\n")["formatted"]
    assert formatted == {"text": "fun main() { print(1) }\n", "status": 0, "diagnostics": ""}
    broken = _tools(inputs, tmp_path, "fun main( {\n")["formatted"]
    assert broken == {"text": None, "status": 3,
                      "diagnostics": "main.gob:1:11: parse error: expected a pattern, found '{'\n"}


def test_hello_world_compiles_and_runs(inputs, tmp_path):
    result = _host(inputs, tmp_path, 'fun main() {\n\tprint("hello, world")\n}\n')
    assert result["compiled"]["status"] == 0, result["compiled"]["diagnostics"]
    assert result["ran"] == {"status": 0, "stdout": "hello, world\n", "stderr": "",
                             "trap": None, "memory": result["ran"]["memory"]}


def test_a_program_reads_its_standard_input(inputs, tmp_path):
    source = ("import System.IO as IO\n\n"
              "fun main() {\n"
              "\tfor line in IO.lines(IO.stdin()) {\n"
              "\t\tif let Right(text) = line {\n"
              "\t\t\tprint(\"read ${text}\")\n"
              "\t\t}\n"
              "\t}\n"
              "}\n")
    result = _host(inputs, tmp_path, source, stdin="quack\nhonk\n")
    assert result["compiled"]["status"] == 0, result["compiled"]["diagnostics"]
    assert result["ran"]["stdout"] == "read quack\nread honk\n"


def test_a_program_of_several_files_compiles_and_runs(inputs, tmp_path):
    """Each file is written beside the entry, a subdirectory for a dotted
    module name, and the compiler finds them by their imports."""
    result = _host(inputs, tmp_path, {
        "main.gob": ("import Geometry as Geo\nimport Shapes.Circle (area)\n\n"
                     "fun main() {\n\tprint(Geo.double(21))\n\tprint(area(2))\n}\n"),
        "Geometry.gob": "export (double)\n\nfun double(n : Int) -> Int = n * 2\n",
        "Shapes/Circle.gob": "export (area)\n\nfun area(r : Int) -> Int = 3 * r * r\n",
    })
    assert result["compiled"]["status"] == 0, result["compiled"]["diagnostics"]
    assert result["ran"]["stdout"] == "42\n12\n"


def test_an_error_in_a_module_names_its_file(inputs, tmp_path):
    result = _host(inputs, tmp_path, {
        "main.gob": "import Geometry\n\nfun main() {\n\tprint(Geometry.double(21))\n}\n",
        "Geometry.gob": 'export (double)\n\nfun double(n : Int) -> Int = n + "a"\n',
    })
    assert result["compiled"]["status"] == 1
    assert result["compiled"]["diagnostics"].startswith("Geometry.gob:3:")
    assert result["ran"] is None


def test_streamed_writes_concatenate_to_the_output(inputs, tmp_path):
    """`onWrite` sees every write as it happens, so what it is given adds up
    to the `stdout` and `stderr` that `run` returns."""
    source = ("fun main() {\n"
              "\tfor i in Int.range(0, 50) {\n"
              "\t\tprint(\"line ${i} \u00e9\")\n"
              "\t}\n"
              "\tlet xs = [1]\n"
              "\tprint(xs[3])\n"
              "}\n")
    result = _host(inputs, tmp_path, source)
    assert result["compiled"]["status"] == 0, result["compiled"]["diagnostics"]
    ran, streamed = result["ran"], result["streamed"]
    assert ran["stdout"].startswith("line 0 \u00e9\nline 1 \u00e9\n")
    assert ran["stderr"].startswith("panic: array index out of bounds")
    assert streamed["stdout"] == ran["stdout"]
    assert streamed["stderr"] == ran["stderr"]
    assert streamed["calls"] > 2


def test_a_type_error_comes_back_as_diagnostics(inputs, tmp_path):
    result = _host(inputs, tmp_path, 'fun main() {\n\tprint(1 + "a")\n}\n')
    assert result["compiled"]["status"] == 1
    assert result["compiled"]["diagnostics"] == (
        "main.gob:2:8: type error: a numeric literal cannot have type 'String';"
        " it must be one of 'Int', 'Float'\n")
    assert result["ran"] is None


def _driver(inputs: tuple[Path, Path], tmp_path: Path, mode: str,
            source: str | dict[str, str]) -> dict:
    node = toolchain.needs_node()
    if isinstance(source, str):
        entry = tmp_path / "main.gob"
        entry.write_text(source)
    else:
        entry = tmp_path / "program"
        for name, text in source.items():
            (entry / name).parent.mkdir(parents=True, exist_ok=True)
            (entry / name).write_text(text)
    argv = [node, f"--stack-size={STACK_KIB}", str(DRIVER), mode, str(inputs[0]), str(inputs[1]),
            str(entry)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)


# A program with an error that has a note, for `check`.
NOT_VAR = "type R = R { a : Int }\n\nfun main() {\n\tlet r = R { a = 1 }\n\tr.a = 2\n}\n"


def test_check_returns_the_diagnostics_as_objects(inputs, tmp_path):
    checked = _driver(inputs, tmp_path, "--check", NOT_VAR)["checked"]
    assert checked == {
        "diagnostics": [{
            "file": "main.gob", "line": 5, "column": 2, "endLine": 5, "endColumn": 5,
            "severity": "error", "stage": "type error", "code": None,
            "message": "cannot assign to field 'a': it is not 'var' in 'R';"
                       " declare it 'var a' to make it assignable",
            "notes": [{"file": "main.gob", "line": 1, "column": 14, "endLine": 1,
                       "endColumn": 15, "message": "'a' is declared here"}],
        }],
        # A program that does not type-check has no types.
        "types": [],
        "status": 1,
        "text": "",
    }


def test_the_tools_worker_checks_every_file_with_the_library(inputs, tmp_path):
    """The page's tools Worker fetches `lib.tar.gz` and answers a `check`
    message with the files it checked and each file's diagnostics."""
    files = {
        "main.gob": "import Shapes\n\nfun main() {\n\tlet unused = 4\n\tprint(Shapes.area(2))\n}\n",
        "Shapes.gob": "export (area)\n\nfun area(side : Int) -> Int {\n\tmatch side {\n"
                      "\t\ts -> s * s\n\t}\n}\n",
    }
    answer = _driver(inputs, tmp_path, "--worker", files)
    assert answer["replies"] == ["status", "ready", "checked"]
    checked = answer["checked"]
    assert (checked["type"], checked["id"]) == ("checked", 7)
    assert checked["files"] == [["main.gob", files["main.gob"]], ["Shapes.gob", files["Shapes.gob"]]]
    found = [(d["file"], d["code"], d["line"], d["endLine"]) for d in checked["diagnostics"]]
    assert sorted(found) == [
        ("Shapes.gob", "irrefutable-match", 4, 6),
        ("main.gob", "unused-variable", 4, 4),
    ]
    # The same check gives the types of every file's bindings, warnings and
    # all, and the status the Types view reads them by.
    assert checked["status"] == 0
    assert checked["types"] == [
        {"file": "Shapes.gob", "line": 3, "column": 1, "name": "area", "type": "fun(Int) -> Int"},
        {"file": "main.gob", "line": 3, "column": 1, "name": "main", "type": "fun() -> Unit"},
    ]


def test_a_panic_comes_back_with_its_report_and_status(inputs, tmp_path):
    result = _host(inputs, tmp_path, "fun main() {\n\tlet xs = [1, 2, 3]\n\tprint(xs[5])\n}\n")
    assert result["compiled"]["status"] == 0, result["compiled"]["diagnostics"]
    ran = result["ran"]
    assert ran["status"] == 1
    assert ran["trap"] is None
    assert ran["stderr"].startswith("panic: array index out of bounds: read at index 5, length 3\n")
    assert "  at main (main.gob:3:8)\n" in ran["stderr"]


def test_the_vendored_shim_is_what_provenance_records():
    """Every vendored file is listed with its SHA-256, and nothing else is
    there, so an edit to the vendored code shows up here."""
    text = (VENDOR / "PROVENANCE").read_text()
    recorded = {}
    for line in text.splitlines():
        if len(line) > 66 and line[64:66] == "  ":
            recorded[line[66:]] = line[:64]
    present = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in VENDOR.iterdir() if path.name != "PROVENANCE"}
    assert present == recorded
