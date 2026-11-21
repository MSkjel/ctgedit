"""Host Plant SCADA's own composer DLL to cross-check our composite genie output."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

POWERSHELL32 = r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
DLL_NAME = "SE.CtDraw.Composition.dll"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$asm = [System.Reflection.Assembly]::LoadFrom('%(dll)s')
$bf = [System.Reflection.BindingFlags]'Static,NonPublic,Public'
$hm = $asm.GetType('SchneiderElectric.CtDraw.Composition.CompositionInstanceAccessor').GetMethod('GenerateMd5HashFromParameterValues', $bf)
$dm = $asm.GetType('SchneiderElectric.CtDraw.Composition.XmlHelper').GetMethod('GetDigest', $bf)
Add-Type -AssemblyName System.Xml.Linq
$req = [System.IO.File]::ReadAllText('%(req)s', [System.Text.Encoding]::UTF8) | ConvertFrom-Json
foreach ($h in $req.hashes) {
  $d = [System.Collections.Generic.Dictionary[string,object]]::new()
  foreach ($p in $h.params) {
    if ($p.t -eq 'Bool') { $v = [bool]$p.v } elseif ($p.t -eq 'Int') { $v = [int]$p.v } else { $v = [string]$p.v }
    $d.Add([string]$p.n, $v)
  }
  $a = New-Object object[] 2; $a[0] = [guid]$h.id; $a[1] = $d.psobject.BaseObject
  'H ' + $hm.Invoke($null, $a)
}
foreach ($f in $req.digests) {
  $doc = [System.Xml.Linq.XDocument]::Load([string]$f)
  $b = New-Object object[] 1; $b[0] = $doc.Root
  'D ' + $dm.Invoke($null, $b)
}
"""


class HostError(RuntimeError):
    pass


def dll_path() -> Optional[str]:
    try:
        from .compiler import scada_bin

        p = os.path.join(scada_bin(), DLL_NAME)
    except Exception:  # noqa: BLE001
        return None
    return p if os.path.isfile(p) else None


def available() -> bool:
    """The DLL and the 32-bit Windows PowerShell are there."""
    return os.name == "nt" and os.path.isfile(POWERSHELL32) and dll_path() is not None


def _typed(v) -> Tuple[str, object]:
    if isinstance(v, bool):
        return "Bool", v
    if isinstance(v, int):
        return "Int", v
    return "String", str(v)


def run(
    hashes: Sequence[Tuple[str, Dict[str, object]]] = (), digests: Sequence[str] = (), timeout: float = 120.0
) -> Tuple[List[str], List[str]]:
    """Ask the DLL for the instance names of ``hashes`` ([(composition id,
    {option: typed value}) in template order]) and the digests of the
    template files ``digests``. Returns (names, digests). HostError when the
    host cannot run."""
    dll = dll_path()
    if dll is None or not os.path.isfile(POWERSHELL32):
        raise HostError("Plant SCADA's composition DLL or the 32-bit Windows PowerShell is not installed.")
    req = {
        "hashes": [
            {"id": cid, "params": [{"n": k, "t": _typed(v)[0], "v": _typed(v)[1]} for k, v in vals.items()]}
            for cid, vals in hashes
        ],
        "digests": [os.path.abspath(p) for p in digests],
    }
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ctgedit_composer_")
    try:
        rq = os.path.join(tmp, "request.json")
        with open(rq, "w", encoding="utf-8") as fh:
            json.dump(req, fh)
        ps1 = os.path.join(tmp, "host.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as fh:
            fh.write(_SCRIPT % {"dll": dll.replace("'", "''"), "req": rq.replace("'", "''")})
        try:
            cp = subprocess.run(
                [POWERSHELL32, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1],
                capture_output=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HostError("The composition host did not run: %s" % e) from e
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    out = cp.stdout.decode("utf-8", "replace").splitlines()
    names = [ln[2:].strip() for ln in out if ln.startswith("H ")]
    digs = [ln[2:].strip() for ln in out if ln.startswith("D ")]
    if cp.returncode != 0 or len(names) != len(req["hashes"]) or len(digs) != len(digests):
        raise HostError(
            "The composition host failed: %s"
            % (cp.stderr.decode("utf-8", "replace").strip()[:400] or "no output")
        )
    return names, digs
