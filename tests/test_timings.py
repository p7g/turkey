"""`boot build --timings`: each phase's time, the backend's spread over
functions, and the optimizer's dependency structure, on stderr."""

import re

from tests import bootc

PHASES = ["check", "lower", "mono", "opt", "ssa", "select", "backend", "tables"]


def test_timings_name_every_phase_in_order():
    out, err = bootc.boot_with_stderr(*bootc.argv("asm"), "--timings",
                                      "tests/programs/adt.gob")
    phases = re.findall(r"^time\t(\w+)\t\d+\.\dms$", err, re.MULTILINE)
    assert phases == PHASES, err
    assert re.search(r"^functions\t\d+\ttotal \d+\.\dms\tlargest ", err, re.MULTILINE), err
    # The optimizer reduces twice, and reports each.
    assert len(re.findall(r"^opt-schedule\tcomponents \d+\ttotal .*\tcritical path ",
                          err, re.MULTILINE)) == 2, err
    assert ".globl" in out


def test_without_the_flag_nothing_is_reported():
    _, err = bootc.boot_with_stderr(*bootc.argv("asm"), "tests/programs/adt.gob")
    assert "time\t" not in err and "opt-schedule" not in err
