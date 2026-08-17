"""
laser_output.py — the shared laser output safety layer.

CANONICAL: laser-laser-laser/laser_output.py — sync, do not fork.
Copied verbatim into laser-arcade and promptwaver. It must stay importable on
its own: stdlib + numpy only, nothing from any host project, Python 3.9
compatible. A bug in here is a safety bug in three places.

What lives here is exactly the part every project needs and none of them
should write twice: the arm gate, the brightness ceiling, the watchdog, and
blanking on every exit path (docs/lasercubeoutput.md §4). What deliberately
does *not* live here is path planning, geometry correction or scene logic —
those differ legitimately per project.

Frame interchange format, shared by every backend:

    numpy (N,6) int32 — columns x, y, r, g, b, i
    x,y  0..4095 (12-bit DAC space)
    rgbi 0..255

Callers working in normalised floats convert at their own edge with
from_normalised().
"""

from __future__ import annotations

import atexit
import signal
import threading
import time

import numpy as np

try:                                    # typing.Protocol is 3.8+, but the
    from typing import Protocol         # runtime import is not worth a hard
except ImportError:                     # failure on an odd interpreter.
    Protocol = object


# Blank faster than this and we are just burning CPU; slower and a stalled
# render loop keeps painting for longer than it should.
WATCHDOG_TICK = 0.05
MIN_STALL_TIMEOUT = 0.25


class LaserOutput(Protocol):
    """Minimal contract every backend implements (Helios, LaserCube, Null)."""

    name: str
    #: True if write() blocks until the device is ready, so the device's own
    #: point clock paces the caller's render loop and it needs no sleep.
    paces_loop: bool
    last_points: int

    def write(self, frame, pps) -> bool:
        """Render one frame. Must be safe to call at frame rate."""

    def blank(self) -> bool:
        """Extinguish output NOW. Must bypass all scene/colour logic and any
        brightness state — this is the 'stop emitting' path and must work even
        if the rest of the app is in a bad state. True if the device took it."""

    def close(self) -> None:
        """Release the device. MUST blank first."""


class NullOutput:
    """No-hardware backend. The honest stand-in for 'no laser attached'."""

    name = "none"
    paces_loop = False

    def __init__(self):
        self.last_points = 0

    def write(self, frame, pps):
        self.last_points = len(frame)
        return True

    def blank(self):
        return True

    def close(self):
        pass

    def diagnostics(self):
        return [("device", "none — preview and monitor only", "ok"),
                ("points last frame", str(self.last_points), "")]


