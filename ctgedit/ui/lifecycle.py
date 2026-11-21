"""Delete and rename pages and library elements."""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from PySide6 import QtCore, QtWidgets

PAGE_FILES = "files"  # delete the page and its files (CtDraw: Yes)
PAGE_PROJECT = "project"  # remove it from the project only (CtDraw: No)

#: before a delete / rename: False cancels; else what to run once it succeeded (the open
#: tabs closed), or True for nothing
BeforeHook = Callable[[str, str, str], object]


def _ask_before(before, kind: str, path: str, element: str):
    """(go on, the step for after a success)."""
    if before is None:
        return True, None
    got = before(kind, path, element)
    return bool(got), (got if callable(got) else None)


def _after(step) -> None:
    if step is not None:
        step()


_KIND_NOUN = {"symbol": "symbol", "genie": "genie", "supergenie": "super genie", "template": "template"}
_LIST_MAX = 8


def _warn(parent, title: str, text: str) -> None:
    QtWidgets.QMessageBox.warning(parent, title, text)


def _notice(parent, title: str, text: str) -> None:
    """A NON-blocking notice (the deletion is done; this is a caveat)."""
    box = QtWidgets.QMessageBox(
        QtWidgets.QMessageBox.Information, title, text, QtWidgets.QMessageBox.Ok, parent
    )
    box.setModal(False)
    box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
    box.show()


def _page_stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _project_name(path: str) -> str:
    return os.path.basename(os.path.dirname(os.path.abspath(path)))


def _open_in_builder(project: str, page: str) -> bool:
    try:
        from ..backend.graphics_builder import open_pages

        return any(p.lower() == project.lower() and n.lower() == page.lower() for p, n in open_pages())
    except Exception:  # noqa: BLE001 -- a hint only
        return False


def _touch(catalog, directory: str) -> None:
    try:
        if catalog is None:
            from ..io.catalog import catalog_for

            catalog = catalog_for()
        catalog.touch(directory)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- pages
def ask_delete_page(parent, path: str) -> Optional[str]:
    """CtDraw's page Delete question with explicit buttons: PAGE_FILES,
    PAGE_PROJECT or None (cancel)."""
    name, proj = _page_stem(path), _project_name(path)
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle("Delete Page")
    box.setText("Delete page '%s' from project '%s'?" % (name, proj))
    info = (
        "Delete Page and Files removes %s.ctg, its .ctF and .rdb files and its rows in "
        "the project database.\n\nRemove from Project Only removes the database rows "
        "and the .rdb file, and keeps %s.ctg on disk." % (name, name)
    )
    if name.startswith("!"):
        info += "\n\nThis is a system page (!). Objects that open it at runtime stop working."
    if _open_in_builder(proj, name):
        info += (
            "\n\nIt is open in the Graphics Builder. Close it there without saving, or "
            "the Graphics Builder's next save writes it back."
        )
    info += (
        "\n\nA backup is made first: Tools > Restore Backup puts the page back. "
        "Compile the project afterwards."
    )
    box.setInformativeText(info)
    b_files = box.addButton("Delete Page and Files", QtWidgets.QMessageBox.DestructiveRole)
    b_proj = box.addButton("Remove from Project Only", QtWidgets.QMessageBox.ActionRole)
    b_cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(b_cancel)
    box.exec()
    clicked = box.clickedButton()
    return PAGE_FILES if clicked is b_files else PAGE_PROJECT if clicked is b_proj else None


