r"""Check the simulator's DLL and SQL access against the real things.

python tools/sim_outside_check.py [--ctsim path\to\ctsim.exe] [--project dir]
"""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ctgedit.sim import client as C  # noqa: E402

EXAMPLE = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"
HOST_FILE = "ANName.ci"

#: thin wrappers: plain STRING and INT parameters, so call text can call them
WRAPPERS = """

INT FUNCTION OxConnect(STRING s)
	RETURN SQLConnect(s);
END

INT FUNCTION OxExec(INT h, STRING s)
	RETURN SQLExec(h, s);
END

INT FUNCTION OxCall(INT h, STRING s)
	RETURN SQLCall(h, s);
END

STRING FUNCTION OxScalar(INT h, STRING s)
	INT bNull;
	STRING v;
	v = SQLGetScalar(h, s, bNull);
	RETURN v + "|" + IntToStr(bNull);
END

INT FUNCTION OxRecordset(INT h, STRING s)
	RETURN SQLGetRecordset(h, s);
END

STRING FUNCTION OxField(INT h, STRING f, INT nRow = -1)
	RETURN SQLGetField(h, f, nRow);
END

INT FUNCTION OxIsNull(INT h, STRING f)
	RETURN SQLIsNullField(h, f, -1);
END

STRING FUNCTION OxFieldInfo(INT h, INT n)
	STRING s;
	INT w;
	INT e;
	e = SQLFieldInfo(h, n, s, w);
	RETURN s + ":" + IntToStr(w) + ":" + IntToStr(e);
END

INT FUNCTION OxSet(INT h, STRING s)
	RETURN SQLSet(h, s);
END

INT FUNCTION OxParamStr(INT h, STRING n, STRING v, INT t)
	RETURN SQLParamsSetAsString(h, n, v, t);
END

INT FUNCTION OxParamInt(INT h, STRING n, INT v)
	RETURN SQLParamsSetAsInt(h, n, v);
END

STRING FUNCTION OxErr(INT h = -1)
	RETURN SQLErrMsg(h);
END

STRING FUNCTION OxDllEx(INT h, STRING s)
	RETURN DLLCallEx(h, s);
END

REAL FUNCTION OxDllExR(INT h, REAL a, REAL b)
	RETURN DLLCallEx(h, a, b);
END
"""


class Check:
    def __init__(self, session):
        self.s = session
        self.failed = 0
        self.count = 0
        self.dialogs: "queue.Queue[dict]" = queue.Queue()
        self.options: "queue.Queue[dict]" = queue.Queue()
        session.add_listener(self._event)

    def _event(self, name, body):
        name = str(name).split("/")[-1]
        if name == "dialog":
            self.dialogs.put(body)
        elif name == "optionsChanged":
            self.options.put(body)

    def call(self, expr, timeout=40.0):
        try:
            return self.s.request("call", {"expr": expr, "timeoutMs": 30000}, timeout=timeout)
        except C.CtsimError as e:
            return {"result": None, "failed": str(e)}

    def result(self, expr):
        body = self.call(expr)
        if body.get("failed"):
            print("      %s: %s" % (expr, body["failed"]))
        return body.get("result")

    def asked(self, expr, answer, expect_action):
        """Run ``expr``, answer the permission question it raises, return the result."""
        out = {}
        t = threading.Thread(target=lambda: out.update(self.call(expr)), daemon=True)
        t.start()
        try:
            d = self.dialogs.get(timeout=20)
        except queue.Empty:
            self.check("a question for %s" % expr, None, "a permission dialog")
            t.join(40)
            return out.get("result")
        self.check("the question", (d.get("kind"), d.get("action")), ("permission", expect_action))
        print("      asked: %s | %s | detail: %s" % (d.get("title"), d.get("text"), d.get("detail")))
        self.s.request("answer", {"id": d.get("id"), "permission": answer})
        t.join(40)
        return out.get("result")

    def check(self, what, got, want):
        self.count += 1
        ok = want(got) if callable(want) else got == want
        if not ok:
            self.failed += 1
        print(
            "  %s %s: %r%s"
            % ("ok  " if ok else "FAIL", what, got, "" if ok or callable(want) else " (wanted %r)" % (want,))
        )
        return ok