class SafeOutput:
    """Wraps any LaserOutput backend with the §4 safety requirements.

    Owns four things the backends deliberately do not:

    * an **arm gate** — constructed disarmed, and while disarmed it keeps
      writing but substitutes darkness. That is not the same as not writing:
      a DAC that stops being fed repeats its last frame forever, so the safe
      disarmed state is *actively streaming darkness*, not silence.
    * a **brightness ceiling** applied at the final packing stage, after all
      scene, mask and geometry logic, so nothing upstream can bypass it.
      A creative limiter, not a safety interlock — see the class docstring
      caveat in §4.4 of docs/lasercubeoutput.md.
    * a **watchdog** on a daemon thread, so a stalled render loop cannot leave
      the beam painting indefinitely.
    * **blanking on close**, with the device lock shared across all of the
      above so the watchdog and the render thread never call into the device
      concurrently.
    """

    def __init__(self, backend, max_brightness=0.05, armed=False,
                 on_change=None):
        self.backend = backend
        self.on_change = on_change          # notified on arm/disarm/ceiling
        self._lock = threading.RLock()
        self._armed = bool(armed)
        self._max_brightness = 0.0
        self.max_brightness = max_brightness

        self._panic = False
        self._closed = False
        self._started = False       # no frames written yet — nothing to guard
        self._heartbeat = time.monotonic()
        self._stall_timeout = MIN_STALL_TIMEOUT
        self._stalled = False
        self._stop_watchdog = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, daemon=True,
                                          name="laser-watchdog")
        self._watchdog.start()

    # ---- pass-through identity ----

    @property
    def name(self):
        return self.backend.name

    @property
    def paces_loop(self):
        return self.backend.paces_loop

    @property
    def last_points(self):
        return self.backend.last_points

    # ---- arm gate ----

    @property
    def armed(self):
        return self._armed and not self._panic

    def arm(self):
        """Arm the output. Returns True only if the device actually came up.

        If the backend has a hardware gate and that gate refuses — the
        LaserCube declining while over-temperature, say — we stay disarmed.
        Reporting ARMED while the device is dark is not a harmless cosmetic
        bug: the operator would believe the beam is under their control when
        the real state is something else entirely, and would find out when it
        cooled down and came back.
        """
        with self._lock:
            self._panic = False
            if not self._gate_device(True):
                self._armed = False
                self._notify()
                print("[laser] ARM REFUSED by the device — still disarmed")
                return False
            self._armed = True
        self._notify()
        return True

    def disarm(self):
        """Takes effect on the very next frame, with no fade. Beam changes
        are instant by design — a crossfade on the way down is a crossfade
        you are still emitting through."""
        with self._lock:
            self._armed = False
            self._gate_device(False)
        self._notify()

    def _gate_device(self, on):
        """Mirror the arm state onto the device, for backends that have a
        hardware output gate.

        Optional by design. The Helios has no such gate — its safe state is
        streaming darkness, which the frame gate below already provides. The
        LaserCube does (CMD_SET_OUTPUT), and using it means a disarmed device
        stops emitting even if this process is killed mid-frame. Backends
        without enable/disable are unaffected.

        Returns True if the device is in the requested state — including the
        vacuous True for backends with no gate at all. Disarming reports True
        even on error: a failed disable must never leave the caller believing
        it is still armed, and the frame gate has already gone dark anyway.
        """
        fn = getattr(self.backend, "enable" if on else "disable", None)
        if fn is None:
            return True
        try:
            ok = fn()
            return True if ok is None else bool(ok)
        except Exception as e:
            print(f"[laser] device output gate failed: {e}")
            return not on

    def set_armed(self, value):
        self.arm() if value else self.disarm()

    # ---- brightness ceiling ----

    @property
    def max_brightness(self):
        return self._max_brightness

    @max_brightness.setter
    def max_brightness(self, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.05
        self._max_brightness = float(np.clip(v, 0.0, 1.0))

    def set_max_brightness(self, value):
        self.max_brightness = value
        self._notify()

    def _notify(self):
        if self.on_change:
            try:
                self.on_change(self)
            except Exception:
                pass

    # ---- output ----

    def write(self, frame, pps):
        # Stamped before the (potentially blocking) device write, so time
        # spent legitimately waiting on the DAC still counts as liveness.
        self._heartbeat = time.monotonic()
        self._started = True
        n = max(len(frame), 1)
        # A frame legitimately takes n/pps seconds; at 4000 points and
        # 5000 pps that is 800 ms, so a fixed threshold would false-trip
        # constantly. Allow three frames' grace.
        self._stall_timeout = max(MIN_STALL_TIMEOUT, 3.0 * n / max(pps, 1))

        out = self._gate(frame)
        with self._lock:
            if self._closed:
                return False
            ok = self.backend.write(out, pps)
        self._heartbeat = time.monotonic()
        if self._stalled:
            self._stalled = False
            print("[laser] render loop recovered — watchdog released")
        return ok

    def _gate(self, frame):
        """Apply the arm gate and the ceiling. Returns a frame safe to send.

        Copies rather than mutating: the caller's array may still be aliased
        by the preview or the browser stream, and the ceiling is a DAC-only
        transform — the monitor must keep showing what the content actually
        is, or the limiter becomes invisible instead of obvious.
        """
        if not self.armed:
            out = frame.copy()
            out[:, 2:6] = 0          # keep the geometry, drop every photon
            return out
        cap = self._max_brightness
        if cap >= 1.0:
            return frame
        out = frame.copy()
        out[:, 2:6] = (out[:, 2:6] * cap).astype(out.dtype)
        return out

    # ---- safety ----

    def diagnostics(self):
        """Backend diagnostics, plus the safety state this layer owns.

        Runs under the device lock so it cannot interleave with a write or
        with the watchdog. Backends that provide nothing still return the
        safety rows, so the panel is never empty.
        """
        rows = [("armed", "ARMED" if self.armed else "disarmed",
                 "warn" if self.armed else "ok"),
                ("brightness ceiling", f"{self._max_brightness:.0%}",
                 "warn" if self._max_brightness > 0.25 else "ok"),
                ("watchdog", "tripped" if self._stalled else "idle",
                 "bad" if self._stalled else "ok"),
                ("stall threshold", f"{self._stall_timeout * 1000:.0f} ms", "")]
        fn = getattr(self.backend, "diagnostics", None)
        if fn is None:
            return rows + [("backend", self.backend.name, "")]
        try:
            with self._lock:
                return list(fn()) + rows
        except Exception as e:
            return [("error", f"diagnostics failed: {e}", "bad")] + rows

    def swap_backend(self, factory, kind="?"):
        """Replace the live backend — e.g. Helios USB to LaserCube network —
        without restarting.

        **Always disarms first, and stays disarmed afterwards.** Arming is a
        statement about one specific projector; carrying it across a device
        change would arm a device the operator never armed. Re-arming is
        deliberate, every time.

        `factory` is called to build the new backend and may raise (no device
        present, network discovery failed). If it does, we land on NullOutput
        rather than leaving a closed backend in place — a dead object whose
        blank() silently does nothing is the worst outcome here.

        Returns (ok, message) for the caller to surface.
        """
        with self._lock:
            self.disarm()
            old = self.backend
            for step, fn in (("blank", old.blank), ("close", old.close)):
                try:
                    fn()
                except Exception as e:
                    print(f"[laser] old backend {step}() failed: {e}")
            self.backend = NullOutput()      # safe intermediate state
            self._started = False            # new device, new liveness clock
            try:
                self.backend = factory()
            except Exception as e:
                msg = f"could not open {kind}: {e}"
                print(f"[laser] {msg} — output is now NONE")
                return False, msg
        self._notify()
        print(f"[laser] output switched to {self.backend.name} — DISARMED")
        return True, f"switched to {self.backend.name}"

    def panic(self):
        """Disarm and blank as directly as possible. Safe to call from a
        signal handler: it only sets a flag and lets the watchdog thread do
        the device call, because re-entering the driver while the main thread
        is inside a write is not safe."""
        self._panic = True
        self._armed = False

    def blank(self):
        with self._lock:
            if self._closed:
                return False
            return self.backend.blank()

    def close(self):
        """Idempotent — it is called from the render loop's finally block and
        again from atexit, and either one may get there first."""
        # Stand the watchdog down *before* contending for the lock. At
        # interpreter shutdown a daemon thread holding it mid-blank would
        # otherwise be a deadlock on the one path that must always complete.
        self._armed = False
        self._stop_watchdog.set()
        with self._lock:
            if self._closed:
                return
            # Blank explicitly rather than trusting the backend's close() to
            # do it. Backends vary; this layer is the guarantee.
            try:
                self.backend.blank()
            except Exception as e:
                print(f"[laser] blank on close failed: {e}")
            try:
                self.backend.close()
            finally:
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- watchdog ----

    def _watch(self):
        """Blank if the render loop stops feeding us.

        Only acts when it can take the device lock. Failing to take it means
        the render thread is alive inside write() — that is evidence of
        liveness, not a stall. If the driver is genuinely wedged inside a C
        call then no amount of software can blank that device anyway; the
        hardware key switch and Remote Stop are the layer that covers it.
        """
        while not self._stop_watchdog.wait(WATCHDOG_TICK):
            if self._closed:
                return
            # Before the first frame there is nothing to guard, and startup
            # (pygame init, MIDI enumeration, audio device open) routinely
            # takes longer than the stall threshold. Guarding it would just
            # blank an output that has not emitted anything yet.
            stale = (self._started and
                     (time.monotonic() - self._heartbeat) > self._stall_timeout)
            if not (stale or self._panic):
                continue
            if not self._lock.acquire(blocking=False):
                continue            # render thread is inside write(): alive
            try:
                if self._closed:
                    return
                if not self._stalled:
                    self._stalled = True
                    why = "panic" if self._panic else "render loop stalled"
                    print(f"[laser] WATCHDOG: {why} — blanking output")
                self.backend.blank()
            except Exception as e:
                print(f"[laser] WATCHDOG: blank failed: {e}")
            finally:
                self._lock.release()


def install_panic_handlers(out):
    """Blank on every exit path (§4.1): SIGINT, SIGTERM and atexit.

    SIGTERM is the one that matters — without a handler the default action
    terminates the process outright, so the render loop's finally block never
    runs and the DAC keeps replaying its last frame. Both handlers raise
    KeyboardInterrupt so the existing try/finally teardown still runs normally.

    Call this as late as possible before the render loop, so nothing installed
    afterwards displaces the handlers.
    """
    atexit.register(out.close)

    def _handler(signum, frame):
        out.panic()
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread, or the platform disallows it. The
            # atexit backstop still applies.
            pass


def from_normalised(xy, rgb, i=None):
    """Build the (N,6) int32 interchange frame from normalised floats.

    xy:  (N,2) float in [-1, 1]
    rgb: (N,3) float in [0, 1]
    i:   (N,) float in [0, 1], or None to derive it as max(r, g, b)

    For projects whose pipelines are float-native. The inverse of the
    v/2047.5 - 1 mapping used everywhere in normalised space.
    """
    xy = np.asarray(xy, dtype=np.float64)
    rgb = np.asarray(rgb, dtype=np.float64)
    n = len(xy)
    out = np.zeros((n, 6), dtype=np.int32)
    out[:, 0:2] = np.clip((xy + 1.0) * 2047.5, 0, 4095).astype(np.int32)
    out[:, 2:5] = np.clip(rgb * 255.0, 0, 255).astype(np.int32)
    if i is None:
        out[:, 5] = out[:, 2:5].max(axis=1)
    else:
        out[:, 5] = np.clip(np.asarray(i, dtype=np.float64) * 255.0,
                            0, 255).astype(np.int32)
    return out