def delete_page(
    parent, path: str, *, before: Optional[BeforeHook] = None, ask: Optional[Callable] = None, catalog=None
) -> Optional[dict]:
    """Ask, then delete page ``path`` (see :func:`ctgedit.io.project.delete_page`).
    Returns its report, or None when cancelled / refused."""
    choice = (ask or ask_delete_page)(parent, path)
    if choice not in (PAGE_FILES, PAGE_PROJECT):
        return None
    go, after = _ask_before(before, "page", path, "")
    if not go:
        return None
    from ..io.project import delete_page as _delete_page
    from ..io.updatepages import backup_page_for_delete

    pdir = os.path.dirname(os.path.abspath(path))
    name = _page_stem(path)
    try:
        backup = backup_page_for_delete(path)
    except OSError as e:
        # (the project database is busy: another ctgedit process is writing it)
        _warn(parent, "Delete Page", "Page '%s' was not deleted: %s" % (name, e))
        return None
    if backup is None and os.path.exists(path):
        _warn(
            parent,
            "Delete Page",
            "Page '%s' was not deleted: no backup of it could be made (in %s)."
            % (name, os.path.dirname(os.path.dirname(path))),
        )
        return None
    try:
        rep = _delete_page(pdir, name, files=(choice == PAGE_FILES))
        rep["backup"] = backup
    except PermissionError as e:
        _warn(
            parent,
            "Delete Page",
            "Page '%s' could not be deleted: the project database is locked (%s).\n\n"
            "Close the database views in Plant SCADA Studio or wait for the compile to "
            "finish, then try again. Rows already removed stay removed." % (name, e),
        )
        return None
    except (OSError, ValueError) as e:
        _warn(parent, "Delete Page", "Page '%s' could not be deleted: %s" % (name, e))
        return None
    finally:
        _touch(catalog, pdir)
    _after(after)  # (removed from the project: its tabs close now, not before)
    if rep["errors"]:
        _warn(
            parent,
            "Delete Page",
            "'%s' was removed from project '%s', but these files could not be deleted:\n\n%s"
            "\n\nClose any program that has them open, then delete them in Explorer."
            % (name, _project_name(path), "\n".join(rep["errors"])),
        )
    return rep


# --------------------------------------------------------------------------- elements
def _kind(lib_path: str, element: str) -> str:
    from ..format.element import element_kind

    return _KIND_NOUN.get(element_kind(lib_path, element), "element")


def _is_system_library(lib_path: str) -> bool:
    from ..format.projects import IMPLICIT_INCLUDE_PROJECTS

    return _project_name(lib_path).lower() in {n.lower() for n in IMPLICIT_INCLUDE_PROJECTS}


def _ref(lib_path: str, element: str) -> str:
    return "%s.%s" % (os.path.splitext(os.path.basename(lib_path))[0], element)


def element_usage(lib_path: str, element: str, catalog=None) -> List:
    """The page entries that place ``library.element`` ([] when unknown)."""
    try:
        if catalog is None:
            from ..io.catalog import catalog_for

            catalog = catalog_for()
        return catalog.element_usage(lib_path, element)
    except Exception:  # noqa: BLE001 -- the warning is a hint, never a blocker
        return []


_SUPER_NOTE = "A super genie is opened at runtime, so the pages and genies that open it are not listed."


def usage_text(usage: List, lib_path: str = "", element: str = "") -> str:
    """'It is placed on 3 pages: A, B and C.' (the first few named); for a
    super genie, a note that its runtime uses cannot be listed."""
    note = ("\n\n" + _SUPER_NOTE) if lib_path and _kind(lib_path, element) == "super genie" else ""
    if not usage:
        return "No page of its project, or of the projects that include it, places it." + note
    names = ["%s (%s)" % (e.name, e.project) for e in usage]
    shown = ", ".join(names[:_LIST_MAX])
    if len(names) > _LIST_MAX:
        shown += " and %d more" % (len(names) - _LIST_MAX)
    return "It is placed on %d page%s: %s." % (len(usage), "" if len(usage) == 1 else "s", shown) + note


def ask_delete_element(parent, lib_path: str, element: str, usage: List) -> bool:
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning if usage else QtWidgets.QMessageBox.Question)
    box.setWindowTitle("Delete %s" % _kind(lib_path, element).title())
    box.setText(
        "Delete %s '%s' from %s?"
        % (_kind(lib_path, element), _ref(lib_path, element), os.path.basename(lib_path))
    )
    info = usage_text(usage, lib_path, element)
    if usage:
        info += "\n\nThose pages keep what they show now, but their link is broken."
    if _is_system_library(lib_path):
        info += "\n\n%s is in the %s project, which every project includes." % (
            os.path.basename(lib_path),
            _project_name(lib_path),
        )
    info += "\n\nThe element is marked deleted in its library; Tools > Pack Libraries frees its space."
    box.setInformativeText(info)
    if len(usage) > _LIST_MAX:
        box.setDetailedText("\n".join("%s / %s" % (e.project, e.name) for e in usage))
    b_del = box.addButton("Delete", QtWidgets.QMessageBox.DestructiveRole)
    b_cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(b_cancel)
    box.exec()
    return box.clickedButton() is b_del


