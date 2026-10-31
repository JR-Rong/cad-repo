"""Load the SHIPPED 2.5 duplicate predicates without needing FreeCAD.

`cadkit.fingerprint` imports FreeCAD at module scope (its compute() needs a
live document), while scaled_same()/related() are pure math over recorded
invariants. Stubbing the FreeCAD module lets a host-python3 merge gate use the
exact shipped predicates instead of a paraphrase that drifts. compute() is
never reachable this way — if the real module is importable, use it as-is.
"""
import importlib
import os
import sys
import types


def load(plugin_python):
    """Return (module, mode) or raise ImportError with the reason."""
    sys.path.insert(0, os.path.abspath(plugin_python))
    try:
        return importlib.import_module("cadkit.fingerprint"), "engine"
    except ImportError:
        pass
    saved = {}
    for name in ("FreeCAD", "Part", "Draft", "Sketcher", "Mesh", "MeshPart", "TechDraw"):
        mod = types.ModuleType(name)
        mod.__doc__ = "stub for host-python import of cadkit.fingerprint"
        sys.modules[name] = mod
        saved[name] = None
    try:
        return importlib.import_module("cadkit.fingerprint"), "pure-math-stub"
    except ImportError:
        for name in saved:
            sys.modules.pop(name, None)
        raise
    # note: the stubs stay installed on purpose; removing them would break
    # later attribute lookups inside the imported module.


def selftest(plugin_python):
    fp, mode = load(plugin_python)
    a = {"solids": 1, "faces": 120, "edges": 360, "steps": 120, "compactness": 41.0,
         "bbox": [120.0, 80.0, 40.0], "volume": 180000.0, "ratios": [1.0, 0.667, 0.333]}
    b = {k: v for k, v in a.items()}
    b["bbox"] = [x * 1.25 for x in a["bbox"]]
    b["volume"] = a["volume"] * 1.25 ** 3
    c = {k: v for k, v in a.items()}
    c["faces"], c["edges"], c["steps"] = 121, 363, 121      # different topology
    assert fp.scaled_same(a, b) is True, "scaled copy must be flagged"
    assert fp.scaled_same(a, c) is False, "different topology must not be flagged"
    return mode
