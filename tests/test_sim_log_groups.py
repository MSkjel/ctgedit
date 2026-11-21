"""The Outputs log: grouped repeats, filter and search."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.ui.simulate import panels as P  # noqa: E402
from ctgedit.ui.simulate.sources import LogEntry  # noqa: E402
from qtkit import app as _app


def _err(t, an=11, call='TagRead("NoSuch", 0, "")', text="Bad tag (274)", page="FxSim1"):
    return LogEntry(
        t, "error", text, level="error", code=274, call=call, an=an, page=page, source="visible", window=0
    )


def _panel():
    _app()
    p = P.OutputsPanel()
    p.group_box.setChecked(True)
    return p


def test_the_same_error_from_the_same_place_is_one_row_with_a_count():
    p = _panel()
    p.add_entries(
        [
            _err(1.0),
            _err(2.0, call='TagRead("Other", 0, "")'),
            _err(3.5),
            _err(4.0, an=12),
            LogEntry(4.5, "log", "hello"),
        ]
    )
    rows = p.entries()
    assert len(rows) == 3  # AN 11 x3, AN 12, the log line
    g = next(r for r in rows if r.text(P.COL_COUNT) == "3")
    assert g.text(P.COL_TIME) == "3.50 s"  # the last time
    assert g.text(P.COL_MSG) == "Bad tag (274)"
    assert next(r for r in rows if "AN 12" in r.text(P.COL_WHERE)).text(P.COL_COUNT) == ""
    # more of it later: the same row counts on, nothing new is added
    p.add_entries([_err(9.0)])
    assert len(p.entries()) == 3 and g.text(P.COL_COUNT) == "4"
    assert g.text(P.COL_TIME) == "9.00 s"
    # the severity buttons count the lines: 5 errors, 1 info line
    assert p.severity_buttons["error"].text() == "5"
    assert p.severity_buttons["info"].text() == "1"
    assert p.summary.text() == "Showing everything: 6 lines in 3 rows."


def test_a_group_opens_to_its_occurrences_and_the_details_say_how_often():
    p = _panel()
    p.add_entries([_err(1.0), _err(2.0, call='TagRead("Other", 0, "")'), _err(3.0)])
    g = p.entries()[0]
    p.log.setCurrentItem(g)
    d = p.details.toPlainText()
    assert "Happened 3 times, first at 1.00 s, last at 3.00 s." in d
    assert '2x  TagRead("NoSuch", 0, "")' in d and '1x  TagRead("Other", 0, "")' in d
    assert "The latest time:" in d
    g.setExpanded(True)
    assert g.childCount() == 3
    assert [g.child(k).text(P.COL_CALL) for k in range(3)][1] == 'TagRead("Other", 0, "")'
    # an open group shows a new occurrence at once
    p.add_entries([_err(4.0)])
    assert g.childCount() == 4
    # a child is one occurrence: its own details, Show on Page for its object
    p.log.setCurrentItem(g.child(1))
    assert "Happened" not in p.details.toPlainText()
    assert p.selected_entry().call == 'TagRead("Other", 0, "")'
    assert p.show_button.isEnabled()


def test_group_repeats_off_lists_every_line_and_back():
    p = _panel()
    p.add_entries([_err(1.0), _err(2.0), _err(3.0), LogEntry(4.0, "log", "x")])
    p.group_box.setChecked(False)
    assert len(p.entries()) == 4 and all(r.text(P.COL_COUNT) == "" for r in p.entries())
    p.add_entries([_err(5.0)])
    assert len(p.entries()) == 5
    p.group_box.setChecked(True)
    assert len(p.entries()) == 2
    assert {r.text(P.COL_COUNT) for r in p.entries()} == {"4", ""}
    from ctgedit.ui.simulate import options

    assert options.get("log_group") is True


def test_filter_and_search_hide_rows_and_the_summary_says_so():
    p = _panel()
    p.add_entries(
        [
            _err(1.0),
            _err(2.0, an=5, text="Divide by zero (275)"),
            LogEntry(3.0, "log", "Pump started"),
            LogEntry(4.0, "write", "Tank1 = 4"),
        ]
    )
    p.set_severity_shown("info", False)
    shown = [r.text(P.COL_MSG) for r in p.entries() if not r.isHidden()]
    assert sorted(shown) == ["Bad tag (274)", "Divide by zero (275)"]
    p.search.setText("divide")
    p.search.flush()
    assert [r.text(P.COL_MSG) for r in p.entries() if not r.isHidden()] == ["Divide by zero (275)"]
    assert "Showing 1 of 4 rows" in p.summary.text() and "3 lines hidden" in p.summary.text()
    assert "info off" in p.summary.text() and "the search" in p.summary.text()
    p.show_all()
    assert not any(r.isHidden() for r in p.entries())
    assert p.search.text() == ""


def test_rows_sort_by_count_and_by_time():
    from PySide6 import QtCore

    p = _panel()
    p.add_entries(
        [_err(1.0, an=1), _err(2.0, an=2), _err(3.0, an=2), _err(4.0, an=2), _err(5.0, an=3), _err(6.0, an=3)]
    )
    # by time (the default): the group seen last at the bottom
    assert "AN 2" in p.entries()[-1].text(P.COL_WHERE) or "AN 3" in p.entries()[-1].text(P.COL_WHERE)
    p.log.sortByColumn(P.COL_COUNT, QtCore.Qt.DescendingOrder)
    assert [r.text(P.COL_COUNT) for r in p.entries()] == ["3", "2", ""]
    p.add_entries([_err(7.0, an=1), _err(8.0, an=1), _err(9.0, an=1)])
    assert p.entries()[0].text(P.COL_COUNT) == "4"


def test_clear_empties_everything():
    p = _panel()
    p.add_entries([_err(1.0), _err(2.0)])
    p.clear()
    assert not p.entries() and not p.groups() and p.summary.text() == "Nothing logged yet."


def test_every_line_has_a_type_and_a_severity():
    from ctgedit.ui.simulate import ctsim_source as CS
    from ctgedit.ui.simulate.sources import category_of, severity_of

    e = CS.log_entry(1.0, {"level": "info", "text": "TraceMsg: hi", "kind": "message"})
    assert (category_of(e), severity_of(e)) == ("message", "info")
    e = CS.log_entry(1.0, {"level": "warning", "text": "x", "kind": "hardware", "code": 274})
    assert (category_of(e), severity_of(e)) == ("cicode_error", "warning")
    e = CS.log_entry(1.0, {"level": "error", "text": "task 6 PageDisplay halted: y", "kind": "task"})
    assert category_of(e) == "task"
    # a line without a kind: taken from what it says
    e = CS.log_entry(1.0, {"level": "warning", "text": "Hardware error (274) z", "code": 274})
    assert category_of(e) == "cicode_error"
    e = CS.log_entry(1.0, {"level": "info", "text": "reload a.ci: compiled"})
    assert category_of(e) == "reload"
    assert category_of(LogEntry(1, "write", "T = 1")) == "write"
    assert severity_of(LogEntry(1, "not_simulated", "f")) == "warning"
    assert category_of(LogEntry(1, "log", "Startup: Main runs as task 3")) == "startup"


def test_types_turn_off_and_on_and_the_type_column_says_what_it_is():
    p = _panel()
    p.add_entries(
        [
            _err(1.0),
            LogEntry(2.0, "log", "Task 3 started", category="task"),
            LogEntry(3.0, "write", "Tank1 = 4"),
        ]
    )
    assert {r.text(P.COL_KIND) for r in p.entries()} == {"Simulator", "Task", "Tag write"} or "Tag write" in {
        r.text(P.COL_KIND) for r in p.entries()
    }
    p.set_type_shown("task", False)
    assert [r.text(P.COL_MSG) for r in p.visible_entries()].count("Task 3 started") == 0
    assert "1 type off" in p.summary.text() and p.types_button.text() == "Types (1 off)"
    p.show_only_type("write")
    assert [r.text(P.COL_MSG) for r in p.visible_entries()] == ["Tank1 = 4"]
    p._fill_types_menu()
    labels = [a.text() for a in p.types_menu.actions() if a.text()]
    assert "Tag write (1)" in labels
    p.show_all()
    assert len(p.visible_entries()) == 3


def test_a_hidden_message_stays_hidden_counts_and_comes_back():
    p = _panel()
    p.add_entries([_err(1.0), _err(2.0), _err(3.0, an=12)])
    e = p.entries()[0].data(0, P._ENTRY_ROLE)
    assert not p.hidden_button.isVisibleTo(p)
    p.hide_message(e)  # this message from this place
    vis = p.visible_entries()
    assert len(vis) == 1 and "AN 12" in vis[0].text(P.COL_WHERE)
    assert p.hidden_button.text() == "Hidden: 1"
    assert "2 lines hidden (1 hidden message)" in p.summary.text()
    # new lines of it stay hidden; the summary counts them
    p.add_entries([_err(4.0)])
    assert len(p.visible_entries()) == 1 and "3 lines hidden" in p.summary.text()
    # remembered by a new panel (the setting)
    q = _panel()
    q.add_entries([_err(5.0)])
    assert not q.visible_entries() and len(q.hidden_rules()) == 1
    q.unhide(None)
    p.unhide(p.hidden_rules()[0])
    assert len(p.visible_entries()) == 2
    # everywhere: the same message from any place
    p.hide_message(e, everywhere=True)
    assert not p.visible_entries()
    p.unhide(None)


def test_the_filter_takes_vscode_words_and_not_words():
    p = _panel()
    p.add_entries(
        [_err(1.0), _err(2.0, an=5, text="Divide by zero (275)"), LogEntry(3.0, "log", "Pump started")]
    )
    p.search.setText("bad tag, divide")
    p.search.flush()
    assert len(p.visible_entries()) == 2
    p.search.setText("!divide")
    p.search.flush()
    assert sorted(r.text(P.COL_MSG) for r in p.visible_entries()) == ["Bad tag (274)", "Pump started"]
    p.show_all()


def _rows_seen(p) -> list:
    """Everything the log shows, row by row: texts, tips, hidden, selected, open, children,
    the current row and the details."""
    lg = p.log

    def walk(it, depth, out):
        out.append(
            (
                depth,
                tuple(it.text(c) for c in range(lg.columnCount())),
                tuple(it.toolTip(c) for c in range(lg.columnCount())),
                it.isHidden(),
                it.isSelected(),
                it.isExpanded(),
            )
        )
        for k in range(it.childCount()):
            walk(it.child(k), depth + 1, out)

    out: list = []
    for k in range(lg.topLevelItemCount()):
        walk(lg.topLevelItem(k), 0, out)
    cur = lg.currentItem()
    out.append(("current", cur.text(P.COL_TIME) + cur.text(P.COL_MSG) if cur else None))
    out.append(("details", p.details.toPlainText(), p.summary.text()))
    return out


def test_a_batch_places_its_rows_where_the_trees_own_sort_does():
    """A batch with new group rows works out the rows' places itself, with the tree's sorting
    off: the rows, their order (equal times included, a line earlier than its group's last
    one too), the selection, an open group's occurrences and the current row are the tree's
    own."""
    import random
    from PySide6 import QtWidgets

    _app()

    def lines(n, seed):
        rnd = random.Random(seed)
        out = []
        for k in range(n):
            new = k % 3 == 0
            j = k if new else k % 11
            t = 1.0 + (k // 4) * 0.04 - (rnd.choice((0.04, 0.4, 2.0)) if rnd.random() < 0.15 else 0.0)
            out.append(
                LogEntry(
                    t,
                    "error" if j % 4 == 2 else "log",
                    ("new %d" % k) if new else ("again %d" % j),
                    function="F%d" % (j % 5),
                    an=j % 30 + 1,
                    level="error" if j % 7 == 0 else "",
                )
            )
        return out

    seen = []
    old_keep = P.GROUP_KEEP
    P.GROUP_KEEP = 12  # (an open group's oldest occurrences go)
    try:
        for place in (False, True):
            P.OutputsPanel.PLACE_ROWS = place
            p = _panel()
            win = QtWidgets.QMainWindow()
            win.setCentralWidget(p)
            win.show()
            es = lines(900, 7)
            for k in range(0, len(es), 30):
                p.add_entries(es[k : k + 30])
                QtWidgets.QApplication.processEvents()
                if k == 90:
                    p.log.setCurrentItem(p.log.topLevelItem(p.log.topLevelItemCount() // 2))
                    next(r for r in p.entries() if r.text(P.COL_MSG).startswith("again")).setExpanded(True)
                    p.log.topLevelItem(5).setSelected(True)
            seen.append(_rows_seen(p))
            win.close()
    finally:
        P.OutputsPanel.PLACE_ROWS = True
        P.GROUP_KEEP = old_keep
    assert seen[0] == seen[1]
    assert any(r[0] == 1 for r in seen[1] if isinstance(r[0], int))  # an open group


def test_a_new_line_the_filter_hides_comes_hidden_in_both_modes():
    for grouped in (True, False):
        p = _panel()
        p.group_box.setChecked(grouped)
        p.set_severity_shown("info", False)
        p.add_entries([_err(1.0), LogEntry(2.0, "log", "Pump started")])
        shown = [r.text(P.COL_MSG) for r in p.entries() if not r.isHidden()]
        assert shown == ["Bad tag (274)"], (grouped, shown)
