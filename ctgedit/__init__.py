"""ctgedit, an editor for AVEVA Plant SCADA / Citect graphics pages.

``__version__`` is the newest ``vX.Y.Z`` tag of the checkout (the next patch level on a later
commit), or the built program's stamp (ctgedit/buildinfo.py)."""


def __getattr__(name: str):
    if name == "__version__":
        from .buildinfo import version

        return version()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
