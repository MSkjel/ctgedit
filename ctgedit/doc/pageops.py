"""Page-level edits: the pages.DBF row, page keyboard commands, the page template."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections import OrderedDict

from ..format.cnum import is_digits
from ..editing_base import (  # noqa: F401
    CODEC,
    List,
    Optional,
    PAGE_FIELD_LABELS,
    PAGE_FIELD_LIMITS,
    PAGE_KEY_LABELS,
    PAGE_KEY_LIMITS,
    PageRecordError,
    Tuple,
    UnencodableTextError,
    guard_text,
    os,
    writer,
)


class PageEdits:
    """EditableDocument's page-level edits, the pages.DBF row, page keyboard commands, the page
    template."""

    # -- page-level edits ---------------------------------------------------------------
    def page_properties(self) -> dict:
        """The page properties (attributes list1: Page Properties >
        Environment and CtDraw's own '!*' keys) -- what ``set_page_property``
        writes. The associations (list2) are ``associations()``."""
        at = self.parsed.attributes
        return at.list1.as_dict() if at is not None else {}

    def associations(self) -> List[Tuple[str, str, str, str]]:
        """Page Properties > Associations: attributes list2 (a library
        element's trailer record B) as ``(name, default, value_on_error,
        description)`` rows in stored order."""
        at = self.parsed.attributes
        return at.associations() if at is not None else []

    def export_page_model(self, **kw) -> dict:
        """The page as the Cicode engine reads it: the ctsim-page/0 model of the in-memory
        records (unsaved edits included) -- sim/pagemodel.export_document."""
        from ..sim import pagemodel

        return pagemodel.export_document(self, **kw)

    def set_associations(self, pairs, label: str = "associations") -> bool:
        """Replace the association list. One undo step."""
        from ..format.attributes import association_name_error

        at = self.parsed.attributes
        if at is None:
            return False
        new = [(str(k), str(v)) for k, v in pairs]
        stored = {k for k, _v in at.list2.pairs}
        seen = set()
        for k, v in new:
            err = None if k in stored else association_name_error(k)
            if err:
                raise ValueError("%s (%r)" % (err, k))
            if k.lower() in seen:
                raise ValueError("There are two associations named %r. Association names must be unique." % k)
            seen.add(k.lower())
            guard_text(k, v)
        old = list(at.list2.pairs)
        if new == old:
            return False

        def apply(p):
            at.list2.pairs[:] = p
            at.list2.count = len(p)

        self._apply(label, lambda: apply(new), lambda: apply(old))
        return True

    def set_association(
        self, name: str, default: str = "", value_on_error: str = "", description: str = ""
    ) -> bool:
        """Add an association, or change the one called ``name`` (matched
        ignoring case, kept in place). Undoable; False when nothing changes;
        ValueError for a new name that breaks the naming rules."""
        from ..format.attributes import join_association

        at = self.parsed.attributes
        if at is None:
            return False
        value = join_association(default, value_on_error, description)
        pairs = list(at.list2.pairs)
        for i, (k, _v) in enumerate(pairs):
            if k.lower() == name.lower():
                pairs[i] = (name, value)
                break
        else:
            pairs.append((name, value))
        return self.set_associations(pairs, "association")

    def remove_association(self, name: str) -> bool:
        """Delete the association called ``name`` (ignoring case). Undoable."""
        at = self.parsed.attributes
        if at is None:
            return False
        kept = [(k, v) for k, v in at.list2.pairs if k.lower() != name.lower()]
        if len(kept) == len(at.list2.pairs):
            return False
        return self.set_associations(kept, "remove association")

    def set_page_property(self, key: str, value: str) -> bool:
        """Edit/add a page property (attributes list1). Undoable."""
        guard_text(key, value)
        at = self.parsed.attributes
        if at is None:
            return False
        pairs = list(at.list1.pairs)
        old_pairs = list(pairs)
        for i, (k, _v) in enumerate(pairs):
            if k == key:
                pairs[i] = (k, value)
                break
        else:
            pairs.append((key, value))

        def apply(p):
            at.list1.pairs[:] = p
            at.list1.count = len(p)

        self._apply("page property", lambda: apply(pairs), lambda: apply(old_pairs))
        return True

    def set_page_background(self, colour: int) -> None:
        """Set the page background colour (packed as stored on disk)."""
        at = self.parsed.attributes
        if at is None:
            return
        old = (at.bg_color0, at.bg_color1)
        new = (colour & 0xFFFFFFFF, colour & 0xFFFFFFFF)

        def apply(v):
            at.bg_color0, at.bg_color1 = v
            self.page.appearance.background_colour = v[0]

        self._apply("page background", lambda: apply(new), lambda: apply(old))

    def remove_page_property(self, key: str) -> bool:
        """Delete a page property (attributes list1). Undoable."""
        at = self.parsed.attributes
        if at is None:
            return False
        pairs = list(at.list1.pairs)
        old_pairs = list(pairs)
        kept = [(k, v) for k, v in pairs if k != key]
        if len(kept) == len(pairs):
            return False

        def apply(p):
            at.list1.pairs[:] = p
            at.list1.count = len(p)

        self._apply("remove page property", lambda: apply(kept), lambda: apply(old_pairs))
        return True

    def set_page_size(self, width: int, height: int) -> None:
        """Resize the page view area (attributes block + page-block head). Undoable."""
        from ..format.integrity import PAGE_AREA_MAX, PAGE_EXTENT_MAX

        if (
            int(width) > PAGE_EXTENT_MAX
            or int(height) > PAGE_EXTENT_MAX
            or int(width) * int(height) > PAGE_AREA_MAX
        ):
            raise ValueError(
                "The page can be at most %d pixels wide or high and %d million "
                "pixels in all: the Graphics Builder does not open a bigger one."
                % (PAGE_EXTENT_MAX, PAGE_AREA_MAX // 1000000)
            )
        at = self.parsed.attributes
        pb = self.parsed.page
        old = (
            at.width if at else 0,
            at.height if at else 0,
            pb.page_width if pb else 0,
            pb.page_height if pb else 0,
        )
        new = (int(width), int(height), int(width), int(height))

        def apply(v):
            if at is not None:
                at.width, at.height = v[0], v[1]
            if pb is not None:
                pb.page_width, pb.page_height = v[2], v[3]
            self.page.appearance.width = v[0]
            self.page.appearance.height = v[1]

        self._apply("page size", lambda: apply(new), lambda: apply(old))

    # -- the page's project-database row (pages.DBF) and page keyboard commands --
    # CtDraw keeps the Page Properties fields as '!' page properties in memory
    # (mapped to the pages.DBF columns) and the page's keyboard commands as
    # pgkey records: both reach the project database only when the page is
    # SAVED. The document stages them the same way: an edit is an undo step,
    # save() writes it, and closing without saving leaves the database as it was.

    def _page_record_name(self) -> str:
        return os.path.splitext(os.path.basename(self.path or ""))[0]

    def _page_project(self):
        from ..io.project import Project

        return Project(os.path.dirname(os.path.abspath(self.path or "")))

    def page_record_available(self) -> bool:
        """The page lives in a project folder (it has pages.DBF): its page
        properties and keyboard commands can be stored there."""
        try:
            return bool(self.path) and self._page_project().has_page_table()
        except Exception:  # noqa: BLE001
            return False

    def page_keyboard_available(self) -> bool:
        """The page's project also has the pgkey.DBF its page keyboard
        commands are stored in (every shipped project has one)."""
        if not self.page_record_available():
            return False
        try:
            from ..io import pgregister

            return pgregister._find_file(os.path.dirname(os.path.abspath(self.path)), "pgkey.DBF") is not None
        except Exception:  # noqa: BLE001
            return False

    def page_record_on_disk(self) -> Optional[dict]:
        """The page's pages.DBF row as it is on disk now (None: not
        registered, or no project)."""
        if not self.page_record_available():
            return None
        try:
            return self._page_project().page_record(self._page_record_name())
        except Exception:  # noqa: BLE001
            return None

    def page_record_staged(self) -> dict:
        """The pages.DBF edits the next save writes ({column: value})."""
        return dict(self.__dict__.get("_page_rec_staged") or {})

    def page_record(self) -> Optional[dict]:
        """The page's pages.DBF row AS THE NEXT SAVE WRITES IT: the row on
        disk with the staged edits applied. A page not registered yet shows
        what its registration writes (NAME = TITLE = the page name). None
        when the page is not in a project folder."""
        if not self.page_record_available():
            return None
        disk = self.page_record_on_disk()
        name = self._page_record_name()
        row = dict(disk) if disk is not None else {"NAME": name, "TITLE": name}
        row.update(self.page_record_staged())
        return row

    def page_record_registered(self) -> bool:
        return self.page_record_on_disk() is not None

    def page_record_widths(self) -> dict:
        """{column: width} of the project's pages.DBF ({} = no project)."""
        try:
            return self._page_project().page_field_widths()
        except Exception:  # noqa: BLE001
            return {}

    def page_record_problem(self, column: str, value: str) -> Optional[str]:
        """Why ``value`` can't be stored in pages.DBF ``column`` (None if it can)."""
        col = column.upper()
        value = "" if value is None else str(value)
        what = PAGE_FIELD_LABELS.get(col, col)
        widths = self.page_record_widths()
        width = widths.get(col) or PAGE_FIELD_LIMITS.get(col)
        if widths and col not in widths:
            return "This project's pages.DBF has no %s column (%s), so the %s cannot be stored." % (
                col,
                what,
                what,
            )
        try:
            guard_text(value)
        except UnencodableTextError as e:
            return str(e)
        n = len(value.encode(CODEC, "replace"))
        if width and n > width:
            return "The %s can be at most %d characters long (this one has %d)." % (what, width, n)
        v = value.strip()
        if col == "SCANTIME" and v and not (is_digits(v) and int(v) <= 60000):
            return (
                "The page scan time must be a whole number of milliseconds from 0 to "
                "60000, or empty for the default."
            )
        if col == "IGNOREVQ" and v and v not in ("-1", "0", "1", "2"):
            return "Ignore Quality must be -1 (default), 0, 1 or 2."
        return None

    def set_page_record(self, updates: dict, label: str = "page properties") -> bool:
        """Stage pages.DBF edits ({column: value}) -- written by the next
        save, in one undo step. False when nothing changes; ValueError (with
        the reason) when a value cannot be stored or the page has no project
        folder."""
        if not updates:
            return False
        if not self.page_record_available():
            raise ValueError(
                "%s is not in a project folder (no pages.DBF), so its page "
                "properties cannot be stored." % os.path.basename(self.path or "")
            )
        new = {}
        for k, v in updates.items():
            err = self.page_record_problem(k, v)
            if err:
                raise ValueError(err)
            new[k.upper()] = "" if v is None else str(v)
        cur = self.page_record() or {}
        # pages.DBF keeps no leading / trailing blanks (fixed-width, padded)
        new = {k: v for k, v in new.items() if (cur.get(k) or "").strip() != v.strip()}
        if not new:
            return False
        # the undo puts back the EFFECTIVE values (a save in between writes them)
        old = {k: cur.get(k, "") for k in new}

        def apply(vals):
            st = self.__dict__.setdefault("_page_rec_staged", {})
            st.update(vals)

        self._apply(label, lambda: apply(new), lambda: apply(old))
        return True

    # the page's keyboard commands: pgkey rows with a BLANK AN (Example Summary:
    # LBUTTON_UP -> AlarmAck(0,0);...), in stored order
    def page_keyboard_commands(self) -> List[dict]:
        """The page-wide keyboard commands as the next save writes them."""
        return self.page_keyboard_read()[0]

    def page_keyboard_read(self):
        """(keyboard commands, read error or '')."""
        staged = self.__dict__.get("_page_keys_staged")
        if staged is not None:
            return [dict(r) for r in staged], ""
        if not self.page_record_available():
            return [], ""
        try:
            from ..io import pgregister

            rows = pgregister.page_keyboard_rows(
                os.path.dirname(os.path.abspath(self.path)), self._page_record_name()
            )
        except Exception as e:  # noqa: BLE001 -- the caller is told (the second value)
            from ..infra import logsetup

            logsetup.note_exception("editing.page_keyboard_read", page=self.path)
            return [], str(e) or e.__class__.__name__
        return [{k: v for k, v in r.items() if k.upper() not in ("PAGE", "AN")} for r in rows], ""

    def page_keyboard_problem(self, row: dict) -> Optional[str]:
        """Why a page keyboard command cannot be stored (None = it can): a
        command needs a key sequence; the pgkey widths (KEY_SEQ 32, CMD 254,
        AREA / PRIV 16, MSG_LOG 32 -- CtDraw's limits too) and the code page."""
        seq = (row.get("KEY_SEQ") or "").strip()
        if not seq and any((row.get(k) or "").strip() for k in ("CMD", "MSG_LOG")):
            return "A keyboard command needs a key sequence."
        for k, lim in PAGE_KEY_LIMITS.items():
            v = row.get(k) or ""
            try:
                guard_text(v)
            except UnencodableTextError as e:
                return "Key sequence '%s': %s" % (seq, e)
            n = len(v.encode(CODEC, "replace"))
            if n > lim:
                return "Key sequence '%s': the %s can be at most %d characters long (this one has %d)." % (
                    seq,
                    PAGE_KEY_LABELS[k],
                    lim,
                    n,
                )
        return None

    def set_page_keyboard_commands(self, rows, label: str = "page keyboard commands") -> bool:
        """Stage page-wide keyboard commands for the next save. One undo step."""
        if not self.page_record_available():
            raise ValueError(
                "%s is not in a project folder (no pgkey.DBF), so its keyboard "
                "commands cannot be stored." % os.path.basename(self.path or "")
            )
        new = []
        for r in rows:
            r = {
                str(k).upper(): "" if v is None else str(v)
                for k, v in dict(r).items()
                if str(k).upper() not in ("PAGE", "AN")
            }
            if not any(r.get(k, "").strip() for k in PAGE_KEY_LIMITS):
                continue  # a blank row is not a command
            err = self.page_keyboard_problem(r)
            if err:
                raise ValueError(err)
            new.append(r)
        old, unread = self.page_keyboard_read()
        if unread:
            # (staging a list made without the stored rows would replace them all)
            raise ValueError(
                "The page's keyboard commands cannot be read now, so they cannot be changed: %s" % unread
            )

        def norm(rs):
            return [{k: (r.get(k) or "") for k in PAGE_KEY_LIMITS} for r in rs]

        if norm(new) == norm(old):
            return False
        if not self.page_keyboard_available():
            raise ValueError(
                "Project %s has no pgkey.DBF, the table page keyboard commands "
                "are stored in, so they cannot be stored for this page."
                % os.path.basename(os.path.dirname(os.path.abspath(self.path)))
            )

        def apply(rs):
            self._page_keys_staged = [dict(r) for r in rs]

        self._apply(label, lambda: apply(new), lambda: apply(old))
        return True

    def staged_page_state(self) -> dict:
        """The staged pages.DBF edits + keyboard commands (what a recovery
        copy keeps beside the page bytes); {} when nothing is staged."""
        out = {}
        rec = self.page_record_staged()
        if rec:
            out["page_record"] = rec
        keys = self.__dict__.get("_page_keys_staged")
        if keys is not None:
            out["page_keys"] = [dict(r) for r in keys]
        return out

    def restore_staged_page_state(self, state: dict) -> None:
        """Put back :meth:`staged_page_state` (a restored recovery copy)."""
        state = state or {}
        self._page_rec_staged = {str(k).upper(): str(v) for k, v in (state.get("page_record") or {}).items()}
        keys = state.get("page_keys")
        self._page_keys_staged = None if keys is None else [dict(r) for r in keys]

    def _flush_page_record(self, proj, name: str, *, copy_keys_from=None) -> List[str]:
        """Write staged pages.DBF edits and keyboard commands during save."""
        from ..io import pgregister

        state = {
            "page_record": self.page_record_staged(),
            "page_keys": self.__dict__.get("_page_keys_staged"),
        }
        return pgregister.apply_page_state(proj.path, name, state, copy_keys_from=copy_keys_from, proj=proj)

    # -- page template (the placed .ctt reference) ---------------------------
    def template_reference(self):
        """The page's template placement ``(object_index, SymbolReference)`` for the
        root genie whose body references a ``.ctt`` element, or ``(None, None)``."""
        from ..format.symbols import reference_from_object

        for i, po in enumerate(self.parsed.page.objects):
            if po.type_code != 0x08:  # group/genie/template container records
                continue
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                continue
            if ref is not None and ref.ext == "ctt":
                return i, ref
        return None, None

    def set_template_reference(self, index: int, new_ref: str) -> bool:
        """Point the page's template placement at another template."""
        from ..format.symbols import reference_from_object

        def mutate(po):
            old = bytes(po.body_raw)
            if not writer.set_body_reference(po, new_ref):
                return False
            chk = reference_from_object(po)
            if chk is None or chk.raw != new_ref:
                po.body_raw = old
                writer.refresh_object_bags(po)
                return False
            return True

        return bool(self._body_edit("page template", index, mutate))

    def template_status(self) -> Tuple[str, str]:
        """('none' | 'unresolved' | 'current' | 'stale', template link text):
        whether the page's template prefix matches its .ctt element as it is
        now (format/template_place.is_stale)."""
        from ..format import template_place as TP
        from ..format import genie_place as GP

        roots = TP.template_roots(self.parsed.page.objects)
        if not roots:
            return "none", ""
        link = GP.decode(self.parsed.page.objects[roots[0][0]]).link or roots[0][2].raw
        tpl = TP.resolve_template(self._resolver(), roots[0][2])
        if tpl is None:
            return "unresolved", link
        name = self.page.name if self.page else ""
        return ("stale" if TP.is_stale(self.parsed.page, tpl, link, page_name=name) else "current"), link

    def _apply_page_state(
        self,
        label: str,
        objects,
        link_raw,
        link_count,
        size=None,
        background=None,
        props=None,
        associations=None,
    ) -> None:
        """One undo step over the record order, the link table and optionally
        the page size / background / page properties (list 1) / associations
        (list 2)."""
        pb = self.parsed.page
        at = self.parsed.attributes

        def snap():
            return (
                list(pb.objects),
                pb.link_table_raw,
                pb.link_count,
                (
                    pb.page_width,
                    pb.page_height,
                    at.width if at is not None else 0,
                    at.height if at is not None else 0,
                ),
                (at.bg_color0, at.bg_color1) if at is not None else None,
                list(at.list1.pairs) if at is not None else None,
                list(at.list2.pairs) if at is not None else None,
            )

        old = snap()
        sz = old[3] if size is None else (int(size[0]), int(size[1]), int(size[0]), int(size[1]))
        new = (
            list(objects),
            link_raw,
            link_count,
            sz,
            old[4] if background is None else tuple(background),
            old[5] if props is None else list(props),
            old[6] if associations is None else list(associations),
        )

        def apply(state):
            o, raw, cnt, s, bg, pp, aa = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            pb.page_width, pb.page_height = s[0], s[1]
            if at is not None:
                at.width, at.height = s[2], s[3]
                if bg is not None:
                    at.bg_color0, at.bg_color1 = bg
                if pp is not None:
                    at.list1.pairs[:] = pp
                    at.list1.count = len(pp)
                if aa is not None:
                    at.list2.pairs[:] = aa
                    at.list2.count = len(aa)
            self._rerender_page()

        self._apply(label, lambda: apply(new), lambda: apply(old))

    def update_template(self, force: bool = False, page_properties: Optional[bool] = None) -> int:
        """Update Pages for the page's template."""
        from ..format import template_place as TP

        res = self._resolver()
        if res is None:
            return 0
        if not force and self.template_status()[0] != "stale":
            return 0
        self._use_tag_formats()
        pb = self.parsed.page
        r = TP.recompose(pb.objects, res, page_name=self.page.name if self.page else "")
        if r["objects"] is None:
            return 0
        old = [writer.object_payload(p) for p in pb.objects]
        new = [writer.object_payload(p) for p in r["objects"]]
        raw, cnt = TP.replace_link_entry(
            pb.link_table_raw,
            pb.link_count,
            getattr(pb, "area_code", "    "),
            r["link"],
            r["entry"],
            records=r["objects"],
        )
        changed = sum(1 for a, b in zip(old, new) if a != b) + abs(len(old) - len(new))
        if page_properties is None:
            try:
                from ..io.citect_config import template_update_enabled

                page_properties = template_update_enabled()
            except Exception:  # noqa: BLE001
                page_properties = False
        size = bg = props = None
        if page_properties:
            size, bg, props = self._template_page_state(r["template"])
            at = self.parsed.attributes
            if size == (pb.page_width, pb.page_height):
                size = None
            if at is not None and bg == (at.bg_color0, at.bg_color1):
                bg = None
            if at is not None and props == list(at.list1.pairs):
                props = None
            changed += (size is not None) + (bg is not None) + (props is not None)
        if not changed and (raw, cnt) == (pb.link_table_raw, pb.link_count):
            return 0
        self._apply_page_state(
            "update template", r["objects"], raw, cnt, size=size, background=bg, props=props
        )
        return max(changed, 1)

    def template_inputs(self) -> "OrderedDict[str, str]":
        """The page template's inputs (the parameters its element declares, in its order)
        with this page's values (the template root's value bag), else the declared
        defaults. Empty without a resolvable template."""
        from collections import OrderedDict as _OD
        from ..format import genie_place as GP, template_place as TP

        out = _OD()
        res = self._resolver()
        objs = self.parsed.page.objects
        roots = TP.template_roots(objs) if res is not None else []
        if not roots:
            return out
        a, _b, ref = roots[0]
        t = TP.resolve_template(res, ref)
        if t is None:
            return out
        vals, _ov = TP.root_values(objs[a])
        have = {k.lower(): v for k, v in (vals or [])}
        for k, v in GP.declared_params(t.el).items():
            out[k] = have.get(k.lower(), v)
        return out

    def set_template_inputs(self, values: dict, label: str = "template inputs") -> bool:
        """Set page template inputs the way CtDraw's template dialog does: the values on the
        template root's value bag and the template's copy on the page composed again with
        them (a bag-only edit is what CtDraw throws away on its next save). One undo step;
        False when nothing changed or the page has no resolvable template."""
        from ..format import template_place as TP

        vals = {str(k): str(v) for k, v in (values or {}).items()}
        if not vals:
            return False
        guard_text(*vals.keys(), *vals.values())
        res = self._resolver()
        if res is None:
            return False
        self._use_tag_formats()
        pb = self.parsed.page
        r = TP.recompose(pb.objects, res, page_name=self.page.name if self.page else "", values=vals)
        if r["objects"] is None:
            return False
        old = [writer.object_payload(p) for p in pb.objects]
        new = [writer.object_payload(p) for p in r["objects"]]
        if old == new:
            return False
        raw, cnt = TP.replace_link_entry(
            pb.link_table_raw,
            pb.link_count,
            getattr(pb, "area_code", "    "),
            r["link"],
            r["entry"],
            records=r["objects"],
        )
        self._apply_page_state(label, r["objects"], raw, cnt)
        return True

    def _template_page_state(self, tpl, resize_page: bool = True):
        """(size, background, page properties) the page takes from template
        tpl (CtDraw's template load): the template's page
        size, its background (None: keep the page's), and the page's
        properties with the template's set over them (None: unchanged)."""
        from ..format import template_place as TP

        at = self.parsed.attributes
        size = (tpl.width, tpl.height) if (resize_page and tpl.width and tpl.height) else None
        bg, tprops = TP.template_page_properties(tpl)
        props = None
        if at is not None and tprops:
            pairs = list(at.list1.pairs)
            for k, v in tprops:
                for i, (pk, _pv) in enumerate(pairs):
                    if pk.lower() == k.lower():
                        pairs[i] = (pk, v)
                        break
                else:
                    pairs.append((k, v))
            props = pairs
        return size, bg, props

    def template_element(self) -> Optional[Tuple[str, str]]:
        """(library path, element) of the page's template on its include
        path -- what Edit Template opens -- or None."""
        _i, ref = self.template_reference()
        if ref is None:
            return None
        path = self.library_path(ref.library, ref.element, "ctt")
        return (path, ref.element) if path else None

    def cut_template_link(self) -> int:
        """Edit > Cut Link on the page's template: its records become plain objects."""
        from ..format import template_place as TP

        res = self._resolver()
        if res is None:
            raise ValueError("no include path for this page")
        self._use_tag_formats()
        pb = self.parsed.page
        area = getattr(pb, "area_code", "    ")
        objs, raw, cnt = list(pb.objects), pb.link_table_raw, pb.link_count

        def recomposed():
            r = TP.recompose(objs, res, page_name=self.page.name if self.page else "")
            if r["objects"] is None:
                return objs, raw, cnt
            return (r["objects"],) + TP.replace_link_entry(raw, cnt, area, r["link"], r["entry"])

        stale = self.template_status()[0] == "stale"
        if stale:
            objs, raw, cnt = recomposed()
        name = self.page.name if self.page else ""
        try:
            out = TP.cut_link(objs, raw, cnt, area, res, page_name=name)
        except ValueError:
            if stale:
                raise
            # a current page whose template copy no longer lines up with the
            # element record for record (edited by hand): convert the template
            # as it is now, like the stale case
            objs, raw, cnt = recomposed()
            out = TP.cut_link(objs, raw, cnt, area, res, page_name=name)
        self._apply_page_state("cut template link", out["objects"], out["link_raw"], out["link_count"])
        return out["records"]

    def change_template(self, new_ref: str, *, resize_page: bool = True, carry_params: bool = True) -> bool:
        """Link the page to another template."""
        from ..format import template_place as TP

        res = self._resolver()
        if res is None:
            raise KeyError("no include path for this page")
        self._use_tag_formats()
        pb = self.parsed.page
        r = TP.switch(
            pb.objects, res, new_ref, page_name=self.page.name if self.page else "", carry_params=carry_params
        )
        tpl = r["template"]
        raw, cnt = TP.replace_link_entry(
            pb.link_table_raw, pb.link_count, getattr(pb, "area_code", "    "), r["old_link"], r["entry"]
        )
        size, bg, props = self._template_page_state(tpl, resize_page)
        self._apply_page_state(
            "change template", r["objects"], raw, cnt, size=size, background=bg, props=props
        )
        return True