def _library_written(lib_path: str, catalog) -> str:
    from ..editing import ctdraw_cache_warning, libraries_changed

    libraries_changed()
    _touch(catalog, os.path.dirname(os.path.abspath(lib_path)))
    return ctdraw_cache_warning()


def delete_element(
    parent,
    lib_path: str,
    element: str,
    *,
    before: Optional[BeforeHook] = None,
    ask: Optional[Callable] = None,
    catalog=None,
) -> bool:
    """Warn about the pages using it, ask, then delete ``element`` from its
    library (marked deleted until Pack Libraries). True when deleted."""
    usage = element_usage(lib_path, element, catalog)
    if not (ask or ask_delete_element)(parent, lib_path, element, usage):
        return False
    go, after = _ask_before(before, "element", lib_path, element)
    if not go:
        return False
    from ..format.element import delete_library_element

    try:
        ok = delete_library_element(lib_path, element)
    except OSError as e:
        _warn(
            parent,
            "Delete",
            "%s could not be deleted: %s\n\nIs the library open in another "
            "program? Close it there and try again." % (_ref(lib_path, element), e.strerror or e),
        )
        return False
    if not ok:
        _warn(
            parent,
            "Delete",
            "%s is no longer in %s. It may have been deleted or renamed."
            % (_ref(lib_path, element), os.path.basename(lib_path)),
        )
        return False
    _after(after)
    note = _library_written(lib_path, catalog)
    if note:
        _notice(parent, "Restart the Graphics Builder", note)
    return True


def ask_element_name(parent, lib_path: str, element: str, usage: List) -> Optional[str]:
    """The new name (validated, not yet taken), or None."""
    from ..format.element import LibraryFile, check_element_name

    prompt = "New name for %s %s:" % (_kind(lib_path, element), _ref(lib_path, element))
    if usage:
        prompt += (
            "\n\n%s Those pages keep the old name and lose their link "
            "(rename it back, or re-place it there)." % usage_text(usage)
        )
    if _kind(lib_path, element) == "super genie":
        prompt += "\n\n" + _SUPER_NOTE + " Cicode that opens it by the old name stops working."
    text = element
    while True:
        text, ok = QtWidgets.QInputDialog.getText(
            parent, "Rename %s" % _kind(lib_path, element).title(), prompt, QtWidgets.QLineEdit.Normal, text
        )
        if not ok:
            return None
        new = text.strip()
        if new == element:
            return None
        err = check_element_name(new)
        if not err:
            try:
                other = LibraryFile.open(lib_path).find(new)
            except OSError:
                other = None
            if other is not None and other.name.lower() != element.lower():
                err = "%s already has an element named '%s'." % (os.path.basename(lib_path), new)
        if not err:
            return new
        _warn(parent, "Rename", err)


def rename_element(
    parent,
    lib_path: str,
    element: str,
    *,
    before: Optional[BeforeHook] = None,
    ask: Optional[Callable] = None,
    catalog=None,
) -> Optional[str]:
    """Ask for a new name, then rename ``element`` in its library. Returns the
    name written (a super genie keeps '@SUPER'), or None."""
    usage = element_usage(lib_path, element, catalog)
    new = (ask or ask_element_name)(parent, lib_path, element, usage)
    if not new or new == element:
        return None
    go, after = _ask_before(before, "element", lib_path, element)
    if not go:
        return None
    from ..format.element import rename_library_element

    try:
        written = rename_library_element(lib_path, element, new)
    except (KeyError, ValueError) as e:
        _warn(
            parent,
            "Rename",
            "%s could not be renamed: %s" % (_ref(lib_path, element), e.args[0] if e.args else e),
        )
        return None
    except OSError as e:
        _warn(
            parent,
            "Rename",
            "%s could not be renamed: %s\n\nIs the library open in another "
            "program? Close it there and try again." % (_ref(lib_path, element), e.strerror or e),
        )
        return None
    _after(after)
    note = _library_written(lib_path, catalog)
    if note:
        _notice(parent, "Restart the Graphics Builder", note)
    return written
