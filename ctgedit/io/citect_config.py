"""Find the active User folder and read citect.ini."""

from __future__ import annotations

import os
from ..format.textcodec import CODEC
from ..infra import install


def live_user_dir() -> str | None:
    """The active install's ``User`` directory, or None if undiscoverable
    (infra/install.user_dir: the registry's citect.ini wins, else the newest
    one whose User folder exists; CTG_SCADA_ROOT = a fake install or none)."""
    return install.user_dir()


def live_project_dir(project: str = "Example") -> str | None:
    """The active install's ``User\\<project>`` directory, or None."""
    user = live_user_dir()
    if user is None:
        return None
    proj = os.path.join(user, project)
    return proj if os.path.isdir(proj) else user


def active_ini_path() -> str | None:
    """The ACTIVE install's citect.ini (the registry's DefaultINIPath, else the
    Config folder next to the live User directory; a fake install's own), or
    None."""
    return install.active_ini()


def ini_value(section: str, key: str, default: str = "") -> str:
    """A value of the ACTIVE install's citect.ini (case-insensitive section /
    key, like GetPrivateProfileString), ``default`` when absent."""
    ini = active_ini_path()
    if not ini:
        return default
    sec = None
    with open(ini, "rb") as fh:
        text = fh.read().decode("cp1252", "replace")
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
        elif sec == section.lower() and "=" in s:
            k, v = s.split("=", 1)
            if k.strip().lower() == key.lower():
                return v.strip()
    return default


def fast_display_enabled() -> bool:
    """``[animator] FastDisplay`` (default 1): the Graphics Builder writes a
    page's .ctF only when on, and
    the runtime reads the .ctF instead of the .ctg only when on."""
    v = ini_value("animator", "FastDisplay", "1")
    try:
        return int(v) != 0
    except ValueError:
        return v.strip().lower() not in ("false", "no", "off")


#: the copy of citect.ini kept before ctgedit changes it (the runtime backend
#: keeps the same one, backend/runtime.set_run_project)
BACKUP_SUFFIX = ".ctgedit-bak"


class IniWriteError(OSError):
    """citect.ini could not be changed."""


def set_ini_value(section: str, key: str, value: str, ini: str | None = None) -> bool:
    """Set ``[section] key`` in citect.ini."""
    from ..format.fileio import write_atomic

    ini = ini or active_ini_path()
    if not ini:
        raise IniWriteError(
            "The Plant SCADA configuration file (citect.ini) of the active install was not found."
        )
    try:
        with open(ini, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        raise IniWriteError("Could not read %s: %s" % (ini, e)) from e
    nl = "\r\n" if b"\r\n" in raw else "\n"
    # the lossless code page: every byte comes back as it was (cp1252 with
    # 'replace' would turn its 5 undefined bytes into '?' on the write)
    text = raw.decode(CODEC)
    lines = text.split(nl)
    sec, found, end_of = None, False, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
            continue
        if sec != section.lower():
            continue
        if s:
            end_of = i
        if "=" in ln and ln.split("=", 1)[0].strip().lower() == key.lower() and not s.startswith(";"):
            k, _eq, v = ln.partition("=")
            lead = v[: len(v) - len(v.lstrip())]
            lines[i] = k + "=" + lead + value
            found = True
    if not found:
        new = "%s =%s" % (key, value)
        if end_of is None:
            head = next(
                (i for i, ln in enumerate(lines) if ln.strip().lower() == "[%s]" % section.lower()), None
            )
            if head is None:
                while lines and not lines[-1].strip():
                    lines.pop()
                lines += ["", "[%s]" % section, new, ""]
            else:
                lines.insert(head + 1, new)
        else:
            lines.insert(end_of + 1, new)
    out = nl.join(lines).encode(CODEC, "replace")
    if out == raw:
        return False
    try:
        write_atomic(ini + BACKUP_SUFFIX, raw)
        write_atomic(ini, out)
    except OSError as e:
        raise IniWriteError(
            "Could not write %s: %s. Check that you may change files in that folder." % (ini, e)
        ) from e
    try:
        from ..infra import logsetup

        logsetup.files_written(
            "[%s] %s = %s" % (section, key, value),
            [ini + BACKUP_SUFFIX, ini],
            "the old citect.ini is kept as the .ctgedit-bak copy",
        )
    except Exception:  # noqa: BLE001 -- swallow-ok: the log is an aid only
        pass
    return True


def set_fast_display(on: bool, ini: str | None = None) -> bool:
    """Turn ``[Animator] FastDisplay`` on (1) or off (0) -- what the
    Graphics Builder's Options > Fast runtime display writes (CitectIniWriteValueAsString("animator", "FastDisplay")). Returns whether
    citect.ini changed."""
    return set_ini_value("Animator", "FastDisplay", "1" if on else "0", ini=ini)


def _flag(v: str) -> bool:
    try:
        return int(v) != 0
    except ValueError:
        return v.strip().lower() in ("true", "yes", "on")


def template_update_enabled() -> bool:
    """[Animator] TemplateUpdate (default 0; CtDraw reads
    it): when on, re-applying a changed template to a linked page also
    overwrites the page's properties (size, background, page properties)
    with the template's -- AVEVA help topic 1059816."""
    return _flag(ini_value("animator", "TemplateUpdate", "0"))


def allow_edit_super_genie_page() -> bool:
    """``[CtDraw.RSC] AllowEditSuperGeniePage`` (default 0; CtDraw
    reads it when a page opens, help 1101488): 0 = a page
    created from a super genie is not opened for editing, 1 = the Graphics
    Builder asks first."""
    return _flag(ini_value("CtDraw.RSC", "AllowEditSuperGeniePage", "0"))


#: AVEVA's documented defaults of the [CtExplore] template keys (help topics
#: 1101586-1101588)
DEFAULT_TEMPLATE_STYLE = "Tab_Style_1"
DEFAULT_TEMPLATE_RESOLUTION = "Default"


def template_defaults() -> dict:
    """The install's default template style and resolution."""
    return {
        "style": ini_value("CtExplore", "DefaultTemplateStyle", DEFAULT_TEMPLATE_STYLE)
        or DEFAULT_TEMPLATE_STYLE,
        "resolution": ini_value("CtExplore", "DefaultTemplateResolution", DEFAULT_TEMPLATE_RESOLUTION)
        or DEFAULT_TEMPLATE_RESOLUTION,
        "title_bar": _flag(ini_value("CtExplore", "DefaultTemplateTitleBar", "0")),
    }
