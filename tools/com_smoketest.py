"""Smoke test of the Graphics Builder COM server from 64-bit Python."""

import sys
import pythoncom
import win32com.client

CLSID = "{EB087A43-4189-46A9-8819-F717B9963C31}"


def main():
    pythoncom.CoInitialize()
    print("Creating GraphicsBuilder COM object...", flush=True)
    gb = win32com.client.Dispatch(CLSID)
    print("  -> created:", gb, flush=True)

    try:
        ti = gb._oleobj_.GetTypeInfo()
        ta = ti.GetTypeAttr()
        print("  funcs in default interface:", ta.cFuncs, flush=True)
    except Exception as e:
        print("  typeinfo introspection failed:", e, flush=True)

    for prop in ("Visible",):
        try:
            val = getattr(gb, prop)
            print("  %s = %r" % (prop, val), flush=True)
        except Exception as e:
            print("  read %s failed: %s" % (prop, e), flush=True)

    try:
        gb.Visible = False
        print("  set Visible=False OK", flush=True)
    except Exception as e:
        print("  set Visible failed:", e, flush=True)

    try:
        gb.Quit()
        print("  Quit() OK", flush=True)
    except Exception as e:
        print("  Quit failed:", e, flush=True)

    print("SMOKETEST DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("SMOKETEST ERROR:", type(e).__name__, e, flush=True)
        sys.exit(2)
