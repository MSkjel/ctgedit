"""The composer DLL itself, in a 32-bit PowerShell host."""

import glob
import os

import pytest

SA_R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA"
STORE = os.path.join(SA_R2, "_CompositionCaches")
MIXING_R2 = os.path.join(SA_R2, "Mixing_L3.ctg")
TPL_DIR = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\SA_Library\Composite Genies"
DRIVE = "e91e1230-c421-4dec-b7cf-f6bc99b978df"
VALVE = "051512b8-937d-4392-98e8-7a1fda30ecac"
DRIVE_850 = "dd23dc2f0e57bd408d6411d9257bf00"
VALVE_850 = "fb09cc3d43be266b6521f783d8c54e8"

have_r2 = pytest.mark.skipif(
    not (os.path.isdir(STORE) and os.path.isdir(TPL_DIR)),
    reason="R2 ExampleSA composition caches not installed",
)


def _instances(guid):
    d = os.path.join(STORE, "{%s}" % guid)
    return d, sorted(f[:-4] for f in os.listdir(os.path.join(d, "Instances")))


# -- the .NET oracle ----------------------------------------------------------------------
@have_r2
def test_names_and_digests_match_plant_scadas_composer_dll():
    """The composer DLL itself (32-bit PowerShell host, backend/
    composition_host) computes the same instance names and template digests."""
    from ctgedit.backend import composition_host as H
    from ctgedit.format import composer as CP
    from ctgedit.format import composites as C

    if not H.available():
        pytest.skip("SE.CtDraw.Composition.dll / 32-bit PowerShell not available")
    reqs, want = [], []
    for guid in (VALVE, DRIVE):
        d, hashes = _instances(guid)
        model = CP.load_model(os.path.join(d, CP.copied_template_name(guid)))
        for h in hashes[:6]:
            vals = CP.adapted_values(
                model, C.load_instance(os.path.join(d, "Instances", h + ".xml")).values()
            )
            comp = model.composition_for({k.lower(): v for k, v in vals.items()})
            reqs.append((comp.id, vals))
            want.append(h)
    files = sorted(glob.glob(os.path.join(TPL_DIR, "*.xml")))[:6]
    names, digs = H.run(reqs, files)
    assert names == want
    for f, dg in zip(files, digs):
        with open(f, "rb") as fh:
            assert dg == CP.template_digest(fh.read()), f
