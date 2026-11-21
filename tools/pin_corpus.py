"""Pin the corpus files the tests read.

python tools/pin_corpus.py                       every file the tests read
python tools/pin_corpus.py FxPage.ctg            only these names
python tools/pin_corpus.py --role sweep_pages    only these roles
"""

import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests_corpus"))
import corpus  # noqa: E402


def main(argv):
    pins = dict(corpus.pins())
    roles, names = [], []
    it = iter(argv)
    for a in it:
        if a == "--role":
            roles.append(next(it, ""))
        else:
            names.append(a.lower())
    if not (roles or names):
        for d in corpus.TEST_DIRS:
            for f in glob.glob(os.path.join(d, "test_*.py")):
                with open(f, encoding="utf-8") as fh:
                    names.extend(corpus.names_in(fh.read()))
        roles = sorted(corpus.roles())
    names = sorted(set(names))
    by_role = set()
    for r in roles:
        if r not in corpus.roles():
            print("%s is not set in %s" % (r, corpus.LOCAL_FILE))
        by_role.update(corpus.files_of_role(r))
    rels = by_role | {rel for name in names for rel in corpus.files_named(name)}
    n = 0
    for rel in sorted(rels):
        if not os.path.isfile(os.path.join(corpus.R2_USER, rel)):
            print("not installed: %s" % rel)
            continue
        pins[rel.replace("\\", "/")] = corpus.sha1(rel)
        n += 1
    with open(corpus.PINS_FILE, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(pins.items())), fh, indent=1)
    print(
        "pinned %d files (%d names, %d files of %d roles) in %s"
        % (n, len(names), len(by_role), len(roles), corpus.PINS_FILE)
    )


if __name__ == "__main__":
    main(sys.argv[1:])
