"""Link C probes to the actual allocator exports without running Turkey entry."""

import subprocess

import pytest

from tests import bootc, toolchain


@pytest.fixture(scope="module", params=toolchain.BACKENDS)
def allocator_object(request, tmp_path_factory):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    directory = tmp_path_factory.mktemp("allocator-exports-" + request.param)
    source = directory / "entry.gob"
    source.write_text("fun main() {}\n")
    result = subprocess.run(
        toolchain.command(bootc.binary(), request.param, str(source)),
        cwd=bootc.REPO_ROOT, capture_output=True, text=True, check=True)
    # The probe supplies C's main. Keep the generated entry available but never
    # call it: allocator exports must work before literals/globals initialize.
    main = toolchain.c_symbol("main")
    text = result.stdout.replace(f'"{main}"', '"_unused_probe_main"')
    text = text.replace("@main(", "@unused_probe_main(")
    generated = directory / ("exports.s" if request.param == "native" else "exports.ll")
    generated.write_text(text)
    output = directory / "exports.o"
    subprocess.run([*toolchain.cc(), "-O1",
                    *toolchain.clang_only("-Wno-override-module"), "-c",
                    str(generated), "-o", str(output)], check=True,
                   capture_output=True, text=True)
    return output
