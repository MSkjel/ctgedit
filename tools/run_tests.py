"""Run the test suite in groups, several pytest processes at once.

python tools/run_tests.py [<log>] [--workers 3] [--suite tests|corpus|live|all] [--durations N]
                          [--shard K/N]

--durations N: the log ends with the N slowest tests and the N slowest files.
--shard K/N: only the K-th of N parts of the groups (CI runs the parts as parallel jobs; the
slow files lead the groups, so each part gets its share of them).
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

args = sys.argv[1:]
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = (
    args[0]
    if args and not args[0].startswith("--")
    else os.path.join(root, "tools", "_tests", "last_run.log")
)
os.makedirs(os.path.dirname(os.path.abspath(log)), exist_ok=True)
workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 3
only = set(args[args.index("--files") + 1].split(",")) if "--files" in args else None
ignore = {"test_help.py"} if "--ignore-help" in args else set()
py = sys.executable
timeout = int(args[args.index("--timeout") + 1]) if "--timeout" in args else 2700
#: a test stuck this long has its stack written (it is not stopped: the group's timeout does)
STUCK_TEST_S = 600
#: --suite: its folders
SUITES = {
    "tests": ("tests",),
    "corpus": ("tests_corpus",),
    "live": ("tests_live",),
    "all": ("tests", "tests_corpus", "tests_live"),
}
suite = args[args.index("--suite") + 1] if "--suite" in args else "tests"
if suite not in SUITES:
    sys.exit("--suite: one of %s" % ", ".join(SUITES))
n_slowest = int(args[args.index("--durations") + 1]) if "--durations" in args else 0
#: (seconds, test id) of every test phase (setup, call, teardown) of 0.5 s or more
durations: list = []
DURATION = re.compile(r"^([\d.]+)s (?:setup|call|teardown)\s+(\S+)", re.M)


def _wanted(folder, f):
    if f in ignore:
        return False
    return only is None or f in only or "%s/%s" % (folder, f) in only


#: (folder, file name)
files = [
    (d, os.path.basename(f))
    for d in SUITES[suite]
    for f in sorted(glob.glob(os.path.join(root, d, "test_*.py")))
]
files = [(d, f) for d, f in files if _wanted(d, f)]
# the files of 25 s or more (--durations shows them) each get a group of their own, first,
# the longest first
SLOW = [
    "test_session_soak.py",
    "test_incremental_render.py",
    "test_settings_audit_editing.py",
    "test_update_pages_parallel.py",
    "test_page_properties.py",
    "test_sim_live_reload.py",
    "test_settings_audit_panels.py",
    "test_underlay.py",
    "test_canvas_nav.py",
]
slow = sorted(((d, f) for d, f in files if f in SLOW), key=lambda x: SLOW.index(x[1]))
rest = [(d, f) for d, f in files if f not in SLOW]
#: files per process: each process pays about 5 s to start (Qt, the editor's imports)
GROUP = 6
groups = [[x] for x in slow]
for _d in SUITES[suite]:
    _mine = [x for x in rest if x[0] == _d]
    groups += [_mine[i : i + GROUP] for i in range(0, len(_mine), GROUP)]
if "--shard" in args:
    _k, _n = (int(x) for x in args[args.index("--shard") + 1].split("/"))
    if not 1 <= _k <= _n:
        sys.exit("--shard: K/N with 1 <= K <= N")
    groups = groups[_k - 1 :: _n]
base = tempfile.mkdtemp(prefix="ctgsuite_")
tot = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
#: the skips that are failures on a changed corpus file (tests/conftest.py: 'input changed'):
#: counted apart, they are not like a skip for a missing install
input_changed: list = []
#: the golden skips whose oracle file is older than its fixture (tests/fxinstall.oracle:
#: 'oracle stale'): a new capture of their units is due, counted apart too
oracle_stale: list = []
#: the failures, crashes and timeouts, printed after the total
problems: list = []


def _names(g):
    return " ".join("%s/%s" % x for x in g)


def run(k, g):
    env = dict(os.environ, PYTHONPATH=root)
    # the guard is for tests/ only (its conftest turns it on): tests_corpus/ and tests_live/
    # read the real install
    env.pop("CTG_HERMETIC", None)
    t0 = time.time()
    p = subprocess.Popen(
        [
            py,
            "-m",
            "pytest",
            "-q",
            "-rfEs",
            "-p",
            "no:cacheprovider",
            "-o",
            "faulthandler_timeout=%d" % STUCK_TEST_S,
            "--basetemp",
            os.path.join(base, "g%d" % k),
        ]
        + (["--durations=0", "--durations-min=0.5"] if n_slowest else [])
        + ["%s/%s" % x for x in g],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, err = p.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        # the whole tree: a test's own processes (the engine, a page job) go with it
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        out, err = p.communicate()
        timed_out = True
    r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
    r.timed_out = timed_out
    # the disk is small: a group's temp folder goes as soon as the group is done
    # (--keep-temp keeps them all)
    if "--keep-temp" not in args:
        shutil.rmtree(os.path.join(base, "g%d" % k), ignore_errors=True)
    return k, g, r, time.time() - t0


def _write_durations(out):
    per_test: dict = {}
    per_file: dict = {}
    for sec, tid in durations:
        per_test[tid] = per_test.get(tid, 0.0) + sec
        f = tid.split("::")[0]
        per_file[f] = per_file.get(f, 0.0) + sec
    for title, table in (("TESTS", per_test), ("FILES", per_file)):
        out.write("SLOWEST %s (the phases of 0.5 s or more):\n" % title)
        for name, sec in sorted(table.items(), key=lambda x: -x[1])[:n_slowest]:
            out.write("  %7.1fs %s\n" % (sec, name))


def main() -> int:
    t_all = time.time()
    with open(log, "w", encoding="utf-8") as out:
        out.write(
            "%d files in %d groups, %d workers (suite %s)\n"
            % (sum(len(g) for g in groups), len(groups), workers, suite)
        )
        out.flush()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(run, k, g) for k, g in enumerate(groups, 1)]
            for fut in as_completed(futs):
                k, g, r, dt = fut.result()
                tail = (r.stdout or "").strip().splitlines()
                durations.extend((float(m.group(1)), m.group(2)) for m in DURATION.finditer(r.stdout or ""))
                if r.timed_out:
                    # (its stdout ends in a test's dots, not a summary: written here)
                    err = (r.stderr or "").strip().splitlines()
                    stuck = [
                        ln
                        for ln in err
                        if "File " in ln and "site-packages" not in ln and "<frozen" not in ln
                    ]
                    tot["error"] += 1
                    problems.append("%s :: TIMED OUT after %ds" % (_names(g), timeout))
                    out.write(
                        "[%d/%d] %.0fs %s :: TIMED OUT after %ds (stopped)\n"
                        % (k, len(groups), dt, _names(g), timeout)
                    )
                    out.write(
                        "    the stuck stack (the last frames first):\n        %s\n"
                        % (
                            "\n        ".join(stuck[:12])
                            or "(no stack: no test was stuck for %ds)" % STUCK_TEST_S
                        )
                    )
                    out.flush()
                    continue
                summary = tail[-1] if tail else "(no output) " + (r.stderr or "")[-300:]
                for key in tot:
                    m = re.search(r"(\d+) %s" % key, summary)
                    if m:
                        tot[key] += int(m.group(1))
                out.write("[%d/%d] %.0fs %s :: %s\n" % (k, len(groups), dt, _names(g), summary))
                changed = [ln for ln in tail if ln.startswith("SKIPPED") and "input changed" in ln]
                if changed:
                    input_changed.extend(changed)
                    out.write("    " + "\n    ".join(changed[:20]) + "\n")
                stale = [ln for ln in tail if ln.startswith("SKIPPED") and "oracle stale" in ln]
                if stale:
                    oracle_stale.extend(stale)
                    out.write("    " + "\n    ".join(stale[:20]) + "\n")
                summarised = re.search(r"\d+ (passed|failed|error|skipped)|no tests ran", summary)
                if r.returncode not in (0, 5):
                    fails = [ln for ln in tail if ln.startswith(("FAILED", "ERROR"))]
                    if fails:
                        problems.extend(fails[:40])
                        out.write("    " + "\n    ".join(fails[:40]) + "\n")
                    elif summarised:
                        # every test passed, then the process died on its way out
                        # (a Qt teardown crash)
                        tot["error"] += 1
                        problems.append(
                            "%s :: CRASHED AT EXIT (0x%08X)" % (_names(g), r.returncode & 0xFFFFFFFF)
                        )
                        out.write(
                            "    CRASHED AT EXIT rc=%s (0x%08X) after the summary\n"
                            % (r.returncode, r.returncode & 0xFFFFFFFF)
                        )
                if not summarised:
                    # the process died (no pytest summary): pytest's faulthandler
                    # wrote the crashing thread's stack to stderr
                    err = (r.stderr or "").strip().splitlines()
                    ran = sum(
                        len(m.group(1))
                        for m in re.finditer(r"^([.FEsxX]+)(?:\s+\[\s*\d+%\])?$", r.stdout or "", re.M)
                    )
                    tot["error"] += 1
                    problems.append(
                        "%s :: CRASHED (0x%08X) after %d test(s)"
                        % (_names(g), r.returncode & 0xFFFFFFFF, ran)
                    )
                    out.write(
                        "    CRASHED rc=%s after %d test(s); stderr:\n        %s\n"
                        % (r.returncode, ran, "\n        ".join(err[-40:]))
                    )
                out.flush()
        out.write("TOTAL %s in %.0fs\n" % (tot, time.time() - t_all))
        if n_slowest:
            _write_durations(out)
        if input_changed:
            out.write(
                "INPUT CHANGED (failed on a corpus file changed since its pin; re-pin "
                "with tools/pin_corpus.py once checked): %d\n" % len(input_changed)
            )
        if oracle_stale:
            out.write(
                "ORACLE STALE (a golden test's oracle file is older than its fixture; "
                "capture its units again with tools/live/fixture_oracle.py): %d\n" % len(oracle_stale)
            )
    if "--keep-temp" not in args:
        shutil.rmtree(base, ignore_errors=True)
    print("TOTAL %s in %.0fs -> %s" % (tot, time.time() - t_all, log))
    # (on GitHub Actions: annotations, listed on the run's page)
    mark = "::error::" if os.environ.get("GITHUB_ACTIONS") == "true" else "    "
    for p in problems:
        print(mark + p)
    if input_changed:
        print(
            "INPUT CHANGED: %d failing test(s) read a corpus file changed since its pin" % len(input_changed)
        )
    if oracle_stale:
        print("ORACLE STALE: %d golden test(s) wait for a new capture of their units" % len(oracle_stale))
    return 1 if tot["failed"] or tot["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
