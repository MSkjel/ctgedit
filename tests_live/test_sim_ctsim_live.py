"""The ctsim client against the real engine, on a copy of Example."""

import datetime
import os
import shutil
import struct
import time

import pytest

from ctgedit.format.dbf import Dbf
from ctgedit.sim import client as C

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")

R2_USER = os.path.join("C:\\", "ProgramData", "AVEVA Plant SCADA 2023 R2", "User")
#: Example and what it includes (include.dbf), plus the implicit Include and System
PROJECTS = ("Example", "SxW_Style_Include", "Library_Equipment", "Include", "System")


def _real_engine():
    tried = []
    for path in [
        os.environ.get(C.PATH_ENV, ""),
        os.path.join(C.checkout_build_dir(), C.EXE_NAME),
    ]:
        if not path:
            continue
        exe = os.path.join(path, C.EXE_NAME) if os.path.isdir(path) else path
        tried.append(exe)
        if os.path.isfile(exe) and os.path.isfile(os.path.join(os.path.dirname(exe), C.DLL_NAME)):
            return C.EngineLocation(exe, "test"), tried
    return None, tried


def write_dbf(path, fields, rows):
    desc = b"".join(
        n.encode("ascii").ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([ln]) + b"\0" * 15 for n, ln in fields
    )
    hlen = 32 + len(desc) + 1
    rlen = 1 + sum(ln for _n, ln in fields)
    d = datetime.date.today()
    head = struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, 0, hlen, rlen)
    with open(path, "wb") as fh:
        fh.write(head + desc + b"\x0d" + b"\x1a")
    with Dbf(path) as t:
        t.append([t.encode(r) for r in rows])


def _wanted(name: str) -> bool:
    low = name.lower()
    if low.startswith("pg") and low.endswith(".dbf"):
        return False  # the page tables: the compiler's input, not needed
    # the compiled library, and one compiled page (the tags a page reads come from its .RDB)
    return low.endswith((".dbf", ".ci")) or low in ("_library.rdb", "mixer.rdb")


def make_install(root):
    """A COPY of R2 Example and its include chain in ``root`` (tables, Cicode and the compiled
    library only, about 20 MB; never the live folders) with its own MASTER.DBF."""
    user = os.path.join(str(root), "inst", "User")
    rows = []
    for name in PROJECTS:
        src = os.path.join(R2_USER, name)
        if not os.path.isdir(src):
            pytest.skip("the R2 project %s is not installed" % name)
        dst = os.path.join(user, name)
        os.makedirs(dst)
        for f in os.listdir(src):
            if _wanted(f) and os.path.isfile(os.path.join(src, f)):
                shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))
        rows.append({"NAME": name, "PATH": dst})
    write_dbf(os.path.join(user, "MASTER.DBF"), [("NAME", 64), ("PATH", 200)], rows)
    return os.path.join(user, "Example")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    loc, tried = _real_engine()
    if loc is None:
        pytest.skip("ctsim.exe is not built (looked in %s)" % ", ".join(tried))
    version = C.engine_version(loc)
    root = tmp_path_factory.mktemp("ctsim_live")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(C.SERVERS_ENV, str(root / "servers"))
        # an engine without the server exits at once: no copy needed to find that out
        probe = C.StartedServer.start(loc, str(root))
        try:
            probe.wait_announced(str(root / "servers"), 5)
        except C.ServerUnavailable as e:
            if "no server yet" in str(e):
                pytest.skip("%s (%s) has no server yet: %s" % (loc.exe, version, e))
        finally:
            probe.stop(timeout=3, grace=0)
        main = make_install(root)
        try:
            s = C.open_session(main, location=loc, start_timeout=120)
        except BaseException:
            shutil.rmtree(str(root), ignore_errors=True)
            raise
        yield s
        s.close()
        running = s.server.running()
    shutil.rmtree(str(root), ignore_errors=True)
    assert not running


def test_hello_says_what_the_server_is(session):
    assert not session.attached and session.server.running()
    assert session.version or session.hello


def test_the_tags_come_from_the_project(session):
    try:
        rows, total = session.all_tags(limit=5000)
    except C.NotAvailable as e:
        pytest.skip("ctsim/tags is not in this engine yet: %s" % e)
    names = {r["name"].lower() for r in rows}
    with Dbf(os.path.join(session.project, "variable.dbf"), writable=False) as t:
        first = t.field(t.read(0), "NAME").strip().lower()
    assert total >= len(rows) > 100
    assert first in names or session.tags(filter=first).total >= 1


