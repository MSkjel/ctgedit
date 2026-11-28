"""Tools > Pack Libraries and Pack Database."""

from __future__ import annotations

from .open_tabs import all_tabs

from ..main_window_base import (
    QtWidgets,
    contextlib,
    os,
)
from ..messages import warn
from .run_text import full_stop


class PackCommands:
    """Packing the libraries and the project database, in a helper process."""

    # ------------------------------------------------------- pack libraries and the database

    def _pack_libraries_dialog(self) -> None:
        """Tools > Pack Libraries: the project (the page's, or any with no page open) and the
        included projects, then the pack."""
        if self._update_pages_running():
            return
        from ..dialog.pack_dialog import PackLibrariesDialog

        dlg = PackLibrariesDialog(self, getattr(self.doc, "path", "") or "")
        try:
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            dirs = dlg.project_dirs()
        finally:
            dlg.deleteLater()
        if dirs:
            self._pack_libraries(dirs)

    def _pack_database_dialog(self) -> None:
        """Tools > Pack Database: the project and the included projects, then the pack in a
        helper process; the results window when it ends."""
        if self._update_pages_running():
            return
        from ..dialog.pack_dialog import PackDatabaseDialog

        dlg = PackDatabaseDialog(self, getattr(self.doc, "path", "") or "")
        try:
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            dirs = dlg.project_dirs()
        finally:
            dlg.deleteLater()
        if dirs:
            self._pack_database(dirs)

    def _refuse_while_builder_runs(self, title: str, why: str, then: str) -> bool:
        """The Graphics Builder runs (Plant SCADA Studio starts one too): say why ``title``
        waits for it and return True."""
        try:
            from ...backend.graphics_builder import ctdraw_pids

            running = bool(ctdraw_pids())
        except Exception:  # noqa: BLE001  # swallow-ok: no check, the work reports what it meets
            running = False
        if running:
            warn(
                self,
                title,
                "The Graphics Builder is running (Plant SCADA Studio starts one too): %s.\n\n"
                "Close Plant SCADA Studio and the Graphics Builder, then %s." % (why, then),
            )
        return running

    def _pack_database(self, dirs: list) -> bool:
        """Pack the database of the project folders ``dirs`` in a helper process. False when
        it did not start."""
        from ...infra.paths import data_dir
        from ...workers import pack_worker as PW
        from ..jobs.helper_job import HelperJob

        if self._refuse_while_builder_runs(
            "Pack Database",
            "Studio and the Graphics Builder keep the project's tables open",
            "run Pack Database again",
        ):
            return False
        from ..jobs.update_run import UpdatePagesRun

        job = HelperJob(
            self, PW.command, {"format": PW.FORMAT, "dirs": dirs}, data_dir("pack"), label="Pack Database"
        )
        job.finished.connect(lambda res: self._database_packed(job, res))
        UpdatePagesRun.pack_busy = "Pack Database"
        if not job.start():
            UpdatePagesRun.pack_busy = ""
            warn(self, "Pack Database", "The database could not be packed: the helper process did not start.")
            return False
        self._pack_job = job
        return True

    def _database_packed(self, job, res: dict) -> None:
        from ..jobs.update_run import UpdatePagesRun

        if getattr(self, "_pack_job", None) is job:
            self._pack_job = None
        UpdatePagesRun.pack_busy = ""
        self._show_run_results("Pack Database", self._pack_database_lines(res))

    @staticmethod
    def _pack_database_lines(res: dict) -> list:
        """The lines of Pack Database's result window."""
        from ..names import plural

        if res.get("error"):
            return ["The database could not be packed: %s" % full_stop(str(res["error"]))]
        projects = res.get("projects") or []
        tables = sum(len(p["packed"]) for p in projects)
        saved = sum(int(p["saved"]) for p in projects)
        if not tables:
            lines = ["No deleted rows: nothing to pack."]
        else:
            lines = ["%s packed, %.1f MB reclaimed." % (plural(tables, "table"), saved / 1e6)]
        for p in projects:
            for table, n in sorted(p["packed"].items(), key=lambda kv: -kv[1])[:20]:
                lines.append("%s %s: %s removed." % (p["project"], table, plural(n, "deleted row")))
        kept = ["%s %s" % (p["project"], t) for p in projects for t in p["kept"]]
        if kept:
            lines.append("")
            lines.append(
                "Kept until the project is compiled (the change log names their rows): %s." % ", ".join(kept)
            )
        failed = [(p["project"], t, why) for p in projects for t, why in p["failed"]]
        if failed:
            lines.append("")
            lines.append("These tables were left as they were:")
            lines += ["%s %s: %s" % (proj, t, full_stop(why)) for proj, t, why in failed[:20]]
        if res.get("stopped"):
            lines.append("Stopped before every table was packed.")
        return lines

    def _pack_libraries(self, dirs: list) -> None:
        """Tools > Pack Libraries: the libraries of the project folders ``dirs`` (the dialog
        asked)."""
        if not dirs or self._update_pages_running():
            return
        from ...infra import backups, logsetup

        if self._refuse_while_builder_runs(
            "Pack Libraries",
            "it keeps the library elements it has loaded in memory and writes them back into "
            "the libraries, which would undo the pack or damage it",
            "run Pack Libraries again",
        ):
            return
        from ...format.projects import pack_composition_caches

        # the pages open here keep their instances: what they link now (unsaved
        # placements too) and what they linked earlier -- an Undo brings it back
        open_links = set()
        for w, e in all_tabs():
            with contextlib.suppress(Exception):
                open_links |= e["doc"].composite_links()
        unused = {}
        from ..busy import wait_cursor

        with wait_cursor():
            for d in dirs:
                try:
                    found = pack_composition_caches(d, dry_run=True, extra_links=open_links).unused
                except Exception as e:  # noqa: BLE001 -- the caches never stop a pack
                    logsetup.get("pack").warning("composition caches of %s: %s", d, e)
                    continue
                if found:
                    unused[d] = found
        prune = False
        if unused:
            n = sum(len(v) for v in unused.values())
            per = "\n".join(
                "• %s: %d" % (os.path.basename(os.path.normpath(d)), len(v)) for d, v in unused.items()
            )
            from ..messages import choose

            got = choose(
                self,
                "Pack Libraries",
                "%d composite genie instance%s in the composition caches %s not used by "
                "any page or library element of these projects (or of a project that "
                "includes them):\n%s\n\nDelete them? This cannot be undone in the editor "
                "(their files are kept in the pack backup; Tools > Restore Backup puts them "
                "back)." % (n, "" if n == 1 else "s", "is" if n == 1 else "are", per),
                ["Delete Unused", "Keep Them"],
                "Keep Them",
            )
            if got is None:
                return
            prune = got == "Delete Unused"
        folder = backups.new_folder("pack")
        from ...infra.paths import data_dir
        from ...workers import pack_worker as PW
        from ..jobs.helper_job import HelperJob
        from ..jobs.update_run import UpdatePagesRun

        req = {
            "format": PW.FORMAT,
            "mode": "libraries",
            "dirs": list(dirs),
            "backup": folder,
            "prune": [d for d in dirs if prune and d in unused],
            "open_links": [list(x) for x in open_links],
        }
        job = HelperJob(self, PW.command, req, data_dir("pack"), label="Pack Libraries")
        job.finished.connect(lambda res: self._libraries_packed(job, res, folder))
        UpdatePagesRun.pack_busy = "Pack Libraries"
        if not job.start():
            UpdatePagesRun.pack_busy = ""
            warn(
                self, "Pack Libraries", "The libraries could not be packed: the helper process did not start."
            )
            return
        self._pack_job = job

    def _libraries_packed(self, job, res: dict, folder: str) -> None:
        """Pack Libraries' helper ended: the libraries it rewrote are read again, then its
        result (a stop: the error box with the backup of what it packed before)."""
        from ...infra import backups, logsetup
        from ..jobs.update_run import UpdatePagesRun

        if getattr(self, "_pack_job", None) is job:
            self._pack_job = None
        UpdatePagesRun.pack_busy = ""
        packed = list(res.get("packed") or ())
        failures = [tuple(f) for f in res.get("failures") or ()]
        if packed:
            logsetup.files_written("packed libraries (originals in %s)" % folder, packed)
            from ...editing import libraries_changed

            libraries_changed()
            self._after_library_write(*packed)
        why = res.get("stopped_by") or res.get("error")
        if why:
            logsetup.get("pack").error("%s", why)
            from ..messages import error_box, open_folder_action

            error_box(
                self,
                "Pack Libraries",
                str(why),
                next_steps=(
                    "Libraries packed in the project folders before this one "
                    "stay packed; their originals are in the backup %s "
                    "(Tools > Restore Backup puts them back)." % folder,
                )
                if packed
                else (),
                actions=(open_folder_action("Open Backup Folder", folder),)
                if packed and os.path.isdir(folder)
                else (),
            )
            return
        with contextlib.suppress(Exception):
            backups.prune()
        self._show_run_results(
            "Pack Libraries",
            self._pack_result_lines(
                int(res.get("files") or 0),
                int(res.get("saved") or 0),
                int(res.get("pruned") or 0),
                bool(packed),
                folder,
                failures,
            ),
            backup=folder if packed else "",
        )

    @staticmethod
    def _pack_result_lines(files: int, saved: int, pruned: int, packed: bool, folder: str, failures) -> list:
        """The lines of Pack Libraries' result (the results window, _show_run_results): the
        counts, the backup folder with Tools > Restore Backup when files were rewritten, the
        files left as they were."""
        from ..names import plural

        lines = ["%s packed, %s reclaimed." % (plural(files, "library file"), plural(saved, "byte"))]
        if pruned:
            lines.append(
                "%s deleted from the composition caches." % plural(pruned, "unused composite genie instance")
            )
        if packed:
            lines.append(
                "Backup of the files rewritten: %s (Tools > Restore Backup puts them back)." % folder
            )
        if failures:
            lines.append("")
            lines.append("These files were left as they were:")
            lines += ["%s: %s" % (os.path.basename(p), full_stop(why)) for p, why in failures[:20]]
            if len(failures) > 20:
                lines.append("%d more were left. The log lists them." % (len(failures) - 20))
        return lines
