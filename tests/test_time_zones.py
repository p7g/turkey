"""`Time.Zone`'s look-ups that depend on the environment: zone files found
through `$TZDIR`, and the system's zone from each form `$TZ` takes.

The zone files are the fixtures `tests/programs/time_zones` reads directly, so
nothing here depends on the system's copy of tzdata."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.lang import run

ZONEINFO = Path(__file__).resolve().parent / "programs" / "time_zones" / "zoneinfo"

LOOKUPS = """\
import Time

fun say(r) = match r {
    Right(z) -> show(z)
    Left(e) -> message(e)
}

fun main() {
    print(say(Zone.named("America/New_York")))
    print(say(Zone.named("America/New_York")))
    print(say(Zone.named("Nowhere/Zone")))
    print(say(Zone.named("Bad/Zone")))
    print(DateTime.parse("2026-07-04T12:00:00-04:00[America/New_York]"))
    print(DateTime.parse("2026-07-04T12:00:00-05:00[America/New_York]"))
    print(DateTime.parse("2026-07-04T12:00:00-04:00[Nowhere/Zone]"))
}
"""

SYSTEM = """\
import Time

fun main() {
    match Zone.system() {
        Right(z) -> {
            print(z)
            let p = PlainDateTime.new(Option.expect(Date.new(2026, Jul, 4), "date"),
                                      Option.expect(TimeOfDay.new(12, 0, 0), "time"))
            print(DateTime.offset(DateTime.of(p, z)))
        }
        Left(e) -> print(message(e))
    }
}
"""


def output(src: str, env: dict[str, str]) -> list[str]:
    result = run(src, env=env)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_named_zones_come_from_tzdir(tmp_path):
    zones = tmp_path / "zones"
    (zones / "America").mkdir(parents=True)
    (zones / "America" / "New_York").write_bytes(
        (ZONEINFO / "America" / "New_York").read_bytes())
    (zones / "Bad").mkdir()
    (zones / "Bad" / "Zone").write_bytes(b"TZif2 but not really")
    assert output(LOOKUPS, {"TZDIR": str(zones)}) == [
        "America/New_York",
        "America/New_York",
        "no time zone named 'Nowhere/Zone'",
        f"{zones}/Bad/Zone is not a valid time zone",
        "Some(2026-07-04T12:00:00-04:00[America/New_York])",
        "None",
        "None",
    ]


@pytest.mark.parametrize("tz, expected", [
    ("America/Sao_Paulo", ["America/Sao_Paulo", "-03:00"]),
    (":Australia/Lord_Howe", ["Australia/Lord_Howe", "+10:30"]),
    ("EST5EDT,M3.2.0,M11.1.0", ["EST5EDT,M3.2.0,M11.1.0", "-04:00"]),
    (str(ZONEINFO / "Pacific" / "Apia"), [str(ZONEINFO / "Pacific" / "Apia"), "+13:00"]),
    ("", ["the system's time zone is not set"]),
    ("Nowhere", ["no time zone named 'Nowhere'"]),
])
def test_the_system_zone_follows_tz(tz, expected):
    assert output(SYSTEM, {"TZDIR": str(ZONEINFO), "TZ": tz}) == expected