def test_a_tag_write_comes_back(session):
    rows, _t = session.all_tags(limit=2000)
    tag = next(r for r in rows if str(r["type"]).upper() in ("INT", "REAL", "LONG"))
    name = tag["name"]
    try:
        session.subscribe([name])
    except C.NotAvailable:
        pass
    seen = []
    session.add_listener(lambda n, b: seen.append((n, b)))
    try:
        session.set_tag(name, 42)
    except C.NotAvailable as e:
        pytest.skip("ctsim/setTag is not in this engine yet: %s" % e)
    end = time.monotonic() + 5
    while time.monotonic() < end and not any(n == "ctsim/tagsChanged" for n, _b in seen):
        time.sleep(0.05)
    got = next(r for r in session.tags(filter=name, count=50).tags if r["name"] == name)
    assert float(got["value"]) == 42.0


def test_call_runs_cicode(session):
    try:
        value = session.call('StrToInt("42")', timeout=60)
    except C.NotAvailable as e:
        pytest.skip("ctsim/call is not in this engine yet: %s" % e)
    assert float(value) == 42.0


def test_the_clock_steps(session):
    try:
        c = session.clock_request("pause")
        c2 = session.clock_request("step")
    except C.NotAvailable as e:
        pytest.skip("ctsim/clock is not in this engine yet: %s" % e)
    assert not c2.running
    assert (c2.scan, c2.time) >= (c.scan, c.time)


def test_page_requests_say_when_they_are_missing(session):
    if session.supports("openPage") is False:
        with pytest.raises(C.NotAvailable):
            session.open_page("NoSuchPage")
    else:
        with pytest.raises(C.RequestFailed):
            session.open_page("NoSuchPage")


def test_the_simulate_windows_source_on_the_real_engine(session, monkeypatch):
    """EngineSimulation (the Simulate window's source) joins the running server: the page's
    tags from its compiled .RDB, every tag searched by the server, a write, the clock, and
    the page on the server's states once the engine animates pages (else the demo, said)."""
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation

    monkeypatch.delenv(C.DISABLE_ENV, raising=False)  # this test wants the engine
    hub = C.Hub(linger=0)
    sim = EngineSimulation(hub=hub, fake=FakeSimulation())
    try:
        sim.start(os.path.join(session.project, "Mixer.ctg"), None)
        end = time.monotonic() + 30
        while time.monotonic() < end and (sim.status().busy or not sim.tags_active()):
            time.sleep(0.05)
        st = sim.status()
        assert st.connected and "already running" in st.text, st.text
        page, n = sim.search_tags("", page_only=True)
        assert n > 10 and all(t.page_reads for t in page)
        sim.search_tags("", page_only=False)
        end = time.monotonic() + 20
        while time.monotonic() < end and sim.search_tags("")[1] is None:
            time.sleep(0.05)
        assert sim.search_tags("")[1] >= n
        tag = next(t for t in page if t.type.upper() in ("INT", "REAL", "LONG"))
        sim.set_tag(tag.name, 7)
        end = time.monotonic() + 5
        while (
            time.monotonic() < end
            and float(next(t for t in sim.search_tags("", True)[0] if t.name == tag.name).value or 0) != 7
        ):
            time.sleep(0.05)
        assert float(next(t for t in sim.search_tags("", True)[0] if t.name == tag.name).value) == 7.0
        sim.clock("pause")
        sim.clock("step")
        end = time.monotonic() + 5
        while time.monotonic() < end and sim.clock_state().running:
            time.sleep(0.05)
        assert not sim.clock_state().running
        if session.supports("openPage") is not False:
            end = time.monotonic() + 30
            while time.monotonic() < end and not sim.page_active():
                time.sleep(0.05)
            assert sim.page_active() and not sim.status().demo
        else:
            assert st.demo and "cannot run pages yet" in sim.status().text
    finally:
        sim.stop()
    assert session.server.running()  # an attached client never stops it


def test_an_uncompiled_project_says_why(tmp_path, monkeypatch):
    """The real engine refuses a project whose _LIBRARY.RDB it cannot read (a project with
    none it compiles from its source); the client's message
    carries the engine's reason (its stderr is the OEM code page: the path's letters
    survive)."""
    loc, tried = _real_engine()
    if loc is None:
        pytest.skip("ctsim.exe is not built (looked in %s)" % ", ".join(tried))
    monkeypatch.setenv(C.SERVERS_ENV, str(tmp_path / "servers"))
    proj = tmp_path / "Prosj\u00e6kt"
    proj.mkdir()
    (proj / "_LIBRARY.RDB").write_bytes(b"not an rdb file at all")
    with pytest.raises(C.ServerUnavailable) as e:
        C.open_session(str(proj), location=loc, start_timeout=60)
    text = str(e.value)
    if "no server yet" in text:
        pytest.skip(text)
    assert "did not start" in text and "_LIBRARY.RDB" in text and "Prosj\u00e6kt" in text
