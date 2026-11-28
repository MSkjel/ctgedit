"""Style, property, alarm indicator, Find / Replace, Display Value and Fill edits."""

from __future__ import annotations


from typing import TYPE_CHECKING
from ..infra.logsetup import note_exception as _note_exc

if TYPE_CHECKING:  # (names only the annotations use)
    from collections import OrderedDict

from ..format.cnum import c_atoi, is_digits
from ..editing_base import (  # noqa: F401
    List,
    ObjectKind,
    Optional,
    PageObject,
    Sequence,
    Tuple,
    USER_MARKED_KEYS,
    crlf_text,
    factory,
    geom,
    guard_text,
    inputs,
    metadata_problem,
    object_name_rule_problem,
    os,
    unique_object_name,
    writer,
)
from ..editing_base import (  # noqa: F401
    LIBRARY_GENERATION,
    codes,
    find_options,
    name_key,
    rename_header,
)


class PropertyEdits:
    """EditableDocument's style, property, metadata, alarm indicator, special object, Find /
    Replace, Modify AN, Display Value and Fill edits."""

    # -- style / property edits ---------------------------------------------------
    def _subtree_span(self, index: int) -> List[int]:
        """``index`` and its descendants: the contiguous DFS run of the parsed
        record list (child counts = class_flag)."""
        objs = self.parsed.page.objects
        end, pending = index + 1, objs[index].class_flag
        while pending > 0 and end < len(objs):
            pending += objs[end].class_flag - 1
            end += 1
        return list(range(index, end))

    def _after_bag_edit(self, index: int, po: PageObject, key: str) -> None:
        """Keep derived state in step after a bag edit."""
        from ..format import colourstate

        if colourstate.COLOUR_KEY.match(key):
            colourstate.rebuild(po)
        if po.type_code == 0x05 and (
            key in ("TXT_OFF", "TXT_ON", "TXTMODE", "TXT_STR") or key.startswith(("STATE_", "ARRAY_"))
        ):
            writer.rebuild_text_strings(po)
        if po.type_code == 0x1001 and key == "TEXT":
            writer.sync_button_caption(po)
        if po.type_code == 0x1001 and key in ("TEXT", "WIDTH"):
            # an auto-width button (WIDTH absent / 0) follows its caption; removing or
            # zeroing WIDTH re-sizes it at once (as CtDraw's apply does)
            self._refit_button(po)
        up = key.upper()
        if po.type_code == 0x13 and up in ("URL.NONGENIE", "ENABLEPRINT"):
            # the Web Content's string section: the URL the
            # runtime's .ctF keeps once the bags are stripped
            from ..format import webcontent

            webcontent.rebuild_string_section(po)
        if po.type_code == 0x0D and up.startswith("ACTIVEX"):
            # the control's property list (the end of its class data) mirrors its
            # ACTIVEX_ / ACTIVEXEXT_ keys; CtDraw's save rewrites a stale one
            from ..format import olecontrol

            olecontrol.rebuild_list(po)
        if up.startswith("METADATA_"):
            # the runtime's .ctF keeps the dwFlags-0x100 section, not the bags
            writer.rebuild_metadata_section(po)
        if up.startswith("AI_") and po.type_code == 0x08 and po.class_flag:
            # the indicator frame is part of the group's Bounds
            self._refit_alarm_bounds(index, po)
        self._derive_dynamic(index, po)

    def _refit_button(self, po: PageObject) -> bool:
        """Resize an auto-width button to its caption and store the width."""
        changed = geom.refit_button(po)
        if po.type_code == 0x1001 and geom.button_auto_width(po):
            gh = geom.parse_geom_header(po.body_raw)
            if gh is not None:
                l, _t, r, _b = geom.norm_rect(gh.rect1)
                changed = writer.ensure_object_property(po, "WIDTH", str(r - l)) or changed
        return changed

    def _derive_dynamic(self, index: int, po: PageObject) -> None:
        """dwFlags bit 0x10 of ``po`` (and the members inheriting it) as the
        saver derives it -- after a bag edit, and after a colour edit (a
        flashing pair makes the record dynamic)."""
        from ..format import colourstate

        objs, off = self._dynamic_context()
        k = index + off
        if 0 <= k < len(objs) and objs[k] is po:
            colourstate.derive_dynamic_flags(objs, k)
        else:
            colourstate.derive_dynamic_flag(po)
        objs = self.parsed.page.objects
        if 0 <= index < len(objs) and objs[index] is po and inputs.input_children(objs, index):
            po.subtype_size |= 0x10  # an owner of touch / keyboard records stays dynamic

    def _dynamic_context(self):
        """(DFS record list, offset of the parsed objects in it) that the 0x10
        derive walks: every parent a record can inherit from must be in it."""
        return self.parsed.page.objects, 0

    def _genie_root_drops(self, index: int, key: str) -> bool:
        """``key`` cannot be an own property of the placed genie instance
        ROOT ``index``: CtDraw's page load re-composes the instance and drops
        it (genie_place.root_key_kept): only the object name and the alarm
        indicator stay."""
        if isinstance(self, _ed().ElementDocument):
            return False  # element load rules differ (genie_norm)
        objs = self.parsed.page.objects
        po = objs[index]
        if not (po.subtype_size & 0x200 and po.class_flag) or _root_key_kept()(key):
            return False
        sem = self.page.objects if self.page is not None else []
        if index < len(sem) and sem[index] is not None:
            return sem[index].parent is None
        from ..format.placement import forest_parents

        return forest_parents(objs)[index] is None

    def _user_tooltip(self, index: int, key: str, value: str) -> str:
        """Tool tips on genie members are stored as 'text<User>'."""
        if key.upper() not in USER_MARKED_KEYS or not value or value.endswith("<User>"):
            return value
        sem = self.page.objects if self.page is not None else []
        objs = self.parsed.page.objects
        # a genie ancestor: the model's verdict, or a placed-genie record
        # (dwFlags 0x200 -- the ancestors objprops.registry.member_editable_keys
        # opens the member's Access > General for)
        if (
            index < len(sem)
            and sem[index] is not None
            and any(
                a < len(sem)
                and sem[a] is not None
                and (sem[a].is_genie or (a < len(objs) and objs[a].subtype_size & 0x200))
                for a in self.page.ancestors(index)
            )
        ):
            return value + "<User>"
        return value

    def set_property(self, index: int, key: str, value: str) -> bool:
        """Edit an existing property/animation value on an object (the record's
        own value bag e0.b1 when the key is one of its properties). False for a
        key a placed genie root cannot keep (:meth:`_genie_root_drops`)."""
        guard_text(key, value)
        if self._genie_root_drops(index, key):
            return False
        value = self._user_tooltip(index, key, value)

        def mutate(po):
            ok = writer.set_object_property(po, key, value)
            if ok:
                self._after_bag_edit(index, po, key)
            return ok

        return bool(self._body_edit("property", index, mutate))

    def ensure_property(self, index: int, key: str, value: str) -> bool:
        """Set a property, adding the key to the value bag if needed."""
        guard_text(key, value)
        if self._genie_root_drops(index, key):
            return False
        value = self._user_tooltip(index, key, value)

        def mutate(po):
            ok = writer.ensure_object_property(po, key, value)
            if ok:
                self._after_bag_edit(index, po, key)
            return ok

        return bool(self._body_edit("property", index, mutate))

    def remove_property(self, index: int, key: str) -> bool:
        def mutate(po):
            ok = writer.remove_object_property(po, key)
            if ok:
                self._after_bag_edit(index, po, key)
            return ok

        return bool(self._body_edit("remove property", index, mutate))

    def object_name(self, index: int) -> str:
        """The object's name as CtDraw's Access > General shows it: the
        ``__AN_NAME__`` bag value, else the geometry-header name lpstr (genie
        members carry a header name and no bag key)."""
        name = self.object_properties(index).get("__AN_NAME__")
        if name is not None:
            return name
        hdr = geom.parse_geom_header(self.parsed.page.objects[index].body_raw)
        return hdr.name if hdr is not None else ""

    # -- metadata (the Metadata page) --------------------------------------------
    def metadata(self, index: int) -> List[Tuple[str, str]]:
        """The object's metadata, (name, value) in stored order."""
        out = []
        for k, v in self.object_properties(index).items():
            if len(k) > 9 and k.startswith("METADATA_"):
                name, _eq, value = (v or "").lstrip("=").partition("=")
                out.append((name, value))
        return out

    def set_metadata(self, index: int, items: Sequence[Tuple[str, str]]) -> bool:
        """Replace the object's metadata."""
        items = [(str(n), str(v)) for n, v in items]
        msg = metadata_problem(items)
        if msg:
            raise ValueError(msg[1])
        if items == self.metadata(index):
            return False
        if items and self._genie_root_drops(index, "METADATA_0"):
            return False
        guard_text(*[t for pair in items for t in pair])
        old = [k for k in self.object_properties(index) if k.upper().startswith("METADATA_")]

        def mutate(po):
            edits = [(k, None) for k in old]
            if not writer.set_object_properties(po, edits):
                return False
            add = [("METADATA_%d" % n, "%s=%s" % (name, value)) for n, (name, value) in enumerate(items)]
            if add and not writer.set_object_properties(po, add):
                return False
            self._after_bag_edit(index, po, "METADATA_0")
            return True

        return bool(self._body_edit("metadata", index, mutate))

    # -- alarm indicator (the Alarm Indicator page) ------------------------------------
    def alarm_indicator(self, index: int) -> dict:
        """What the Alarm Indicator page shows for group / genie ``index``
        (format.alarmind.page_values: has, expr, eqhier, include, inside,
        width, padding, colour, flag_on, flag_loc)."""
        from ..format import alarmind as AI

        props = {k.upper(): v for k, v in self.object_properties(index).items()}
        return AI.page_values(props)

    def set_alarm_indicator(self, index: int, values: dict) -> bool:
        """Apply the Alarm Indicator page."""
        from ..format import alarmind as AI

        po = self.parsed.page.objects[index]
        if po.type_code != 0x08:
            return False
        cur = self.alarm_indicator(index)
        want = dict(cur)
        want.update(values)
        if want == cur:
            return False
        edits = AI.apply_edits(want)
        guard_text(*[v for _k, v in edits if v])
        # a placed genie ROOT keeps its own indicator in the value bag e0.b1
        # only, never in the template bag e0.b2 (the members that inherit it
        # from their element carry both)
        template = not (po.subtype_size & 0x200)

        def mutate(p):
            if not writer.set_object_properties(p, edits, template=template):
                return False
            self._after_bag_edit(index, p, "AI_ON")
            return True

        return bool(self._body_edit("alarm indicator", index, mutate))

    # -- special objects: Trend, Web Content, Cicode object, ActiveX ------------------
    def _apply_object_edits(self, index: int, label: str, edits, body=None) -> bool:
        """Write ``edits`` = [(key, value or None = remove)] and, when given,
        ``body(po) -> bool`` (a class-section change) as ONE undo step --
        what a property page's Apply does. Unchanged keys are skipped; False
        when nothing changed."""
        props = self.object_properties(index)
        todo = []
        for k, v in edits:
            if v is None or v == "":
                if k in props:
                    todo.append((k, None))
            elif props.get(k) != v:
                todo.append((k, v))
        guard_text(*[v for _k, v in todo if v])
        if not todo and body is None:
            return False
        if isinstance(self, _ed().ElementDocument):
            # an element member: its template bag (set_member_property) + a
            # separate class-section edit, in one step
            changed = False
            with self.batch(label):
                for k, v in todo:
                    changed |= bool(
                        self.remove_property(index, k) if v is None else self.ensure_property(index, k, v)
                    )
                if body is not None:
                    changed |= bool(self._body_edit(label, index, body))
            return changed

        def mutate(po):
            changed = False
            if todo:
                if not writer.set_object_properties(po, todo):
                    return False
                changed = True
            if body is not None and body(po):
                changed = True
            for k, _v in todo:
                self._after_bag_edit(index, po, k)
            return changed

        return bool(self._body_edit(label, index, mutate))

    def trend_properties(self, index: int) -> dict:
        """What the Trend page shows."""
        from ..format import legacy

        po = self.parsed.page.objects[index]
        props = self.object_properties(index)
        info = legacy.trend_info(po)
        pens = []
        for i in range(legacy.TREND_PENS):
            colour = info["pens"][i] if info is not None else None
            pens.append((props.get("PEN%d" % (i + 1), ""), colour))
        if info is not None:
            samples, spacing = info["samples"], info["spacing"]
        else:
            gh = geom.parse_geom_header(po.body_raw)
            l, _t, r, _b = geom.norm_rect(gh.rect1) if gh is not None else (0, 0, 0, 0)
            samples, spacing = max(1, r - l + 1), 1
        return {
            "pens": pens,
            "cluster": props.get("CLUSTER", ""),
            "periodic": (props.get("PERIODIC") or "").strip().upper() == "TRUE",
            "command": props.get("CMD", ""),
            "samples": samples,
            "spacing": spacing,
            "free": info is not None,
        }

    def set_trend_properties(
        self, index: int, *, pens=None, cluster=None, periodic=None, command=None, samples=None, spacing=None
    ) -> bool:
        """Apply the Trend page as one undo step."""
        from ..format import legacy

        po = self.parsed.page.objects[index]
        if po.type_code != legacy.TREND_TC:
            raise ValueError("This object is not a trend.")
        cur = self.trend_properties(index)
        edits = []
        colours = None
        if pens is not None:
            pens = list(pens)[: legacy.TREND_PENS]
            colours = [None] * legacy.TREND_PENS
            for i, row in enumerate(pens):
                tag, colour = row if isinstance(row, (tuple, list)) else (row, None)
                tag = "" if tag is None else str(tag)
                if len(tag) > legacy.MAX_PEN:
                    raise ValueError(
                        "Pen %d is %d characters long; a trend pen takes at most "
                        "%d." % (i + 1, len(tag), legacy.MAX_PEN)
                    )
                edits.append(("PEN%d" % (i + 1), tag or None))
                if colour is not None and tuple(colour) != tuple(cur["pens"][i][1] or ()):
                    colours[i] = (int(colour[0]), int(colour[1]))
            if not any(c is not None for c in colours):
                colours = None
            if colours is not None and not cur["free"]:
                raise ValueError(
                    "This trend comes from a library copy: its pen colours cannot be changed here."
                )
        if periodic is not None:
            edits.append(("PERIODIC", "TRUE" if periodic else "FALSE"))
        if cluster is not None:
            cluster = str(cluster).strip()
            if len(cluster) > 16:
                raise ValueError("The cluster name '%s' is too long: at most 16 characters." % cluster)
            if cluster.lower() != (cur["cluster"] or "").lower():
                edits.append(("CLUSTER", cluster or None))
        if command is not None:
            if len(command) > 64:
                raise ValueError(
                    "The trend command is %d characters long; at most 64 "
                    "(call a Cicode function for more)." % len(command)
                )
            edits.append(("CMD", command or None))
        resize = None
        if samples is not None or spacing is not None:
            n = cur["samples"] if samples is None else int(samples)
            sp = cur["spacing"] if spacing is None else int(spacing)
            if not 1 <= n <= legacy.MAX_SAMPLES:
                raise ValueError("The number of samples must be 1 to %d." % legacy.MAX_SAMPLES)
            if not 1 <= sp <= legacy.MAX_SPACING:
                raise ValueError("The pixels per sample must be 1 to %d." % legacy.MAX_SPACING)
            if (n, sp) != (cur["samples"], cur["spacing"]):
                if not cur["free"]:
                    raise ValueError("This trend comes from a library copy: its size comes from the library.")
                # the right edge stays: the left edge must still be a page
                # coordinate (i16), or the header cannot store it
                gh = geom.parse_geom_header(po.body_raw)
                right = geom.norm_rect(gh.rect1)[2] if gh is not None else 0
                if right - sp * (n - 1) < -32768:
                    raise ValueError(
                        "%d samples of %d pixels make the trend %d pixels wide, more than "
                        "the page's coordinates allow left of x = %d. Use fewer samples or "
                        "fewer pixels per sample." % (n, sp, sp * (n - 1) + 1, right)
                    )
                resize = (n, sp)

        body = None
        if colours is not None or resize is not None:

            def body(p):
                changed = False
                if colours is not None:
                    changed |= legacy.set_trend_pen_colours(p, colours)
                if resize is not None:
                    changed |= legacy.set_trend_samples(p, *resize) is not None
                return changed

        return self._apply_object_edits(index, "trend properties", edits, body)

    def web_content(self, index: int) -> dict:
        """The Web Content page: ``url`` (URL.NonGenie) and
        ``enable_print`` (ENABLEPRINT atoi != 0)."""
        from ..format import webcontent as W

        props = self.object_properties(index)
        return {"url": props.get(W.URL_KEY, ""), "enable_print": c_atoi(props.get(W.PRINT_KEY, "")) != 0}

    def set_web_content(self, index: int, *, url=None, enable_print=None) -> bool:
        """Apply the Web Content page."""
        from ..format import webcontent as W

        po = self.parsed.page.objects[index]
        if po.type_code != W.WEB_TC:
            raise ValueError("This object is not a Web Content object.")
        edits = []
        if url is not None:
            why = W.url_problem(url)
            if why:
                raise ValueError(why)
            edits.append((W.URL_KEY, url or None))
        if enable_print is not None:
            edits.append((W.PRINT_KEY, "1" if enable_print else None))
        return self._apply_object_edits(index, "web content", edits)

    #: the Cicode object's 'Display as' default (an empty SYMBOL
    #: shows the 'cicode.default' library symbol, else the f(x) class icon)
    CICODE_DEFAULT_SYMBOL = "cicode.default"

    def cicode_object(self, index: int) -> dict:
        """The Cicode page: command (CMD)
        and ``symbol`` (SYMBOL, '' = the default display)."""
        props = self.object_properties(index)
        sym = props.get("SYMBOL", "") or ""
        # (CtDraw's load shows the stored text as it is, 'cicode.default' for an empty
        # key: a stored '<default>' stays '<default>')
        if sym.strip().lower() == self.CICODE_DEFAULT_SYMBOL:
            sym = ""
        return {"command": props.get("CMD", ""), "symbol": sym}

    def set_cicode_object(self, index: int, *, command=None, symbol=None) -> bool:
        """Apply the Cicode page."""
        from ..format import legacy

        po = self.parsed.page.objects[index]
        if po.type_code != 0x1009:
            raise ValueError("This object is not a Cicode object.")
        edits = []
        if command is not None:
            if len(command) > 254:
                raise ValueError(
                    "The command is %d characters long; a Cicode object takes "
                    "at most 254 (call a Cicode function for more)." % len(command)
                )
            edits.append(("CMD", command or None))
        extent = None
        if symbol is not None:
            symbol = str(symbol).strip()
            if symbol.lower() == self.CICODE_DEFAULT_SYMBOL:
                symbol = ""  # (CtDraw removes the key for that text only)
            if symbol and symbol.lower() != "<default>":
                parts = symbol.split(".")
                if len(parts) != 2 or not all(parts):
                    raise ValueError("'%s' is not a library symbol name (library.symbol)." % symbol)
            edits.append(("SYMBOL", symbol or None))
            if legacy.class_tail(po) is not None:
                extent = legacy.RECT2_DELTAS[0x1009]
                if symbol and symbol.lower() != "<default>":
                    from ..format import placement

                    res = self._resolver()
                    info = placement.element_info(res, parts[0], parts[1], "ctl") if res else None
                    if info is not None:
                        (ex, ey), n = info["e"], info["N"]
                        extent = (int(n[0] - ex), int(n[1] - ey), int(n[2] - ex), int(n[3] - ey))
                    else:
                        # the symbol does not resolve here (a page opened outside its
                        # project has no include chain): its drawn extent stays as stored
                        # rather than becoming the f(x) icon's
                        extent = None
        body = None
        if extent is not None:
            gh = geom.parse_geom_header(po.body_raw)
            if gh is not None and tuple(a - b for a, b in zip(gh.rect2, tuple(gh.anchor) * 2)) != tuple(
                extent
            ):

                def body(p):
                    legacy.set_point_extent(p, extent)
                    return True

        return self._apply_object_edits(index, "cicode object", edits, body)

    def activex_associations(self, index: int) -> List[Tuple[str, str, str]]:
        """The ActiveX control's tag associations as its Tag Association page
        reads them: (property, tag, event) of every
        'ACTIVEX_<property>' = 'tag|event' key ('' event = on the property's
        change notification), in stored order."""
        out = []
        for k, v in self.object_properties(index).items():
            if k.startswith("ACTIVEX_"):
                tag, _bar, event = (v or "").partition("|")
                out.append((k[len("ACTIVEX_") :], tag, event))
        return out

    def control_state(self, index: int):
        """(clsid, saved state, (width, height)) of an embedded control, or None. The state is
        the control's OLE compound file; the size is its native rect's."""
        from ..format import olecontrol
        from ..format.placement import native_rect
        from ..io.pgregister import ole_clsid

        po = self.parsed.page.objects[index]
        state = olecontrol.storage(po)
        if state is None:
            return None
        r = native_rect(po) or (0, 0, 1, 1)
        return ole_clsid(po.body_raw), state, (max(1, abs(r[2] - r[0])), max(1, abs(r[3] - r[1])))

    def set_control_state(self, index: int, state: bytes) -> bool:
        """Put ``state`` (the control's new saved state, an OLE compound file) in its record,
        keeping the rest of the record. ONE undo step; False when it is the same."""
        from ..format import olecontrol

        po = self.parsed.page.objects[index]
        if po.type_code != 0x0D:
            raise ValueError("This object is not an ActiveX control.")
        return bool(
            self._body_edit("control properties", index, lambda p: olecontrol.replace_storage(p, state))
        )

    def set_activex_associations(self, index: int, rows) -> bool:
        """The Tag Association page's Apply: every ACTIVEX_ key
        removed, then 'ACTIVEX_<property>' = 'tag|event' for each (property,
        tag, event) row with a tag, in order. ONE undo step."""
        po = self.parsed.page.objects[index]
        if po.type_code != 0x0D:
            raise ValueError("This object is not an ActiveX control.")
        want = []
        for prop, tag, event in rows:
            prop, tag, event = (str(prop or "").strip(), str(tag or "").strip(), str(event or "").strip())
            if not prop or not tag:
                continue
            value = "%s|%s" % (tag, event)
            if len(value) > 160:
                raise ValueError(
                    "The association of '%s' is too long: at most 160 "
                    "characters for the tag and its event." % prop
                )
            want.append(("ACTIVEX_" + prop, value))
        have = [("ACTIVEX_" + p, "%s|%s" % (t, e)) for p, t, e in self.activex_associations(index)]
        if want == have:
            return False
        edits = [(k, None) for k, _v in have] + want
        guard_text(*[v for _k, v in want])
        if isinstance(self, _ed().ElementDocument):
            # key by key through the template bag: remove only what is no
            # longer associated (a remove + re-add of the same value would
            # be skipped as unchanged after the remove)
            keep = {k for k, _v in want}
            return self._apply_object_edits(
                index, "tag association", [(k, None) for k, _v in have if k not in keep] + want
            )

        def mutate(p):
            if not writer.set_object_properties(p, edits):
                return False
            self._after_bag_edit(index, p, "ACTIVEX_")
            return True

        return bool(self._body_edit("tag association", index, mutate))

    def _stored_name(self, index: int) -> str:
        """:meth:`object_name` from the records alone (the heuristic bags +
        the header name; no strict bag walk) -- the namespace checks read it
        for every sibling."""
        po = self.parsed.page.objects[index]
        for bag in po.bags:
            for k, v in bag.pairs:
                if k.upper() == "__AN_NAME__":
                    return v
        hdr = geom.parse_geom_header(po.body_raw)
        return hdr.name if hdr is not None else ""

    def name_siblings(self, index: int) -> List[int]:
        """The objects whose names must differ from ``index``'s."""
        page = self.page
        objs = self.parsed.page.objects
        if page is None or not 0 <= index < len(page.objects):
            return []
        parent = page.objects[index].parent
        if parent is not None and objs[parent].type_code == 0x08:
            me = geom.get_handle(objs[index])
            return [j for j in self._direct_children(parent) if j != index and geom.get_handle(objs[j]) != me]
        return [r for r in page.roots() if r != index]

    def name_conflict(self, index: int, name: str) -> Optional[int]:
        """The object in ``index``'s namespace that already uses ``name``
        (compared without ASCII case, like CtDraw), or None."""
        low = (name or "").strip().lower()
        if not low:
            return None
        for j in self.name_siblings(index):
            if self._stored_name(j).lower() == low:
                return j
        return None

    def object_name_problem(self, index: int, name: str) -> str:
        """Why ``name`` cannot be the name of object ``index`` ('' = it can):
        CtDraw's name rule (:func:`object_name_rule_problem`) and its
        uniqueness check."""
        msg = object_name_rule_problem(name)
        if msg:
            return msg
        j = self.name_conflict(index, name)
        if j is None:
            return ""
        other = self.page.objects[j]
        where = "in the same group" if self.page.objects[index].parent is not None else "on this page"
        return (
            "The name '%s' is already used by the %s (AN %s) %s. Object names must be "
            "unique %s: enter a different name."
            % (name, other.kind.value.replace("_", " "), other.an if other.an >= 0 else "?", where, where)
        )

    def qualified_name(self, index: int) -> str:
        """The object's full name as the runtime looks it up (help 1172255):
        the names of the groups / genies it belongs to, outermost first, and
        its own, joined by '.' ('Genie1.Text4'); unnamed containers add
        nothing. '' for an object without a name."""
        own = self._stored_name(index)
        if not own or self.page is None:
            return own
        parts = [own]
        for a in self.page.ancestors(index):
            n = self._stored_name(a)
            if n:
                parts.append(n)
        return ".".join(reversed(parts))

    def find_objects_by_name(self, query: str) -> List[int]:
        """Objects matching a (qualified) name, in page order (Tools > Goto
        Object by name): 'Text4' = every object named Text4; 'Genie1.Text4' =
        the Text4 whose named containers end with Genie1. Without case."""
        parts = [p.strip().lower() for p in (query or "").split(".")]
        if not parts or not all(parts) or self.page is None:
            return []
        out = []
        for i in range(len(self.page.objects)):
            if self._stored_name(i).lower() != parts[-1]:
                continue
            chain = [p.lower() for p in self.qualified_name(i).split(".")]
            if chain[-len(parts) :] == parts:
                out.append(i)
        return out

    # -- Edit > Find / Replace (ctgedit.features.find_replace) -------------------------------
    def find_text(self, text: str, **options) -> list:
        """Every field of the page that contains ``text``, case-insensitively
        (find_replace.find_hits; ``options`` = the FindOptions fields: scopes,
        match, inside_genies, inside_templates, template_editable)."""
        from ..features import find_replace as FR

        return FR.find_hits(self, FR.FindOptions(text, **find_options(options)))

    def replace_text(self, text: str, replacement: str, hits=None, **options):
        """Replace ``text`` by ``replacement`` in ``hits`` (default: every
        field :meth:`find_text` finds) as ONE undo step; a placed genie
        member's field changes its genie's PARAMETER (one re-composition per
        genie), never the member. A find_replace.ReplaceReport."""
        from ..features import find_replace as FR

        opts = FR.FindOptions(text, **find_options(options))
        if hits is None:
            hits = FR.find_hits(self, opts)
        return FR.replace_hits(self, hits, opts, replacement)

    def animation_point_count(self) -> int:
        """The page's animation points: its distinct ANs (a touch / keyboard
        command shares its owner's). Help 1172254's rule of thumb: keep the number of
        objects (and so ANs) under 3000."""
        seen = set()
        for po in self.parsed.page.objects:
            h = geom.get_handle(po)
            if h is not None:
                seen.add(h)
        return len(seen)

    # -- Modify AN (Settings > Modify AN field; CtDraw's ModifyAN option) -------
    #: the ANs an object can be given: CtDraw's Goto Object finds AN 1..31999
    #: and a paste keeps a handle only below that (_renumber)
    AN_MIN, AN_MAX = 1, 31999

    def an_change_problem(self, index: int, an=None) -> str:
        """Why object ``index`` can't take AN ``an`` ('' if it can)."""
        objs = self.parsed.page.objects if self.parsed and self.parsed.page else []
        if not 0 <= index < len(objs):
            return "There is no such object on this page."
        po = objs[index]
        if po.type_code in inputs.INPUT_TCS:
            return (
                "A touch or keyboard command has the AN of the object it belongs to: "
                "change that object's AN instead."
            )
        old_h = geom.get_handle(po)
        if old_h is None or old_h > geom.MAX_HANDLE:
            return "This object has no AN of its own."
        sem = self.page.objects if self.page is not None else []
        if index < len(sem) and sem[index].raw.get("_from_template"):
            return (
                "This object belongs to the page template, which sets its AN. Edit the template to change it."
            )
        if po.subtype_size & 0x200:
            return (
                "The AN of a placed genie cannot be changed (the Graphics Builder does "
                "not allow it either): its members were composed with it."
            )
        par = inputs.parents(objs)
        a = par[index]
        while a is not None:
            anc = objs[a]
            if anc.subtype_size & 0x200:
                return "This object is part of a genie, which sets its AN. Change the genie instead."
            if anc.type_code == 0x08 and anc.subtype_size & 0x1:
                return (
                    "This object is part of a linked symbol, which the library sets up "
                    "again when the page is opened. Change the symbol instead."
                )
            a = par[a]
        if an is None:
            return ""
        text = str(an).strip()
        if not is_digits(text):
            return "The AN must be a whole number from %d to %d." % (self.AN_MIN, self.AN_MAX)
        n = int(text)
        if not self.AN_MIN <= n <= self.AN_MAX:
            return "The AN must be a number from %d to %d (%d is outside that range)." % (
                self.AN_MIN,
                self.AN_MAX,
                n,
            )
        new_h = n - 1
        if new_h == old_h:
            return ""
        own = {index, *self.input_children(index)}
        for j, other in enumerate(objs):
            if j in own or geom.get_handle(other) != new_h:
                continue
            k = par[j] if other.type_code in inputs.INPUT_TCS and par[j] is not None else j
            what = "another object"
            if k < len(sem):
                o = sem[k]
                what = "the %s at %d, %d" % (
                    o.kind.value.replace("_", " "),
                    int(o.geometry.x),
                    int(o.geometry.y),
                )
                if o.raw.get("_from_template"):
                    what += " (part of the page template)"
            return (
                "AN %d is already used by %s. Two objects of a page cannot share an "
                "AN: choose another number." % (n, what)
            )
        return ""

    def set_object_an(self, index: int, an) -> bool:
        """Give an object a new AN."""
        problem = self.an_change_problem(index, an)
        if problem:
            raise ValueError(problem)
        new_h = int(str(an).strip()) - 1
        objs = self.parsed.page.objects
        if geom.get_handle(objs[index]) == new_h:
            return False
        targets = [index] + self.input_children(index)
        return self._multi_body_edit("change AN", targets, lambda po: geom.set_handle(po, new_h))

    def set_object_name(self, index: int, name: str) -> bool:
        """Rename an object."""
        guard_text(name)
        problem = self.object_name_problem(index, name)
        if problem:
            raise ValueError(problem)
        # an object named by the legacy NAME key (the SA AlarmList / TabBar
        # objects, NAME == the header name) keeps that form
        key = name_key(self.parsed.page.objects[index])[0] or "__AN_NAME__"
        with self.batch("object name"):
            if name:
                if not self.ensure_property(index, key, name):
                    return False
            elif key in self.object_properties(index):
                if not self.remove_property(index, key):
                    return False
            hdr = geom.parse_geom_header(self.parsed.page.objects[index].body_raw)
            if hdr is not None and hdr.name != name:
                self._body_edit("object name", index, lambda po: rename_header(po, name))
        return True

    def _unique_pasted_names(self, records: Sequence[PageObject]) -> None:
        """Rename pasted top-level objects whose names clash ('Name_1', 'Name_2', ...)."""
        taken = set()
        if self.page is not None:
            taken = {self._stored_name(r).lower() for r in self.page.roots()}
        taken.discard("")
        pending = 0
        for po in records:
            if pending == 0:
                key, old = name_key(po)
                if old:
                    new = unique_object_name(old, lambda c: c.lower() in taken)
                    if new != old:
                        if new:
                            writer.set_object_properties(po, [(key, new)])
                        else:
                            writer.remove_object_property(po, key)
                        hdr = geom.parse_geom_header(po.body_raw)
                        if hdr is not None and hdr.name.lower() == old.lower():
                            rename_header(po, new)
                    if new:
                        taken.add(new.lower())
            else:
                pending -= 1
            pending += po.class_flag

    def _in_genie_context(self, index: int) -> bool:
        """``index`` is (inside) a genie instance or a linked symbol, where
        bag keys are genie parameters rather than the object's own settings."""
        objs = self.parsed.page.objects
        if objs[index].subtype_size & 0x200:
            return True
        sem = self.page.objects if self.page is not None else []
        if index < len(sem) and sem[index].is_genie:
            return True
        try:
            anc = self.page.ancestors(index)
        except Exception:  # noqa: BLE001
            _note_exc("properties.PropertyEdits._in_genie_context", index=index)
            anc = []
        return any(a < len(objs) and (objs[a].subtype_size & 0x201) for a in anc)

    def set_line_width(self, index: int, width: int) -> bool:
        """Set the pen width byte in the 54-byte style block (1..32)."""
        return bool(
            self._body_edit(
                "line width",
                index,
                lambda po: geom.set_colour_block_flag(po, geom.FLAG_LINE_WIDTH, max(1, min(32, int(width)))),
            )
        )

    def set_line_style(self, index: int, style: int) -> bool:
        """Set the pen style byte (GDI PS_*: 0 solid, 1 dash, 2 dot,
        3 dash-dot, 4 dash-dot-dot)."""
        return bool(
            self._body_edit(
                "line style",
                index,
                lambda po: geom.set_colour_block_flag(po, geom.FLAG_LINE_STYLE, max(0, min(4, int(style)))),
            )
        )

    def set_filled(self, index: int, filled: bool) -> bool:
        """Set the fill-style byte (0 hollow / 1 solid)."""
        return bool(
            self._body_edit(
                "fill style",
                index,
                lambda po: geom.set_colour_block_flag(po, geom.FLAG_FILL_STYLE, 1 if filled else 0),
            )
        )

    def set_corner_radius(self, index: int, radius: int) -> bool:
        """Rounded-rectangle corner radius (0..32). Undoable."""
        return bool(self._body_edit("corner radius", index, lambda po: geom.set_corner_radius(po, radius)))

    def set_ellipse(
        self,
        index: int,
        *,
        style: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> bool:
        """Ellipse variant: style 0 ellipse / 1 pie / 2 arc + angles (deg)."""

        def mutate(po):
            ok = False
            if style is not None:
                cb = geom.find_colour_block(po)
                if cb is not None:
                    a = (cb.flags[3] & 0xF0) | (max(0, min(2, int(style))))
                    ok = geom.set_colour_block_flag(po, geom.FLAG_ATTRIBS, a) or ok
            if start is not None or end is not None:
                tail = geom.parse_shape_tail(po)
                if tail is not None and tail["kind"] == "ellipse":
                    ok = (
                        geom.set_ellipse_angles(
                            po, tail["start"] if start is None else start, tail["end"] if end is None else end
                        )
                        or ok
                    )
            return ok

        return bool(self._body_edit("ellipse", index, mutate))

    def set_gradient(
        self, index: int, mode: int, colour: Optional[int] = None, off: Optional[int] = None
    ) -> bool:
        """Gradient fill (mode 0..8 + optional second colour; ``off`` = its
        flashing half -- a flashing gradient makes the record dynamic for a
        rect / ellipse / polyline: colourstate.gradient_flashing).
        Undoable."""

        def mutate(po):
            ok = geom.set_gradient(po, mode, colour, off)
            if ok:
                self._derive_dynamic(index, po)
            return ok

        return bool(self._body_edit("gradient", index, mutate))

    def set_polygon_open(self, index: int, open_: bool) -> bool:
        """Polygon open (polyline) vs closed. Undoable."""

        def mutate(po):
            cb = geom.find_colour_block(po)
            if cb is None:
                return False
            a = (cb.flags[3] & ~0x01) | (1 if open_ else 0)
            return geom.set_colour_block_flag(po, geom.FLAG_ATTRIBS, a)

        return bool(self._body_edit("polygon style", index, mutate))

    def set_rectangle_style(self, index: int, style: int) -> bool:
        """Rect border style: 0 none / 1 border / 2 extra line / 3 both."""

        def mutate(po):
            cb = geom.find_colour_block(po)
            if cb is None:
                return False
            a = (cb.flags[3] & 0x0F) | ((max(0, min(3, int(style)))) << 4)
            return geom.set_colour_block_flag(po, geom.FLAG_ATTRIBS, a)

        return bool(self._body_edit("rectangle style", index, mutate))

    def set_effect(self, index: int, effect: int, depth: Optional[int] = None) -> bool:
        """Set the 3D effect and depth."""

        def mutate(po):
            cb = geom.find_colour_block(po)
            if cb is None:
                return False
            ok = geom.set_colour_block_flag(
                po, geom.FLAG_EFFECTS, (cb.flags[0] & 0xF0) | (max(0, min(4, int(effect))) & 0x0F)
            )
            if depth is not None:
                d = -1 if int(depth) < 0 else max(0, min(32, int(depth)))
                ok = geom.set_colour_block_flag(po, geom.FLAG_EFFECT_DEPTH, d & 0xFF) or ok
            if ok:
                self._derive_dynamic(index, po)
            return ok

        return bool(self._body_edit("3d effect", index, mutate))

    def set_colour_slot(
        self, index: int, slot: int, on: int, off: Optional[int] = None, label: str = "colour"
    ) -> bool:
        """Write one style-block colour pair."""
        from ..format import colourstate

        def mutate(po):
            ok = colourstate.set_slot(po, slot, on, off)
            if ok:
                self._derive_dynamic(index, po)
            return ok

        return bool(self._body_edit(label, index, mutate))

    def set_pipe_colour(self, index: int, on: int, off: Optional[int] = None) -> bool:
        """A pipe's colour as CtDraw's colour picker sets it:
        highlight = the pair, lowlight = its halves' bytes >> 1, fill = line =
        the pair (colourstate.set_pipe_colour); a flashing pair makes the
        pipe dynamic. False for a pipe without its own style block."""
        from ..format import colourstate

        def mutate(po):
            ok = colourstate.set_pipe_colour(po, on, off)
            if ok:
                self._derive_dynamic(index, po)
            return ok

        return bool(self._body_edit("pipe colour", index, mutate))

    def set_foreground(self, index: int, on: int, off: Optional[int] = None) -> bool:
        """A text's or button's caption colour ('Foreground'): a text's fill
        slot (the load copies it to the line slot), a button's
        font-block text pair (CtDraw draws the caption in it)."""
        po = self.parsed.page.objects[index]
        if po.type_code == 0x1001:
            off = on if off is None else off

            def mutate(p):
                return geom.set_button_font(p, text=(on & 0xFFFFFF, off & 0xFFFFFF))

            return bool(self._body_edit("text colour", index, mutate))
        if po.type_code != 0x05:
            # another caption class (a V3/V4 number / string): its static colour channel
            return self.set_colour(index, on & 0xFFFFFF)
        return self.set_colour_slot(
            index, geom.SLOT_FILL, on & 0xFFFFFF, None if off is None else off & 0xFFFFFF, label="text colour"
        )

    def set_text_charset(self, index: int, charset: int) -> bool:
        """A text's or button's Script (Windows character set: 0 Western,
        204 Cyrillic, 161 Greek ...). The text is re-measured (_body_edit),
        an auto-width button re-sized."""
        po = self.parsed.page.objects[index]

        def mutate(p):
            if p.type_code == 0x1001:
                ok = geom.set_button_font(p, charset=charset)
                if ok:
                    self._refit_button(p)
                return ok
            return geom.set_text_charset(p, charset)

        if po.type_code not in (0x05, 0x1001):
            return False
        return bool(self._body_edit("script", index, mutate))

    #: CtDraw's button face defaults with Custom Fill off: the
    #: XP bitmap's up / down greys, else COLOR_BTNFACE for both
    _XP_FILL_UP, _XP_FILL_DOWN = 0xF4F4F0, 0xE2E2D9

    @staticmethod
    def button_default_fill(xp: bool):
        """(up, down) face colours CtDraw writes when Custom Fill is off."""
        if xp:
            return _ed().EditableDocument._XP_FILL_UP, _ed().EditableDocument._XP_FILL_DOWN
        from ..render import buttons as B

        q = B.sys_colour(B.COLOR_BTNFACE)
        c = (q.red() << 16) | (q.green() << 8) | q.blue()
        return c, c

    def button_face(self, index: int) -> Optional[dict]:
        """A button's Appearance > General state."""
        po = self.parsed.page.objects[index]
        if po.type_code != 0x1001:
            return None
        props = self.object_properties(index)
        mode = (props.get("VIS_MODE") or "").strip().upper()
        text = props.get("TEXT") or ""
        out = {
            "mode": mode,
            "xp": mode == "XP_STYLE",
            "caption": "",
            "symbol": "",
            "custom_fill": False,
            "up": None,
            "down": None,
        }
        if mode in ("", "NORMAL", "XP_STYLE"):
            if text.startswith("#"):
                out["type"], out["symbol"] = "symbol", text[1:]
            else:
                out["type"], out["caption"] = "text", text
        else:
            out["type"] = "target"
        cb = geom.find_colour_block(po)
        if cb is not None:
            out["custom_fill"] = bool(cb.flags[geom.FLAG_ATTRIBS] & 0x04)
            out["up"] = tuple(cb.slots[geom.SLOT_FILL])
            out["down"] = tuple(cb.slots[geom.SLOT_BACK])
        return out

    def set_button_face(
        self,
        index: int,
        *,
        type: str,
        caption: str = "",
        symbol: str = "",
        mode: str = "TARGET",
        xp: bool = True,
        custom_fill: Optional[bool] = None,
        up=None,
        down=None,
    ) -> bool:
        """Apply a button's Appearance > General page as one undo step."""
        type = (type or "text").lower()
        if type not in ("text", "symbol", "target"):
            raise ValueError("button type must be 'text', 'symbol' or 'target'")
        po = self.parsed.page.objects[index]
        if po.type_code != 0x1001:
            return False
        cur = self.button_face(index) or {}
        props = self.object_properties(index)
        if type == "target":
            vis = (mode or "TARGET").strip().upper()
            if vis not in ("BORDER_3D", "BORDER", "TARGET"):
                raise ValueError("a target button's mode is BORDER_3D, BORDER or TARGET")
            text = None
        else:
            vis = "XP_STYLE" if xp else "NORMAL"
            text = caption if type == "text" else "#" + symbol.strip()
            guard_text(text)
        changed = False
        with self.batch("button"):
            if (props.get("VIS_MODE") or "") != vis:
                changed |= bool(self.ensure_property(index, "VIS_MODE", vis))
            if text is None:
                if "TEXT" in props:
                    changed |= bool(self.remove_property(index, "TEXT"))
            elif crlf_text(text) != (props.get("TEXT") or ""):
                changed |= bool(self.set_text(index, text))
            if custom_fill is not None and type != "target":
                was = bool(cur.get("custom_fill"))
                if not custom_fill:
                    up, down = [(c, c) for c in self.button_default_fill(xp)]
                up = tuple(up) if up is not None else cur.get("up")
                down = tuple(down) if down is not None else cur.get("down")
                if custom_fill != was or (custom_fill and (up != cur.get("up") or down != cur.get("down"))):
                    changed |= bool(self._set_button_fill(index, custom_fill, up, down))
        return changed

    def _set_button_fill(self, index: int, custom: bool, up, down) -> bool:
        from ..format import colourstate

        def mutate(po):
            cb = geom.find_colour_block(po)
            if cb is None:
                return False
            a = cb.flags[geom.FLAG_ATTRIBS]
            geom.set_colour_block_flag(po, geom.FLAG_ATTRIBS, (a | 0x04) if custom else (a & ~0x04))
            if up is not None:
                colourstate.set_slot(po, geom.SLOT_FILL, up[0], up[1])
            if down is not None:
                colourstate.set_slot(po, geom.SLOT_BACK, down[0], down[1])
            self._derive_dynamic(index, po)
            return True

        return bool(self._body_edit("button fill", index, mutate))

    def set_font(
        self,
        index: int,
        *,
        face: Optional[str] = None,
        size: Optional[int] = None,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        underline: Optional[bool] = None,
        strikeout: Optional[bool] = None,
        justify: Optional[str] = None,
    ) -> bool:
        """Set a text's or button's font."""
        guard_text(face)

        def bits(a):
            if justify is not None:
                a = (a & ~0x0F) | {"left": 0, "right": 1, "centre": 2}.get(justify, 0)
            for bit, val in ((0x10, bold), (0x20, italic), (0x40, underline), (0x80, strikeout)):
                if val is not None:
                    a = (a | bit) if val else (a & ~bit)
            return a

        def mutate_button(po):
            f = geom.parse_button_font(po)
            if f is None:
                return False
            before = bytes(po.body_raw)
            a = bits(f["attribs"])
            geom.set_button_font(
                po,
                face=face if face is not None and face != f["face"] else None,
                size=size if size is not None and size != f["size"] else None,
                attribs=a if a != f["attribs"] else None,
            )
            self._refit_button(po)
            return bytes(po.body_raw) != before

        if self.parsed.page.objects[index].type_code == 0x1001:
            return bool(self._body_edit("font", index, mutate_button))

        def mutate(po):
            changed = False
            flags_change = any(v is not None for v in (bold, italic, underline, strikeout, justify))
            if flags_change:
                cb = geom.find_colour_block(po)
                if cb is not None:
                    a = bits(cb.flags[3])
                    if a != cb.flags[3]:
                        changed |= geom.set_colour_block_flag(po, geom.FLAG_ATTRIBS, a)
            if size is not None:
                changed |= geom.set_text_font_size(po, size)
            if face is not None:
                changed |= geom.set_text_face(po, face)
            return changed

        return bool(self._body_edit("font", index, mutate))

    def set_text_effect(self, index: int, effect: int) -> bool:
        """Set the 3D text effect (0 Normal, 1 Raised, 2 Lowered, 3 Shadowed,
        4 Embossed -- the style block's 'effects' low nibble)."""

        def mutate(po):
            cb = geom.find_colour_block(po)
            if cb is None:
                return False
            new = (cb.flags[0] & 0xF0) | (max(0, min(4, int(effect))) & 0x0F)
            return geom.set_colour_block_flag(po, geom.FLAG_EFFECTS, new)

        return bool(self._body_edit("text effect", index, mutate))

    def set_colour(self, index: int, colour: int, *, line: bool = False, outline_only: bool = False) -> bool:
        """Set an object's static colour (0x00RRGGBB) via its native channel."""
        return bool(
            self._body_edit(
                "colour",
                index,
                lambda po: factory.set_object_colour(po, colour, line=line, outline_only=outline_only),
            )
        )

    # -- appearance as a whole (the format painter, named styles) ----------------------------
    #: the Style fields an appearance carries (Copy Appearance / a named style)
    APPEARANCE_FIELDS = (
        "line_width",
        "line_style",
        "filled",
        "line_colour",
        "fill_colour",
        "gradient_mode",
        "gradient_colour",
        "corner_radius",
        "rectangle_style",
        "font",
        "font_size",
        "bold",
        "italic",
        "underline",
        "strikeout",
        "justify",
        "text_colour",
    )

    def appearance_of(self, index: int) -> dict:
        """The look of object ``index`` as a dict (APPEARANCE_FIELDS; a colour as 0x00RRGGBB,
        the justify as its name), for :meth:`apply_appearance` on other objects."""
        st = self.page.objects[index].style
        out = {}
        for k in self.APPEARANCE_FIELDS:
            v = getattr(st, k, None)
            if hasattr(v, "name"):
                v = v.name.lower()
            out[k] = v
        return out

    def apply_appearance(self, indices, app: dict, label: str = "paste appearance") -> int:
        """Give the objects ``indices`` the look ``app`` (from :meth:`appearance_of`, or a named
        style): every field the object can take, through the same setters the Properties
        panel uses, as ONE undo step. Returns how many objects took something."""
        app = dict(app or {})
        taken = 0
        with self.batch(label):
            for i in indices:
                # (the records, not the page model: an object created in the same batch,
                # a drawing tool's new object with its style, is derived at the batch's end)
                if not 0 <= i < len(self.parsed.page.objects):
                    continue
                got = False
                for key, fn in (
                    ("line_width", lambda v: self.set_line_width(i, int(v))),
                    ("line_style", lambda v: self.set_line_style(i, int(v))),
                    ("filled", lambda v: self.set_filled(i, bool(v))),
                    ("corner_radius", lambda v: self.set_corner_radius(i, int(v))),
                    ("rectangle_style", lambda v: self.set_rectangle_style(i, int(v))),
                    ("fill_colour", lambda v: self.set_colour(i, int(v))),
                    ("line_colour", lambda v: self.set_colour(i, int(v), line=True)),
                    ("text_colour", lambda v: self.set_colour(i, int(v))),
                ):
                    v = app.get(key)
                    if v is None:
                        continue
                    try:
                        got = bool(fn(v)) or got
                    except Exception:  # noqa: BLE001  # swallow-ok: a field the object cannot take
                        pass
                # a pipe's own lowlight (the new-pipe style, ui/editor/draw_styles; never in
                # appearance_of): after its colour, which derives the lowlight from the
                # highlight
                if app.get("lolight_colour") is not None and self.parsed.page.objects[i].type_code == 0x0B:
                    try:
                        got = (
                            bool(
                                self.set_colour_slot(
                                    i,
                                    geom.SLOT_LOLIGHT,
                                    int(app["lolight_colour"]) & 0xFFFFFF,
                                    label="lowlight colour",
                                )
                            )
                            or got
                        )
                    except Exception:  # noqa: BLE001  # swallow-ok: a pipe without a style of its own
                        pass
                if app.get("gradient_mode") is not None:
                    try:
                        got = (
                            bool(self.set_gradient(i, int(app["gradient_mode"]), app.get("gradient_colour")))
                            or got
                        )
                    except Exception:  # noqa: BLE001  # swallow-ok: not a shape
                        pass
                if app.get("font") or app.get("font_size"):
                    try:
                        got = (
                            bool(
                                self.set_font(
                                    i,
                                    face=app.get("font") or None,
                                    size=int(app["font_size"]) if app.get("font_size") else None,
                                    bold=app.get("bold"),
                                    italic=app.get("italic"),
                                    underline=app.get("underline"),
                                    strikeout=app.get("strikeout"),
                                    justify=app.get("justify") or None,
                                )
                            )
                            or got
                        )
                    except Exception:  # noqa: BLE001  # swallow-ok: not a text
                        pass
                taken += 1 if got else 0
        return taken

    def set_text(self, index: int, text: str) -> bool:
        """Set a text caption (Text body lpstrs) or button caption (TEXT bag).
        Line breaks are stored CRLF (:func:`crlf_text`); the extent is
        re-measured (``_body_edit``)."""
        guard_text(text)
        text = crlf_text(text)

        def mutate(po):
            if po.type_code == 0x05:
                return writer.set_object_text(po, text)
            ok = writer.ensure_object_property(po, "TEXT", text)
            if ok:
                writer.sync_button_caption(po)
                self._refit_button(po)  # an auto-width button follows its caption
            return ok

        return bool(self._body_edit("text", index, mutate))

    # -- Appearance > Display Value (format.dspval) -------------------------------
    def text_caption(self, index: int) -> Optional[str]:
        """A text's caption as CtDraw's loader takes it -- the tail lpstr
        -- verbatim; None when the record has no text tail."""
        tail = geom.parse_text_tail(self.parsed.page.objects[index])
        return tail["text"] if tail is not None else None

    def text_display(self, index: int):
        """What the object sheet's Display Value page shows for text
        ``index`` (a :class:`~ctgedit.format.dspval.TextDisplay`)."""
        from ..format import dspval

        return dspval.read(self.object_properties(index), self.text_caption(index))

    def apply_text_display(self, index: int, before, after) -> Optional[bool]:
        """Write the Display Value page's change ``before`` -> ``after`` (two
        TextDisplay) the way CtDraw's Apply of the page does
        (dspval.apply_edits), as ONE undo step. None when nothing changed,
        False when the record could not store it."""
        from ..format import dspval

        edits, caption = dspval.apply_edits(
            self.object_properties(index), self.text_caption(index), before, after
        )
        if not edits and caption is None:
            return None
        return self.set_text_display(index, edits, caption)

    def _text_display_per_key(self, index: int) -> bool:
        """The Display Value writes go key by key through ensure_property /
        remove_property (a genie-element TEMPLATE record: ElementDocument)."""
        return False

    def set_text_display(self, index: int, edits, caption: Optional[str] = None) -> bool:
        """Write a text's Display Value keys and caption as one undo step."""
        edits = [(k, v if v else None) for k, v in edits]
        guard_text(caption, *[v for _k, v in edits])
        if not edits and caption is None:
            return False
        if self._text_display_per_key(index):
            ok = True
            with self.batch("display value"):
                have = {k.lower() for k in self.object_properties(index)}
                for k, v in edits:
                    if v is None:
                        if k.lower() in have:
                            ok = bool(self.remove_property(index, k)) and ok
                    else:
                        ok = bool(self.ensure_property(index, k, v)) and ok
                if caption is not None and "TXT_OFF" not in dict(edits):
                    ok = bool(self.set_text(index, caption)) and ok
            return ok

        def mutate(po):
            if edits and not writer.set_object_properties(po, edits):
                return False
            if caption is not None:
                writer.set_text_caption(po, caption)
            self._after_bag_edit(index, po, "TXTMODE")
            return True

        return bool(self._body_edit("display value", index, mutate))

    # -- Fill > Color / Fill > Level (format.fillcolour) ------------------------------
    @staticmethod
    def fill_static_slot(po: PageObject) -> Optional[int]:
        """The style slot of the static colour the Fill > Color page shows."""
        from ..format import colourstate

        if not po.subtype_size & 0x2:
            return None
        L = colourstate.layout(po)
        if L is None or "style_at" not in L:
            return None
        if po.type_code == colourstate.PIPE_TC:
            return geom.SLOT_HILIGHT
        if po.type_code == colourstate.TEXT_TC:
            return geom.SLOT_FILL
        return geom.SLOT_FILL if po.body_raw[L["style_at"] + 2] else geom.SLOT_LINE

    def fill_colour(self, index: int):
        """What the object sheet's Fill > Color page shows for ``index`` (a
        :class:`~ctgedit.format.fillcolour.FillColour`)."""
        from ..format import colourstate, fillcolour

        po = self.parsed.page.objects[index]
        L = colourstate.layout(po)
        slot = self.fill_static_slot(po)
        static = arr = sta = None
        if L is not None:
            b = po.body_raw
            static = (
                colourstate.state0_source(b, L["style_at"])
                if slot is not None
                else colourstate.pair_at(b, L["state0_at"])
            )
            if "arr_at" in L:
                arr = [colourstate.pair_at(b, L["arr_at"] + 8 * k) for k in range(L["arr_n"])]
            if "sta_at" in L:
                sta = [colourstate.pair_at(b, L["sta_at"] + 8 * k) for k in range(L["sta_n"])]
        return fillcolour.read(
            self.object_properties(index),
            static,
            static_editable=slot is not None,
            palette=self.colour_palette(),
            binary_array=arr,
            binary_states=sta,
        )

    def apply_fill_colour(self, index: int, before, after) -> Optional[bool]:
        """Write the Fill > Color page's change ``before`` -> ``after`` (two
        FillColour) the way CtDraw's Apply of the page does
        (fillcolour.apply_edits), as ONE undo step. None when nothing
        changed, False when the record could not store it."""
        from ..format import fillcolour

        edits, static = fillcolour.apply_edits(self.object_properties(index), before, after)
        if static is not None:
            po = self.parsed.page.objects[index]
            slot = self.fill_static_slot(po)
            cur = self.fill_colour(index).static
            if slot is None or cur == static:
                static = None  # (the Appearance page already wrote it)
        if not edits and static is None:
            return None
        return self.set_fill_keys(index, edits, static=static, label="fill colour")

    def set_fill_keys(self, index: int, edits, static=None, label: str = "fill") -> bool:
        """Write fill animation keys and the static colour as one edit."""
        from ..format import colourstate

        edits = [(k, v if v else None) for k, v in edits]
        guard_text(*[v for _k, v in edits])
        if not edits and static is None:
            return False
        if any(v is not None and self._genie_root_drops(index, k) for k, v in edits):
            return False  # a placed genie root: CtDraw's page load drops them
        po = self.parsed.page.objects[index]
        slot = self.fill_static_slot(po) if static is not None else None
        if self._text_display_per_key(index):
            # a genie-element TEMPLATE record: CtDraw's genie editor way
            ok = True
            with self.batch(label):
                have = {k.lower() for k in self.object_properties(index)}
                for k, v in edits:
                    if v is None:
                        if k.lower() in have:
                            ok = bool(self.remove_property(index, k)) and ok
                    else:
                        ok = bool(self.ensure_property(index, k, v)) and ok
                if slot is not None:
                    ok = bool(self.set_colour_slot(index, slot, *static, label=label)) and ok
            return ok

        def mutate(p):
            if edits and not writer.set_object_properties(p, edits):
                return False
            if slot is not None and not colourstate.set_slot(p, slot, *static):
                return False
            self._after_bag_edit(index, p, "COL_MAXON")
            return True

        return bool(self._body_edit(label, index, mutate))

    def object_properties(self, index: int) -> dict[str, str]:
        po = self.parsed.page.objects[index]
        props = po.raw_properties()
        # the heuristic bag scanner cannot frame a value bag whose size-word
        # overshoot runs past the body end (members of linked symbols and
        # templates: overshoot 26..37), so a key stored there would look lost:
        # add what the strict value bag e0.b1 holds, never overriding a value
        vb = writer.value_bag(po)
        if vb:
            have = {k.lower() for k in props}
            for k, v in vb:
                if k.lower() not in have:
                    props[k] = v
        return props

    # Touch 0x1010 / Keyboard 0x100B carry CMD/ACTION input bags with
    # substitution-style trailers; they are input definitions, not genie
    # parameters, so the composite parameter union skips them.
    _BEHAVIOUR_TCS = frozenset({codes.TC.TOUCH, codes.TC.KEYBOARD})

    def genie_bindings(self, index: int) -> dict[str, str]:
        """A placed genie's editable parameter bindings."""
        po = self.parsed.page.objects[index]
        own = writer.genie_bindings(po)
        if own:
            return own
        merged: dict[str, str] = {}
        for j in self.subtree(index):
            if j == index:
                continue
            mpo = self.parsed.page.objects[j]
            if mpo.type_code in self._BEHAVIOUR_TCS:
                continue
            for k, v in writer.genie_bindings(mpo).items():
                merged.setdefault(k, v)
        return merged

    def _resolver(self):
        """A SymbolResolver for this document's page (lazy, cached). None when
        symbol resolution is disabled (``CTG_NO_SYMBOLS=1``)."""
        if getattr(self, "_sym_resolver_gen", None) != LIBRARY_GENERATION[0]:
            # a library element was saved / created since: its SymbolLibrary
            # objects (cached per resolver) and the placement link info are stale
            self._sym_resolver = "unset"
            self.__dict__.pop("_link_cache", None)
            self._sym_resolver_gen = LIBRARY_GENERATION[0]
        if getattr(self, "_sym_resolver", "unset") == "unset":
            from ..format.symbols import SymbolResolver

            self._sym_resolver = SymbolResolver.for_page_env(self.path)
        return self._sym_resolver

    def genie_declared_params(self, index: int) -> "Optional[OrderedDict[str, str]]":
        """The parameters the genie's element declares, name -> default, in order."""
        from ..format import genie_place as GP

        try:
            from ..format.symbols import reference_from_object

            po = self.parsed.page.objects[index]
            ref = reference_from_object(po)
            resolver = self._resolver()
            resolved = resolver.resolve(ref) if (resolver and ref) else None
        except Exception:  # noqa: BLE001
            _note_exc("properties.PropertyEdits.genie_declared_params", index=index)
            resolved = None
        if resolved is None:
            return None
        path = resolved.library_path
        try:
            # (fileio.file_version: a second save of one size within one clock tick counts;
            # inside symbols.held_libraries the version the hold read)
            from ..format.symbols import library_version

            key = (os.path.normcase(path), ref.element.lower(), library_version(path))
        except OSError:
            key = None
        cache = self.__dict__.setdefault("_declared_cache", {})
        if key is not None and key in cache:
            return cache[key]
        # (a batch run asks it once an element and include chain: infra/runmemo; the
        # callers only read it)
        from ..infra import runmemo

        run = runmemo.table("properties.declared_params") if key is not None else None
        rkey = key + (tuple(getattr(resolver, "search_paths", ()) or ()),) if run is not None else None
        from ..format.symbols import lookups_made, note_lookup

        if run is not None and rkey in run:
            self._use_tag_formats()  # (as the first ask of the page sets them)
            decl, looks = run[rkey]
            # (the library lookups the run's first ask made count for this read too: Find's
            # index of the closed pages keeps what each page's read depended on)
            for sp, lk in looks:
                lib, _dot, ext = lk.rpartition(".")
                note_lookup(sp, lib, ext)
            cache[key] = decl
            return decl
        # the page's tag formats, as the genie dialog's OK loads the element
        # with them (_recompose_genie): an element the process-wide memo holds
        # (genie_place.load_element) is valid for this page's formats
        self._use_tag_formats()
        with lookups_made() as looks:
            try:
                # = declared_params(load_element(...)): from the memoised element,
                # else from the declarations alone (genie_place.declared_params_of:
                # no re-composition of the nested genies)
                el = GP.memoised_element(path, ref.element, resolver=resolver)
                decl = (
                    GP.declared_params(el)
                    if el is not None
                    else GP.declared_params_of(path, ref.element, resolver)
                )
            except Exception:  # noqa: BLE001
                _note_exc("properties.PropertyEdits.genie_declared_params")
                decl = None
        if key is not None:
            cache[key] = decl
            if run is not None:
                run[rkey] = (decl, tuple(looks))
        return decl

    def genie_param_uses(self, index: int):
        """Where each parameter of the placed genie goes in its element
        (features/genie_uses.ParamUses by lower-case name); None when its element is
        not found."""
        from ..features import genie_uses as GU

        try:
            from ..format.symbols import reference_from_object

            ref = reference_from_object(self.parsed.page.objects[index])
            resolver = self._resolver()
            resolved = resolver.resolve(ref) if (resolver and ref) else None
            if resolved is None:
                return None
            return GU.element_uses(
                resolved.library_path, ref.element, list(self.genie_declared_params(index) or ())
            )
        except Exception:  # noqa: BLE001
            from ..infra import logsetup

            logsetup.note_exception("properties.genie_param_uses")
            return None

    def _instance_bindings(self, index: int) -> "Optional[OrderedDict[str, str]]":
        """A CLASSIC genie instance's own bindings = its root's level-N value
        bag (strict decode, what CtDraw re-composes the instance from), minus
        the implicit AN. None for anything else (a composite genie's
        parameters live on its members)."""
        from collections import OrderedDict
        from ..format import genie_place as GP

        po = self.parsed.page.objects[index]
        if not po.class_flag or (po.subtype_size & 0x201) != 0x201:
            return None
        sem = self.page.objects if self.page is not None else []
        if index < len(sem) and sem[index].kind is ObjectKind.COMPOSITE_GENIE:
            return None
        try:
            r = GP.decode(po)
        except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
            return None
        if not r.entries:
            return OrderedDict()
        return OrderedDict(
            (k, v) for k, v in own_value_bag(r).pairs if k.upper() != "AN" and not k.startswith("!")
        )

    def genie_all_params(self, index: int) -> "OrderedDict[str, str]":
        """The genie dialog's rows: declared parameters with the instance's values."""
        from collections import OrderedDict

        own = self._instance_bindings(index)
        inst = own if own is not None else self.genie_bindings(index)
        decl = self.genie_declared_params(index)
        if decl is None:
            return OrderedDict(inst)
        low = {k.lower(): v for k, v in inst.items()}
        return OrderedDict((name, low.get(name.lower(), default)) for name, default in decl.items())

    def genie_values(self, index: int) -> "List[Tuple[str, str]]":
        """(parameter, value) of what genie ``index`` passes its element as the Graphics
        Builder composes the page: an outermost placed genie's dialog rows
        (genie_all_params); a genie copy nested in one, its templates filled from the level
        above, from the outermost genie down (what the objects inside it run: the values the
        copy stores are those its element was composed with alone); a copy without templates
        (a composite genie's content), its stored values. [] for an object that is no placed
        genie or copy. Kept while the document is the same."""
        return [
            (k, v) for k, v in self._genie_value_bag(index) if k.upper() != "AN" and not k.startswith("!")
        ]

    def genie_inputs(self, index: int) -> "List[Tuple[str, str, tuple]]":
        """(parameter, value, uses) of each input of genie ``index``: genie_values, then any
        parameter its objects name that the genie gives no value; ``uses`` the fields of its
        objects the input reaches (features/genie_uses.InputUse), () for one nothing uses.
        [] for an object that is no placed genie or copy. Kept while the document is the same."""
        from ..features import genie_uses as GU

        values = self.genie_values(index)
        if not values and not self._genie_value_bag(index):
            return []
        memo = self.__dict__["_genie_values"]  # (genie_values made it for this state)
        key = ("inputs", index)
        got = memo[1].get(key)
        if got is None:
            uses = GU.page_uses(self.parsed.page.objects, self.page, index)
            rows = [(k, v, tuple(uses.pop(k.lower(), ("", ()))[1])) for k, v in values]
            rows += [(nm, "", tuple(us)) for nm, us in uses.values()]
            got = memo[1][key] = tuple(rows)
        return list(got)

    def _genie_value_bag(self, index: int) -> "Tuple[Tuple[str, str], ...]":
        """genie_values with the genie's own AN (and the editor's '!' keys)."""
        from ..format import genie_place as GP

        state = (
            getattr(self, "derive_serial", 0),
            getattr(self, "touch_serial", 0),
            getattr(self, "edit_serial", 0),
            LIBRARY_GENERATION[0],
        )
        memo = self.__dict__.get("_genie_values")
        if memo is None or memo[0] != state:
            memo = self.__dict__["_genie_values"] = (state, {})
        got = memo[1].get(index)
        if got is not None:
            return got
        objs = self.parsed.page.objects
        out: "List[Tuple[str, str]]" = []
        po = objs[index] if 0 <= index < len(objs) else None
        if po is not None and po.class_flag and po.subtype_size & 0x200:
            if po.subtype_size & 0x1:
                if not self.is_composite_genie(index):
                    try:
                        an = own_value_bag(GP.decode(po)).get("AN")
                    except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
                        an = None
                    out = [
                        (k, "" if v is None else str(v))
                        for k, v in self.genie_all_params(index).items()
                        if an is None or k.upper() != "AN"
                    ]
                    if an is not None:
                        out.append(("AN", str(an)))
            else:
                out = self._copy_values(index, po, GP)
        got = memo[1][index] = tuple(out)
        return got

    def _copy_values(self, index: int, po, GP) -> "List[Tuple[str, str]]":
        """_genie_value_bag of the genie copy ``index`` (record ``po``) nested in a placed genie:
        its templates (in its parent genie's parameters) filled with its parent's values, as the
        composition derives the level (GP.subst_level, on a copy of its stored values). The
        record keeps no values for the levels between it and the outermost genie: each comes
        from its own parent, down from the outermost. A copy in a composite genie (no
        templates), its stored values."""
        objs = self.parsed.page.objects
        try:
            ents = GP.decode(po).entries or []
        except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
            return []
        if len(ents) < 2:
            return []
        own = ents[1]
        anc = self.page.ancestors(index)  # (the nearest first)
        roots = [a for a in anc if objs[a].subtype_size & 0x201 == 0x201]
        parent = next((a for a in anc if objs[a].class_flag and objs[a].subtype_size & 0x200), None)
        if parent is None or not roots or self.is_composite_genie(roots[-1]) or not own[2].pairs:
            return list(own[1].pairs)
        outer = GP.Bag(list(self._genie_value_bag(parent)))
        decls = GP.Bag([(k, "\n" if d is None else d) for k, d in GP.template_decls(own[2]).items()])
        mine = [own[0], GP.Bag(list(own[1].pairs)), own[2]]
        GP.subst_level(mine, [decls, outer, GP.Bag([])], outer_vals=outer, decls=decls)
        return list(mine[1].pairs)


def own_value_bag(r):
    """The value bag of a genie record's own level."""
    level = r.entries[1] if len(r.entries) > 1 else r.entries[-1]
    if len(r.entries) > 2 and len(level) > 2 and level[2].pairs:
        return level[2]
    return level[1]


_editing = None
_root_kept = None


def _ed():
    """ctgedit.editing (EditableDocument / ElementDocument are defined there, after this
    module is imported); read once (an import in a function runs PySide's import hook on each
    call)."""
    global _editing
    if _editing is None:
        from .. import editing

        _editing = editing
    return _editing


def _root_key_kept():
    """genie_place.root_key_kept, imported on first use."""
    global _root_kept
    if _root_kept is None:
        from ..format.genie_place import root_key_kept

        _root_kept = root_key_kept
    return _root_kept
