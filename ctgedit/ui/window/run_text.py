"""How a run's results read: page names, labels, the follow-up words, result lines."""

from __future__ import annotations

from typing import List

from ..main_window_base import main_window_class


from ..prefs.settings import option

from ..main_window_base import (
    app_settings,
    os,
)


def page_label(paths):
    """``label(path)``: a page's name, with its project when another of ``paths`` has the
    same name in another project ('!fx_detail (FxMain)'; updatepages.same_name_label)."""
    from ...io.updatepages import same_name_label

    rec_label = same_name_label(list(paths or ()))

    def label(path) -> str:
        a = os.path.abspath(str(path))
        return rec_label(
            {
                "name": os.path.splitext(os.path.basename(a))[0],
                "project": os.path.basename(os.path.dirname(a)),
            }
        )

    return label


def follow_words(saved=(), kinds_of=None) -> str:
    """What the automatic update brings up to date after a run."""
    st = app_settings()
    kinds = {str((kinds_of or {}).get(str(s), "genie")) for s in saved or ()}
    words = []
    if "template" in kinds and option("auto_update_template"):
        words.append("templates")
    if "super genie" in kinds:
        words.append("super genies")
    words += [
        w
        for w, key in (("genies", "opt/auto_update_genies"), ("symbols", "opt/auto_update_symbols"))
        if st.value(key, True, type=bool)
    ]
    return main_window_class()._and_list(words)


def write_text(path: str, text: str) -> None:
    """``text`` into ``path`` as one step (a .tmp file, then replaced): a helper process
    reading it meanwhile gets the old list or the new one, never an empty one. OSError
    when it could not (the file in use: the caller tries again at its next tick)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def full_stop(text: str) -> str:
    """``text`` ending in one full stop (a line of a result box)."""
    t = str(text).rstrip()
    while t.endswith((".", "\u2026")):
        t = t[:-1].rstrip()
    return t + "."


def unique_names(xs) -> List[str]:
    """``xs`` (project names) each once, in order, told apart without case (a folder name
    spelled two ways is one project on Windows)."""
    out: List[str] = []
    seen = set()
    for x in xs:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out
