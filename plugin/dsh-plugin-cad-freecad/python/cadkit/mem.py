"""Memory governor for the CadKit engine.

Why this exists
---------------
macOS does NOT enforce RLIMIT_AS / RLIMIT_RSS (`ulimit -v` is a no-op), so a
freecadcmd job cannot be capped by the kernel. The pipeline is therefore
governed in three cooperating layers:

1. **Budget-driven sizing (deterministic, the main control).** Before any
   allocation we derive, from the per-process cap, the largest triangle
   budget / render resolution / probe concurrency that can fit. Nothing is
   started "as big as possible and hope".
2. **In-process watchdog.** A daemon thread samples real RSS
   (`task_info(MACH_TASK_BASIC_INFO)`, no psutil in freecadcmd's interpreter)
   and raises the degrade level once the cap is approached. Long loops
   (render views, tessellation retries) consult it and shed quality —
   supersampling, then mesh density — instead of pushing the machine into
   the swap. Gross overshoot (> HARD_FACTOR * cap) aborts that job with a
   clean, machine-readable reason rather than letting the OS kill something
   random.
3. **Parent-side guard (index.js).** Optional best-effort sampling of the
   whole child process group plus a kill switch, used when a native OCC
   allocation runs away before Python regains control.

Env contract (set by the Host plugin, read here):

    CADKIT_MEM_MB           per-process soft cap, default 2048
    CADKIT_MEM_FLOOR_MB     OS memory that must stay available, default 768
    CADKIT_PROBE_MAX_WORKERS hard ceiling on probe parallelism, default 2
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

DEFAULT_CAP_MB = 2048
DEFAULT_FLOOR_MB = 768
DEFAULT_PROBE_MAX_WORKERS = 2

# Measured on this pipeline: pure-Python triangle handling used to cost
# ~1.45 KB per triangle; the vectorized path costs ~0.45 KB of live arrays.
# Budget with headroom for OCC + the FCStd doc.
TRI_BYTES = 520
# Per-pixel peak of one render pass: z-buffer (float32) + image (rgb8) plus
# one band of float32 raster scratch.
RENDER_BYTES_PER_PX = 9.5
HARD_FACTOR = 1.35        # abort beyond this multiple of the cap
WARN_AT = 0.80            # start degrading at this fraction of the cap


def _env_int(name, default):
    try:
        v = int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default
    return v if v > 0 else default


def cap_mb():
    """Per-freecadcmd-process soft cap."""
    return _env_int("CADKIT_MEM_MB", DEFAULT_CAP_MB)


def floor_mb():
    """Memory that must remain available to the OS before we start work."""
    return _env_int("CADKIT_MEM_FLOOR_MB", DEFAULT_FLOOR_MB)


def probe_max_workers():
    return _env_int("CADKIT_PROBE_MAX_WORKERS", DEFAULT_PROBE_MAX_WORKERS)


def abort_enabled():
    return True   # W16 recheck: no environment escape for the governor


# ---------------------------------------------------------------------------
# live memory readings
# ---------------------------------------------------------------------------
_libc = None
_task = None
_INFO_CLS = None
MACH_TASK_BASIC_INFO = 20


def _libc_load():
    global _libc, _task, _INFO_CLS
    if _libc is None:
        import ctypes.util

        class BasicInfo(ctypes.Structure):
            _fields_ = [
                ("virtual_size", ctypes.c_uint64),
                ("resident_size", ctypes.c_uint64),
                ("resident_size_max", ctypes.c_uint64),
                ("user_time", ctypes.c_uint64),
                ("system_time", ctypes.c_uint64),
                ("resident_page_count", ctypes.c_uint32),
            ]

        name = ctypes.util.find_library("c") or "libc.dylib"
        _libc = ctypes.CDLL(name)
        _libc.mach_task_self.restype = ctypes.c_uint
        _libc.task_info.restype = ctypes.c_int
        _task = _libc.mach_task_self()
        _INFO_CLS = BasicInfo
    return _libc, _task, _INFO_CLS


def rss_mb():
    """Current resident size of this process in MB (float).

    Uses mach task_info on macOS; falls back to ru_maxrss (peak, so it is a
    conservative upper bound) on other platforms.
    """
    try:
        if sys.platform == "darwin":
            libc, task, cls = _libc_load()
            info = cls()
            count = ctypes.c_uint(ctypes.sizeof(cls) // 4)
            if libc.task_info(task, MACH_TASK_BASIC_INFO,
                              ctypes.byref(info), ctypes.byref(count)) == 0:
                return info.resident_size / 1048576.0
    except Exception:
        pass
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (peak / 1048576.0) if sys.platform == "darwin" else (peak / 1024.0)


def peak_mb():
    """High-water-mark RSS in MB (ru_maxrss; bytes on macOS, KB on Linux)."""
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (peak / 1048576.0) if sys.platform == "darwin" else (peak / 1024.0)


def avail_mb():
    """Roughly reclaimable physical memory in MB, or None if unknown."""
    try:
        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True,
                                 text=True, timeout=5).stdout
            page = 4096
            for line in out.splitlines():
                if "page size of" in line:
                    digits = "".join(c for c in line.split("page size of")[1]
                                     if c.isdigit())
                    if digits:
                        page = int(digits)
                    break
            got = {"free": 0, "inactive": 0, "speculative": 0, "purgeable": 0}
            for key, name in (("Pages free:", "free"),
                              ("Pages inactive:", "inactive"),
                              ("Pages speculative:", "speculative"),
                              ("Pages purgeable:", "purgeable")):
                for line in out.splitlines():
                    if line.startswith(key):
                        got[name] = int("".join(c for c in line.split(":")[1]
                                                if c.isdigit()) or 0)
                        break
            if got["free"] or got["inactive"]:
                return sum(got.values()) * page / 1048576.0
            return None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        return None
    return None


def memory_ok(need_factor=1.0):
    """True when the OS has at least floor_mb * need_factor available."""
    a = avail_mb()
    return True if a is None else a >= floor_mb() * need_factor


# ---------------------------------------------------------------------------
# budget math
# ---------------------------------------------------------------------------
def tri_budget(allow_mb=None, cap=None):
    """Triangle count that fits the render share of the cap."""
    cap = cap or cap_mb()
    allow = allow_mb if allow_mb is not None else cap * 0.35
    return max(20000, int(allow * 1048576.0 / TRI_BYTES))


def clamp_long_side(long_side, cap=None, allow_frac=0.30):
    """Largest long side whose framebuffer + band scratch fits the budget.

    The purchase spec requires long side >= 2000 px, so we never go below it;
    callers report a degrade note when they are pinned there.
    """
    cap = cap or cap_mb()
    allow_px = cap * allow_frac * 1048576.0 / RENDER_BYTES_PER_PX
    limit = int(max(2000, (allow_px / 4.0) ** 0.5))   # square image
    return int(min(long_side, limit))


# ---------------------------------------------------------------------------
# watchdog + degrade log
# ---------------------------------------------------------------------------
_DEGRADED = []
_LOCK = threading.Lock()


def degrade(note):
    """Record one auto-degrade step (surfaced in every action's JSON)."""
    with _LOCK:
        if note not in _DEGRADED:
            _DEGRADED.append(note)
    sys.stderr.write("CADKIT-MEM: degrade: %s\n" % note)


def degraded():
    with _LOCK:
        return list(_DEGRADED)


class Watchdog:
    """Samples RSS in the background; exposes an escalating pressure level.

    level 0: below WARN_AT * cap  — work at full quality
    level 1: over  WARN_AT * cap  — shed supersampling / mesh density
    level 2: over  HARD_FACTOR*cap — abort this action (or kill the process
            when inside a native call that cannot raise)
    """

    def __init__(self, cap=None, interval=0.4, enable=None):
        self.cap = float(cap or cap_mb())
        self.interval = interval
        self.enable = abort_enabled() if enable is None else enable
        self.level = 0
        self.last = 0.0
        self.stop = False
        self._th = None

    def start(self):
        if self.enable:
            self._th = threading.Thread(target=self._loop, daemon=True)
            self._th.start()
        return self

    def sample(self):
        self.last = rss_mb()
        if self.last >= self.cap * HARD_FACTOR:
            self.level = 2
        elif self.last >= self.cap * WARN_AT:
            self.level = max(1, self.level)
        return self.level

    def _loop(self):
        while not self.stop:
            try:
                self.sample()
            except Exception:
                return
            if self.level >= 2 and self.enable:
                # No subprocess/fork here: overshoot usually means the host is
                # already out of memory. Report on stdout so the Host can still
                # parse a CADKIT result, then leave immediately.
                try:
                    sys.stderr.write(
                        "CADKIT-MEM: hard cap exceeded (rss=%.0fMB cap=%.0fMB) — "
                        "aborting job\n" % (self.last, self.cap))
                    sys.stderr.flush()
                    sys.stdout.write("CADKIT-MEM: abort\n")
                    sys.stdout.flush()
                except Exception:
                    pass
                os._exit(86)
            time.sleep(self.interval)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop = True
        if self._th is not None:
            try:
                self._th.join(timeout=2)
            except Exception:
                pass


def status(extra=None, cap=None):
    """JSON-serialisable memory report for the action result."""
    cap = cap or cap_mb()
    out = {
        "cap_mb": int(cap),
        "rss_mb": round(rss_mb(), 1),
        "peak_mb": round(peak_mb(), 1),
        "avail_mb": (None if avail_mb() is None else round(avail_mb())),
        "floor_mb": floor_mb(),
        "degraded": degraded(),
    }
    if extra:
        out.update(extra)
    return out
