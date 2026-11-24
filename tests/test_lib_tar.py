"""`scripts/lib-tar.sh`: the standard library as one tar for the playground.

The archive holds exactly the `.gob` files under `lib/`, byte for byte, as
`lib/...`, and two runs on one tree write the same bytes. The gzip layer differs
between hosts, so nothing here compares a compressed archive with a fixed one.
"""

import gzip
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "lib-tar.sh"
LIB = REPO_ROOT / "lib"


def _write(out: Path, cwd: Path = REPO_ROOT, argument: str | None = None) -> bytes:
    subprocess.run(["sh", str(SCRIPT), argument or str(out)], cwd=cwd, check=True,
                   capture_output=True, timeout=300)
    return out.read_bytes()


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return _write(tmp_path_factory.mktemp("lib-tar") / "lib.tar.gz")


def _expected() -> dict[str, bytes]:
    return {path.relative_to(REPO_ROOT).as_posix(): path.read_bytes()
            for path in LIB.rglob("*.gob")
            if path.is_file() and not path.is_symlink()}


def test_lists_exactly_the_gob_files(archive: bytes) -> None:
    expected = _expected()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == sorted(expected)
        assert all(m.isreg() for m in members)
        extracted = {}
        for m in members:
            f = tar.extractfile(m)
            assert f is not None
            extracted[m.name] = f.read()
        assert extracted == expected


def test_members_have_fixed_metadata(archive: bytes) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for m in tar.getmembers():
            assert (m.mode, m.uid, m.gid, m.uname, m.gname, m.mtime) \
                == (0o644, 0, 0, "", "", 0), m.name


def test_the_format_is_posix_ustar(archive: bytes) -> None:
    assert gzip.decompress(archive)[257:265] == b"ustar\x0000"


def test_gzip_header_has_no_name_or_time(archive: bytes) -> None:
    # Bit 3 of the flags byte says a name follows; bytes 4-7 are the mtime.
    assert archive[:2] == b"\x1f\x8b"
    assert archive[3] & 0x08 == 0
    assert archive[4:8] == bytes(4)


def test_two_runs_write_the_same_bytes(archive: bytes, tmp_path: Path) -> None:
    assert _write(tmp_path / "again.tar.gz") == archive


def test_the_tar_is_what_pythons_ustar_writer_writes(archive: bytes) -> None:
    # An independent writer over the same sorted files: the tar is the same on
    # any host, whatever tar the host has.
    expected = io.BytesIO()
    for name, data in sorted(_expected().items()):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        info.type = tarfile.REGTYPE
        expected.write(info.tobuf(tarfile.USTAR_FORMAT, "utf-8", "strict"))
        expected.write(data)
        expected.write(bytes(-len(data) % 512))
    expected.write(bytes(1024))
    assert gzip.decompress(archive) == expected.getvalue()


def test_the_tar_ends_in_two_zero_blocks(archive: bytes) -> None:
    tar = gzip.decompress(archive)
    assert len(tar) % 512 == 0
    assert tar[-1024:] == bytes(1024)
    assert tar[-1536:-1024] != bytes(512)


def test_a_relative_out_is_relative_to_the_working_directory(
        archive: bytes, tmp_path: Path) -> None:
    written = _write(tmp_path / "sub" / "here.tar.gz", cwd=tmp_path,
                     argument="sub/here.tar.gz")
    assert written == archive


def test_the_hosts_tar_reads_it(archive: bytes, tmp_path: Path) -> None:
    # BSD tar on macOS and GNU tar on Linux both read it, and agree with the
    # listing above.
    path = tmp_path / "lib.tar.gz"
    path.write_bytes(archive)
    listing = subprocess.run(["tar", "-tzf", str(path)], check=True,
                             capture_output=True, text=True).stdout.splitlines()
    assert listing == sorted(_expected())
