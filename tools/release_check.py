"""Release gate: run before tagging or building a release.

python tools/release_check.py                     everything
python tools/release_check.py --skip-tests        no pytest
python tools/release_check.py --frozen dist/ctgedit/ctgedit.exe
"""

from __future__ import annotations

import argparse
import compileall
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

#: the smoke pages in this repo: the fake install's demo plant (tests/fixtures/FxInstall), so a
#: machine without Plant SCADA (CI) smoke-tests too
FAKE_SMOKE_PAGES = [
    os.path.join(REPO, "tests", "fixtures", "FxInstall", "User", "FxDemo", "Overview.ctg"),
    os.path.join(REPO, "tests", "fixtures", "FxInstall", "User", "FxDemo", "Pump.ctg"),
]
#: and with an install: a classic page and a Situational Awareness page of its projects
SMOKE_CANDIDATES = [
    ("Example", "Mixer.ctg"),
    ("ExampleSA", "Mixing_L3.ctg"),
]
INSTALL_USERS = [
    r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User",
]
#: tracked paths whose uncommitted state makes a build unidentifiable
SOURCE_PATHS = (
    "ctgedit",
    "tests",
    "tests_corpus",
    "tests_live",
    "tools",
    "packaging",
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "README.md",
)
_VERSION_RX = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")
_PIN_RX = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s;#]+)")


class Result:
    def __init__(self, name: str):
        self.name, self.ok, self.detail, self.skipped = name, False, "", False


def _env(**extra) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    env.update(extra)
    return env


