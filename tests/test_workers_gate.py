"""Helper processes take turns."""

import subprocess
import sys

from ctgedit.infra import workers


def test_a_check_waits_while_another_kind_of_helper_works():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        workers.track("pages", p)
        assert workers.others_busy("ctf") and workers.others_busy("display")
        assert not workers.others_busy("pages")
    finally:
        p.kill()
        p.wait()
    assert not workers.others_busy("ctf")
    workers.set_busy("genie", True)
    try:
        assert workers.others_busy("ctf") and not workers.others_busy("genie")
    finally:
        workers.set_busy("genie", False)
    assert not workers.others_busy("display")


def test_a_ctf_check_is_not_started_while_a_genie_job_runs(tmp_path):
    from ctgedit.editing_base import CtfPrecheck

    chk = CtfPrecheck({"ctg": "a", "ctf": "b"}, str(tmp_path / "x.ctg"))
    workers.set_busy("genie", True)
    try:
        assert chk.start() is False and not chk.started()
    finally:
        workers.set_busy("genie", False)


def test_free_memory_is_read():
    mb = workers.free_memory_mb()
    assert mb is None or mb > 0