def _handle(v) -> bool:
    try:
        return v is not None and int(v) >= 0
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ctsim", default="")
    ap.add_argument("--project", default=EXAMPLE)
    args = ap.parse_args()
    loc = C.find_engine(args.ctsim or None)
    servers = os.environ[C.SERVERS_ENV] = tempfile.mkdtemp(prefix="ctsim_outside_servers_")
    data = tempfile.mkdtemp(prefix="ctsim_outside_db_")
    print("engine:", loc.exe)
    s = C.open_session(args.project, location=loc, viewer=True)
    c = Check(s)
    try:
        hello = s.hello
        c.check("capability outside", (hello.get("capabilities") or {}).get("outside"), True)
        opts = hello.get("options") or {}
        c.check("dll and sql are off by default", (opts.get("dll"), opts.get("sql")), ("off", "off"))
        host = os.path.join(args.project, HOST_FILE)
        with open(host, encoding="cp1252") as f:
            text = f.read() + WRAPPERS
        done = threading.Event()
        got = {}
        s.reload_file(
            host, callback=lambda body, err: (got.update(body or {}, err=err), done.set()), text=text
        )
        done.wait(120)
        if not c.check("the wrapper functions compile", got.get("compiled"), True):
            print("      ", got)
            return 1

        print("off:")
        c.check("DLLOpen", c.result('DLLOpen("kernel32.dll", "lstrlenA", "JC")'), "-1")
        c.check("SQLConnect", c.result('OxConnect("DSN=Citect_Dbf")'), "-1")
        c.check("SQLErrMsg", c.result("OxErr()"), "SQL Error")

        print("on, DLLs:")
        s.request("options", {"dll": "on", "sql": "on"})
        h = c.result('DLLOpen("kernel32.dll", "lstrlenA", "JC")')
        c.check("DLLOpen kernel32 lstrlenA", h, _handle)
        c.check("DLLCall (stdcall, a string in)", c.result('DLLCall(%s, "^"hello world^"")' % h), "11")
        c.check("DLLCallEx", c.result('OxDllEx(%s, "abc")' % h), "3")
        p = c.result('DLLOpen("msvcrt.dll", "pow", "BBB")')
        c.check("DLLCall (cdecl, reals)", c.result('DLLCall(%s, "2, 10")' % p), "1024.")
        c.check(
            "DLLCallEx (reals)",
            c.result("OxDllExR(%s, 2.0, 0.5)" % p),
            lambda v: v is not None and abs(float(v) - 1.41421) < 1e-4,
        )
        g = c.result('DLLOpen("kernel32.dll", "GetCommandLineA", "C")')
        c.check(
            "DLLCall (a string out: the helper's command line)",
            c.result('DLLCall(%s, "")' % g),
            lambda v: v is not None and "dllhost" in v,
        )
        n = c.result('DLLOpen("msvcrt.dll", "strlen", "JJ")')
        c.check("a call that crashes in the library gives nothing", c.result('DLLCall(%s, "0")' % n), "")
        c.check("the next call works", c.result('DLLCall(%s, "^"ab^"")' % h), "2")
        x = c.result('DLLOpen("kernel32.dll", "ExitProcess", "JJ")')
        c.check("a call that ends the helper gives nothing", c.result('DLLCall(%s, "7")' % x), "")
        c.check("a new helper loads the function again", c.result('DLLCall(%s, "^"abcd^"")' % h), "4")
        c.check("DLLClose", c.result("DLLClose(%s)" % h), "0")
        c.check("a missing library", c.result('DLLOpen("C:\\nowhere\\none.dll", "f", "J")'), "-1")

        print("on, SQL (the dBase ODBC driver on %s):" % data)
        conn = "Driver={Microsoft dBase Driver (*.dbf)};DriverID=277;Dbq=%s" % data
        h = c.result('OxConnect("%s")' % conn)
        if not c.check("SQLConnect", h, _handle):
            print("      ", c.result("OxErr()"))
            return 1
        c.check("SQLInfo state", c.result("SQLInfo(%s, 5)" % h), "Open")
        # (a statement that changes no rows gives -1, as ADO.NET's ExecuteNonQuery does)
        c.check(
            "CREATE TABLE",
            c.result('OxCall(%s, "CREATE TABLE people (Name CHAR(20), Age INTEGER, Born DATE)")' % h),
            lambda v: v in ("-1", "0"),
        )
        # a short message shows the whole text: the driver's message, a '.', its error list
        c.check("a statement that is not SQL", c.result('OxCall(%s, "NONSENSE")' % h), "-1")
        msg = c.result("OxErr(%s)" % h)
        c.check(
            "SQLErrMsg lists the driver's errors",
            msg,
            lambda v: v is not None and ".Odbc Info: " in v and "Index #0: Error: " in v and len(v) <= 253,
        )
        print("      ", repr(msg))
        failed = msg
        c.check(
            "the table is a real file",
            sorted(f.lower() for f in os.listdir(data)),
            lambda v: "people.dbf" in v,
        )
        for name, age, born in (("Ann", 30, "1990-02-03"), ("Bob", 41, "1984-07-08")):
            c.check(
                "INSERT %s" % name,
                c.result(
                    "OxCall(%s, \"INSERT INTO people VALUES ('%s', %d, {d '%s'})\")" % (h, name, age, born)
                ),
                "1",
            )
        q = c.result("SQLQueryCreate(%s)" % h)
        c.check("SQLQueryCreate", q, lambda v: _handle(v) and int(v) != int(h))
        c.result('OxSet(%s, "INSERT INTO people (Name, Age) VALUES (?, ?)")' % q)
        c.check("SQLParamsSetAsString", c.result('OxParamStr(%s, "n", "Cid", 3)' % q), "0")
        c.check("SQLParamsSetAsInt", c.result('OxParamInt(%s, "a", 52)' % q), "0")
        c.check("INSERT with parameters", c.result('OxCall(%s, "")' % q), "1")
        c.check("SQLExec", c.result('OxExec(%s, "SELECT Name, Age, Born FROM people ORDER BY Age")' % h), "0")
        c.check("SQLNumFields", c.result("SQLNumFields(%s)" % h), "3")
        c.check(
            "SQLFieldInfo",
            [c.result("OxFieldInfo(%s, %d)" % (h, k)) for k in range(4)],
            lambda v: (
                v[0].lower().startswith("name:")
                and v[0].endswith(":0")
                and int(v[0].split(":")[1]) >= 20
                and v[1].lower().startswith("age:")
                and v[2].lower() == "born:26:0"
                and v[3] == ":0:266"
            ),
        )
        rows = []
        while c.result("SQLNext(%s)" % h) == "0" and len(rows) < 10:
            rows.append(
                (
                    c.result('OxField(%s, "Name")' % h),
                    c.result('OxField(%s, "AGE")' % h),
                    c.result('OxField(%s, "Born")' % h),
                    c.result('OxIsNull(%s, "Born")' % h),
                )
            )
        c.check(
            "the rows, one SQLNext each",
            rows,
            lambda r: (
                [x[:2] for x in r] == [("Ann", "30"), ("Bob", "41"), ("Cid", "52")]
                and r[2][3] == "1"
                and (r[0][2] or "").startswith("1990-02-03")
            ),
        )
        # stepping past the last row is the manager's last error (handle -1), not the connection's
        c.check(
            "SQLErrMsg() after the last row",
            c.result("OxErr()"),
            "SQL Error (14 - End of data for recordset) on Handle (-1). End of data for recordset.",
        )
        # (the connection's own last error is still the failed statement's: nothing clears it)
        c.check("SQLErrMsg(hSQL) after the last row", c.result("OxErr(%s)" % h), failed)
        c.check("SQLEnd", c.result("SQLEnd(%s)" % h), "0")
        r = c.result('OxRecordset(%s, "SELECT * FROM people WHERE Age > 35")' % h)
        c.check("SQLGetRecordset", r, _handle)
        c.check("SQLRowCount", c.result("SQLRowCount(%s)" % r), "2")
        c.check("SQLGetField of a row", c.result('OxField(%s, "Name", 1)' % r), lambda v: v in ("Bob", "Cid"))
        c.check("SQLEnd of the recordset", c.result("SQLEnd(%s)" % r), "0")
        c.check("UPDATE", c.result('OxCall(%s, "UPDATE people SET Age = Age + 1 WHERE Age > 35")' % h), "2")
        c.check("SQLNumChange", c.result("SQLNumChange(%s)" % h), "2")
        c.check(
            "SQLGetScalar",
            c.result('OxScalar(%s, "SELECT SUM(Age) FROM people")' % h),
            lambda v: v is not None and v.endswith("|0") and float(v[:-2]) == 125.0,
        )
        c.check(
            "SQLGetScalar of NULL",
            c.result('OxScalar(%s, "SELECT Born FROM people WHERE Age = 52")' % h),
            "|1",
        )
        c.check(
            "a statement the database refuses",
            c.result('OxCall(%s, "SELECT nothing FROM nowhere")' % h),
            "-1",
        )
        msg = c.result("OxErr(%s)" % h)
        c.check(
            "SQLErrMsg has the driver's message",
            msg,
            lambda v: v is not None and v.startswith("SQL Error (4 - Database error) on Handle"),
        )
        print("      ", msg)
        c.check("SQLDisconnect", c.result("SQLDisconnect(%s)" % h), "0")

        print("on, SQL through OLE DB (the Jet provider on the same folder):")
        oledb = (
            "SCADA Data Provider=OLEDB;Provider=Microsoft.Jet.OLEDB.4.0;Data Source=%s;"
            "Extended Properties=dBASE IV" % data
        )
        h2 = c.result('OxConnect("%s")' % oledb)
        if c.check("SQLConnect", h2, _handle):
            c.check("SQLGetScalar", c.result('OxScalar(%s, "SELECT COUNT(*) FROM people")' % h2), "3|0")
            c.check("SQLExec", c.result('OxExec(%s, "SELECT Name FROM people ORDER BY Age")' % h2), "0")
            c.check("SQLNext", c.result("SQLNext(%s)" % h2), "0")
            c.check("SQLGetField", c.result('OxField(%s, "Name")' % h2), "Ann")
            c.check("a statement that is not SQL", c.result('OxCall(%s, "NONSENSE")' % h2), "-1")
            msg = c.result("OxErr(%s)" % h2)
            c.check(
                "SQLErrMsg lists the provider's errors", msg, lambda v: v is not None and ".OleDb Info: " in v
            )
            print("      ", repr(msg))
            c.check("SQLDisconnect", c.result("SQLDisconnect(%s)" % h2), "0")
        else:
            print("      ", c.result("OxErr()"))

        print("ask:")
        while not c.options.empty():
            c.options.get()
        s.request("options", {"dll": "ask", "sql": "ask", "dllAllowed": [], "sqlAllowed": []})
        c.check("connect, denied", c.asked('OxConnect("%s")' % conn, "deny", "connect"), "-1")
        h = c.asked('OxConnect("%s")' % conn, "once", "connect")
        c.check("connect, allowed once", h, _handle)
        count = 'OxScalar(%s, "SELECT COUNT(*) FROM people")' % h
        c.check("a query is not asked about", c.result(count), "3|0")
        delete = 'OxCall(%s, "DELETE FROM people WHERE Age = 30")' % h
        c.check("a change, denied", c.asked(delete, "deny", "write"), "-1")
        c.check("nothing was deleted", c.result(count), "3|0")
        c.check("a change, allowed for this simulation", c.asked(delete, "session", "write"), "1")
        c.check(
            "the next change is not asked about",
            c.result('OxCall(%s, "DELETE FROM people WHERE Age = 42")' % h),
            "1",
        )
        c.check("one row is left", c.result(count), "1|0")
        c.result("SQLDisconnect(%s)" % h)
        c.check(
            "a library, denied", c.asked('DLLOpen("kernel32.dll", "lstrlenA", "JC")', "deny", "load"), "-1"
        )
        lh = c.asked('DLLOpen("kernel32.dll", "lstrlenA", "JC")', "always", "load")
        c.check("a library, always allowed", lh, _handle)
        ch = {}
        try:
            while not ch.get("dllAllowed"):
                ch = c.options.get(timeout=10)
        except queue.Empty:
            pass
        c.check(
            "the whitelist reached the viewers",
            ch.get("dllAllowed"),
            lambda v: isinstance(v, list) and len(v) == 1 and v[0].lower().endswith("kernel32.dll"),
        )
        c.check(
            "another function of the library is not asked about",
            c.result('DLLOpen("kernel32.dll", "GetTickCount", "J")'),
            _handle,
        )
        s.request("options", {"dll": "off", "sql": "off"})
        c.check("off again", c.result('DLLCall(%s, "^"x^"")' % lh), "")
    finally:
        try:
            s.close()
        finally:
            shutil.rmtree(data, ignore_errors=True)
            shutil.rmtree(servers, ignore_errors=True)
    print("%d checks, %d failed" % (c.count, c.failed))
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
