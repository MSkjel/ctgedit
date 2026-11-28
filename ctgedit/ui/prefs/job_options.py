"""Long-job timings as settings."""

from __future__ import annotations

from .settings import WHOLE_SPANS, whole

#: the keys this module applies, in the order of their rows
KEYS = (
    "opt/task_report_s",
    "opt/job_poll_ms",
    "opt/worker_idle_s",
    "opt/worker_low_memory_mb",
    "opt/preview_timeout_s",
    "opt/model_wait_s",
    "opt/busy_note_ms",
    "opt/users_note_s",
    "opt/users_read_window_ms",
    "opt/find_pages_per_helper",
    "opt/save_helper_records",
    "opt/save_window_ms",
)


def values() -> dict:
    """{key: the value in effect} as the settings hold them now."""
    return {k: whole(k, WHOLE_SPANS[k]) for k in KEYS}


def poll_ms() -> int:
    """How often a helper process's progress is read (ms), as the settings hold it now."""
    return whole("opt/job_poll_ms", WHOLE_SPANS["opt/job_poll_ms"])


def users_read_window_ms() -> int:
    """Settings > Pages and libraries > Long jobs: the search for what uses an element
    shows its window after this (ms; 0: at once)."""
    return whole("opt/users_read_window_ms", WHOLE_SPANS["opt/users_read_window_ms"])


def save_helper_records() -> int:
    """Settings > Pages and libraries > Long jobs: the save of an element of at least this
    many objects normalises them in a helper process (0: never)."""
    return whole("opt/save_helper_records", WHOLE_SPANS["opt/save_helper_records"])


def save_window_ms() -> int:
    """Settings > Pages and libraries > Long jobs: the save of a large element shows its
    progress window after this (ms; 0: at once)."""
    return whole("opt/save_window_ms", WHOLE_SPANS["opt/save_window_ms"])


def users_note_s() -> int:
    """How long the finished note of a genie update stays in the status bar (s; 0: until
    closed), as the settings hold it now."""
    return whole("opt/users_note_s", WHOLE_SPANS["opt/users_note_s"])


def load() -> dict:
    """Put the settings into effect for the jobs of this process. Returns the values."""
    v = values()
    from ..jobs import genie_jobs, helper_job, tasks

    tasks.LONG_S = float(v["opt/task_report_s"])
    helper_job.POLL_MS = int(v["opt/job_poll_ms"])
    genie_jobs.WarmWorker.IDLE_S = float(v["opt/worker_idle_s"])
    genie_jobs.WarmWorker.LOW_MEMORY_MB = int(v["opt/worker_low_memory_mb"])
    from ..browser import preview

    preview.set_timeout(v["opt/preview_timeout_s"])
    from ..simulate import modeljob

    modeljob.WAIT_S = float(v["opt/model_wait_s"])
    from ..window import files

    files.PageFiles.BUSY_NOTE_MS = int(v["opt/busy_note_ms"])
    from ..dialog import find_dialog

    find_dialog.FindReplaceDialog.PAGES_PER_HELPER = int(v["opt/find_pages_per_helper"])
    return v


def processes_box(key: str, parent=None):
    """The helper-process count box of ``key`` ('opt/find_processes', 'opt/update_processes'),
    as Find, Update Pages and Settings show it: 0 is Automatic. It shows the setting; the
    caller stores it."""
    from PySide6 import QtWidgets

    from .settings import FIND_PROCESSES_RANGE, HELPER_MB, UPDATE_PER_HELPER_RANGE, UPDATE_PROCESSES_RANGE

    if key == "opt/find_processes":
        span = FIND_PROCESSES_RANGE
        tip = "How many helper processes search the closed pages at once. Automatic: half the processors, at most 4."
    else:
        span = UPDATE_PROCESSES_RANGE
        tip = (
            "How many helper processes update the pages at once. Automatic: half the processors, at "
            "most 4, fewer when memory is short. Each gets at least %d pages, so a small run uses fewer."
            % whole("opt/update_pages_per_helper", UPDATE_PER_HELPER_RANGE)
        )
    box = QtWidgets.QSpinBox(parent)
    box.setRange(*span)
    box.setSpecialValueText("Automatic")
    box.setToolTip("%s Each takes about %d MB while it runs." % (tip, HELPER_MB))
    box.setValue(whole(key, span))
    return box