def _pins(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _PIN_RX.match(line)
            if m:
                out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def check_version() -> Result:
    r = Result("version")
    import importlib.metadata as md
    from ctgedit import __version__

    problems = []
    if not _VERSION_RX.match(__version__):
        problems.append("ctgedit.__version__ %r is not a release number (e.g. 1.2.0)" % __version__)
    import tomllib

    try:
        with open(os.path.join(REPO, "pyproject.toml"), "rb") as fh:
            project = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        project = {}
        problems.append("pyproject.toml cannot be read (%s)" % e)
    dyn = project.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version", {})
    if project and dyn.get("attr") != "ctgedit.__version__":
        problems.append("pyproject.toml does not take its version from ctgedit.__version__")
    deps = []
    for spec in project.get("project", {}).get("dependencies", []):
        m = _PIN_RX.match(spec)
        if not m:
            problems.append("pyproject.toml dependency %r is not pinned with ==" % spec)
        elif ";" not in spec or "win32" not in spec or sys.platform == "win32":
            deps.append((m.group(1).lower().replace("_", "-"), m.group(2)))
    req = os.path.join(REPO, "requirements.txt")
    pins = _pins(req) if os.path.exists(req) else {}
    if not pins:
        problems.append("requirements.txt pins nothing")
    lock = os.path.join(REPO, "requirements.lock")
    locked = _pins(lock) if os.path.exists(lock) else {}
    for name, ver in pins.items():
        if locked.get(name) != ver:
            problems.append(
                "requirements.lock has %s==%s, requirements.txt %s -- run "
                "tools/lock_requirements.py" % (name, locked.get(name, "(none)"), ver)
            )
    for name, ver in list(pins.items()) + deps:
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            if name == "pywin32" and sys.platform != "win32":
                continue
            problems.append("%s==%s is pinned but not installed" % (name, ver))
            continue
        if have != ver:
            problems.append("%s is pinned to %s but %s is installed" % (name, ver, have))
    r.ok = not problems
    r.detail = "; ".join(problems) if problems else "version %s, %d pins match" % (__version__, len(pins))
    return r


def check_sources(allow_dirty: bool) -> Result:
    r = Result("sources")
    if allow_dirty:
        r.ok, r.skipped, r.detail = True, True, "skipped (--allow-dirty)"
        return r
    try:
        out = subprocess.run(
            ["git", "-C", REPO, "status", "--porcelain", "--", *SOURCE_PATHS],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        r.detail = "git is not available (%s)" % e
        return r
    if out.returncode != 0:
        r.detail = "git status failed: %s" % out.stderr.strip()
        return r
    lines = []
    for line in out.stdout.splitlines():
        path = line[3:].strip().strip('"')
        # scratch drivers / outputs are ignored by .gitignore rules anyway
        if os.path.basename(path).startswith("_") and path.startswith("tools/"):
            continue
        lines.append(line.strip())
    r.ok = not lines
    r.detail = (
        "all committed"
        if r.ok
        else "%d uncommitted source file(s), e.g. %s -- commit them (or pass --allow-dirty "
        "for a local check)" % (len(lines), ", ".join(lines[:4]))
    )
    return r


def check_compile() -> Result:
    r = Result("compile")
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = compileall.compile_dir(os.path.join(REPO, "ctgedit"), quiet=1, force=False)
    r.ok = bool(ok)
    r.detail = "all modules compile" if ok else buf.getvalue().strip()[-600:]
    return r


def _pytest_summary(text: str) -> str:
    for line in reversed(text.splitlines()):
        if re.search(r"\b(passed|failed|error|errors|no tests ran)\b", line):
            return line.strip("= ").strip()
    return text.strip().splitlines()[-1] if text.strip() else "no output"


def check_tests(tests: List[str], extra: List[str], timeout: int) -> Result:
    r = Result("tests")
    cmd = [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", *extra]
    t0 = time.time()
    try:
        out = subprocess.run(cmd, cwd=REPO, env=_env(), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        r.detail = "pytest did not finish within %d s" % timeout
        return r
    text = out.stdout + out.stderr
    r.ok = out.returncode == 0
    r.detail = "%s (%.0f s)" % (_pytest_summary(text), time.time() - t0)
    if not r.ok:
        failed = [ln for ln in text.splitlines() if ln.startswith(("FAILED", "ERROR"))]
        if failed:
            r.detail += "\n      " + "\n      ".join(failed[:15])
    return r


def default_smoke_pages() -> List[str]:
    pages = [p for p in FAKE_SMOKE_PAGES if os.path.isfile(p)]
    try:
        from ctgedit.io.citect_config import live_user_dir

        users = [u for u in [live_user_dir()] if u] + INSTALL_USERS
    except Exception:  # noqa: BLE001
        users = INSTALL_USERS
    for project, page in SMOKE_CANDIDATES:
        hit = next(
            (os.path.join(u, project, page) for u in users if os.path.isfile(os.path.join(u, project, page))),
            None,
        )
        if hit:
            pages.append(hit)
    return pages


def _run_smoke(cmd: List[str], name: str, timeout: int, frozen: bool = False) -> Result:
    r = Result(name)
    fd, report = tempfile.mkstemp(prefix="ctgedit-smoke-", suffix=".json")
    os.close(fd)
    logs = tempfile.mkdtemp(prefix="ctgedit-smoke-logs-")
    env = _env(CTG_LOG_DIR=logs, CTG_NO_SINGLE_INSTANCE="1")
    if frozen:
        # the built program must run on what it bundles, never on this
        # source tree
        env.pop("PYTHONPATH", None)
    try:
        out = subprocess.run(
            cmd + ["--report", report], cwd=REPO, capture_output=True, text=True, timeout=timeout, env=env
        )
        try:
            with open(report, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError, ValueError:
            data = None
        if data is None:
            r.detail = "no report (exit code %d): %s" % (
                out.returncode,
                (out.stderr or out.stdout).strip()[-600:],
            )
            return r
        rows = []
        for p in data.get("pages", []):
            rows.append(
                "%s %s%s"
                % (
                    "ok  " if p["ok"] else "FAIL",
                    os.path.basename(p["page"]),
                    "" if p["ok"] else " -- " + p["error"],
                )
            )
        if data.get("error"):
            rows.append("FAIL " + data["error"])
        if frozen and not data.get("frozen"):
            rows.append(
                "FAIL %s ran as a source run (%s), not as the built program -- "
                "rebuild it with packaging\\build.ps1"
                % (os.path.basename(cmd[0]), data.get("executable", "?"))
            )
        r.ok = bool(out.returncode == 0 and data.get("ok", False) and (data.get("frozen") or not frozen))
        r.detail = "%s\n      %s" % (data.get("version", ""), "\n      ".join(rows))
    except subprocess.TimeoutExpired:
        r.detail = "the smoke test did not finish within %d s" % timeout
    finally:
        try:
            os.remove(report)
        except OSError:
            pass
        import shutil

        shutil.rmtree(logs, ignore_errors=True)
    return r


def check_smoke(pages: List[str], timeout: int) -> Result:
    if not pages:
        r = Result("smoke")
        r.detail = "no smoke page found; pass --page <file.ctg>"
        return r
    return _run_smoke([sys.executable, "-m", "ctgedit.app", "--smoke-test", *pages], "smoke", timeout)


def check_frozen(exe: str, pages: List[str], timeout: int) -> Result:
    if not os.path.isfile(exe):
        r = Result("frozen")
        r.detail = "%s does not exist -- build it with packaging\\build.ps1" % exe
        return r
    if not pages:
        r = Result("frozen")
        r.detail = "no smoke page found; pass --page <file.ctg>"
        return r
    return _run_smoke([os.path.abspath(exe), "--smoke-test", *pages], "frozen", timeout, frozen=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-tests", action="store_true", help="do not run pytest")
    ap.add_argument("--skip-smoke", action="store_true", help="do not run the smoke test")
    ap.add_argument(
        "--allow-dirty", action="store_true", help="do not require every source file to be committed"
    )
    ap.add_argument(
        "--tests", nargs="+", default=["tests"], metavar="PATH", help="test files / folders (default: tests)"
    )
    ap.add_argument("--pytest-args", default="", help="extra pytest arguments, one string")
    ap.add_argument(
        "--page",
        action="append",
        default=[],
        metavar="CTG",
        help="smoke-test this page (repeatable; default the demo's Overview + Pump, and Mixer + Mixing_L3 "
        "of an installed Plant SCADA)",
    )
    ap.add_argument("--frozen", metavar="EXE", help="also smoke-test this built ctgedit.exe")
    ap.add_argument(
        "--only",
        nargs="+",
        metavar="CHECK",
        choices=["version", "sources", "compile", "tests", "smoke", "frozen"],
        help="run only these checks",
    )
    ap.add_argument("--timeout", type=int, default=3600, help="pytest time limit, seconds")
    a = ap.parse_args(argv)

    def wanted(name):
        return a.only is None or name in a.only

    pages = [os.path.abspath(p) for p in a.page] or default_smoke_pages()
    results: List[Result] = []
    if wanted("version"):
        results.append(check_version())
    if wanted("sources"):
        results.append(check_sources(a.allow_dirty))
    if wanted("compile"):
        results.append(check_compile())
    if wanted("tests") and not a.skip_tests:
        results.append(check_tests(a.tests, shlex.split(a.pytest_args, posix=False), a.timeout))
    if wanted("smoke") and not a.skip_smoke:
        results.append(check_smoke(pages, 900))
    if a.frozen and wanted("frozen"):
        results.append(check_frozen(a.frozen, pages, 900))
    width = max(len(r.name) for r in results) if results else 0
    for r in results:
        state = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
        print("%-4s  %-*s  %s" % (state, width, r.name, r.detail))
    failed = [r.name for r in results if not r.ok]
    print("\nRelease check %s" % ("FAILED: " + ", ".join(failed) if failed else "passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
