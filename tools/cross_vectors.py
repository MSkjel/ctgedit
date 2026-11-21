r"""Write the vectors ctgedit and the Cicode engine both check against.

.venv\Scripts\python.exe tools\cross_vectors.py [--out <engine>\conformance\shared]
"""

import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
USER = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"


def names_vectors():
    from ctgedit import cicode_names as cn
    from ctgedit.format.textcodec import CODEC

    rows = []
    for b in range(256):
        ch = bytes([b]).decode(CODEC)
        rows.append({"byte": b, "char": "U+%04X" % ord(ch), "start": cn.is_start(ch), "part": cn.is_part(ch)})
    return {
        "format": "ctsim-vectors/cicode-names/0",
        "what": "every Windows-1252 byte (decoded): starts / continues a Cicode name",
        "bytes": rows,
    }


def project_inputs():
    """sha1 of MASTER.DBF and every include.dbf under the User folder (sorted paths)."""
    h = hashlib.sha1()
    files = [os.path.join(USER, "MASTER.DBF")]
    for p in sorted(os.listdir(USER)):
        d = os.path.join(USER, p)
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.lower() == "include.dbf":
                    files.append(os.path.join(d, f))
    for f in files:
        h.update(os.path.relpath(f, USER).lower().encode())
        with open(f, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def tagscope_vectors():
    from ctgedit.io import tagscope as T

    cases = []
    for p in sorted(os.listdir(USER), key=str.lower):
        d = os.path.join(USER, p)
        if not os.path.isdir(d) or not any(
            f.lower() == "include.dbf" or f.lower().endswith(".ctg") for f in os.listdir(d)
        ):
            continue
        try:
            roots = T.scope(d).roots
        except Exception:  # noqa: BLE001 -- not a project
            continue
        for r in sorted(set(roots), key=str.lower):
            s = T.scope(d, r)
            cases.append({"project": p, "root": r, "projects": [[q.name, q.relation] for q in s.projects]})
    return {
        "format": "ctsim-vectors/tagscope/0",
        "user": USER,
        "inputs": project_inputs(),
        "what": "the tag scope of each project compiled as each root: projects in search "
        "order [name, relation]",
        "cases": cases,
    }


def main(argv):
    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "external",
        "cicode-sim",
        "conformance",
        "shared",
    )
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    os.makedirs(out, exist_ok=True)
    for name, data in (("cicode_names.json", names_vectors()), ("tagscope.json", tagscope_vectors())):
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=1)
            fh.write("\n")
        n = len(data.get("bytes") or data.get("cases"))
        print("%s: %d entries" % (name, n))


if __name__ == "__main__":
    main(sys.argv[1:])
