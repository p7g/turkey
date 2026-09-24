"""Link C probes to the actual allocator exports without running Turkey entry."""

import shutil
import subprocess

import pytest

from tests import bootc


@pytest.fixture(scope="module", params=["native", "llvm"])
def allocator_object(request, tmp_path_factory):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    directory = tmp_path_factory.mktemp("allocator-exports-" + request.param)
    source = directory / "entry.gob"
    source.write_text("fun main() {}\n")
    result = subprocess.run([str(bootc.binary()), request.param, str(source)],
                            cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                            check=True)
    # The probe supplies C's main. Keep the generated entry available but never
    # call it: allocator exports must work before literals/globals initialize.
    text = result.stdout.replace('"_main"', '"_unused_probe_main"')
    text = text.replace("@main(", "@unused_probe_main(")
    generated = directory / ("exports.s" if request.param == "native" else "exports.ll")
    generated.write_text(text)
    output = directory / "exports.o"
    subprocess.run(["cc", "-O1", "-Wno-override-module", "-c", str(generated),
                    "-o", str(output)], check=True, capture_output=True, text=True)
    return output
