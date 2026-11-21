"""Export a page as the Cicode engine's ctsim-page/0 model (ctgedit/sim/pagemodel.py).

    python tools/sim_page_model.py <page.ctg> [--indent N] [--check] [--no-owners] > page.json

The JSON goes to stdout (ASCII). --check re-reads the JSON and compares it with the page
(pagemodel.check_model); differences go to stderr and the exit code is 1. Read only: the page
and its project are never written.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("CTG_NO_CTF_PRECHECK", "1")


def main(argv) -> int:
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    indent = None
    if "--indent" in args:
        k = args.index("--indent")
        indent = int(args[k + 1])
        del args[k : k + 2]
    check = "--check" in args
    owners = "--no-owners" not in args
    args = [a for a in args if a not in ("--check", "--no-owners")]
    from ctgedit.editing import EditableDocument
    from ctgedit.sim import pagemodel

    doc = EditableDocument.open(args[0])
    model = doc.export_page_model(owners=owners)
    text = pagemodel.dumps(model, indent=indent)
    sys.stdout.buffer.write(text.encode("ascii") + b"\n")
    sys.stdout.flush()
    if check:
        bad = pagemodel.check_model(pagemodel.loads(text), doc)
        for b in bad:
            print(b, file=sys.stderr)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
