"""`playground/host.js`: the wasm compiler and what it writes, run under node.

The host is the layer the playground's page calls. It runs `turkey.wasm` over
browser_wasi_shim with `lib/` unpacked from `lib.tar.gz`, and runs the module
the compiler writes. `tests/playground_host.mjs` drives it under node; the
same module runs in a browser's Worker. These tests need node
(`toolchain.needs_node`), and skip without it unless `$TURKEY_REQUIRE_NODE`
says they must not.

`turkey.wasm` is stage2's build of the compiler for wasm32-wasi, the one the
bootstrap shard's cross-target test also writes, cached on disk.
"""

import hashlib
import json
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


def _host(inputs: tuple[Path, Path], tmp_path: Path, source: str, stdin: str | None = None) -> dict:
    node = toolchain.needs_node()
    compiler, archive = inputs
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    argv = [node, f"--stack-size={STACK_KIB}", str(DRIVER), str(compiler), str(archive), str(entry)]
    if stdin is not None:
        (tmp_path / "stdin").write_text(stdin)
        argv.append(str(tmp_path / "stdin"))
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)


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


def test_a_type_error_comes_back_as_diagnostics(inputs, tmp_path):
    result = _host(inputs, tmp_path, 'fun main() {\n\tprint(1 + "a")\n}\n')
    assert result["compiled"]["status"] == 1
    assert result["compiled"]["diagnostics"] == (
        "main.gob:2:8: type error: a numeric literal cannot have type 'String';"
        " it must be one of 'Int', 'Float'\n")
    assert result["ran"] is None


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
