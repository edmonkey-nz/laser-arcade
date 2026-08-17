"""
helios.py — thin ctypes wrapper around libHeliosDacAPI.so (Grix/helios_dac SDK).

Classic WriteFrame() interface: 12-bit X/Y (0..4095), 8-bit R/G/B/I.
Expects libHeliosDacAPI.so next to this file (or HeliosLaserDAC.dll on Windows)
unless an explicit lib_path is passed.

CANONICAL: laser-laser-laser/helios.py — sync, do not fork.
Shared with laser-arcade and promptwaver. Keep it dependency-free beyond
numpy + stdlib, keep the library search injectable (each project stages the
shared library differently), and keep Python 3.9 compatible.

Safety note: WriteFrame() without HELIOS_FLAG_SINGLE_MODE *repeats* the frame
until the next one arrives. That is why blank() writes a dark frame in repeat
mode rather than merely stopping — a stopped-but-unblanked DAC would sit there
replaying whatever was last sent. See docs/lasercubeoutput.md §4.1/§4.3.
"""

from __future__ import annotations

import ctypes
import os
import platform
import time

import numpy as np

HELIOS_FLAGS_DEFAULT = 0
HELIOS_FLAG_START_IMMEDIATELY = 1 << 0
HELIOS_FLAG_SINGLE_MODE = 1 << 1
HELIOS_FLAG_DONT_BLOCK = 1 << 2

XY_MAX = 0xFFF  # 12-bit
XY_CENTRE = 2047


class HeliosPoint(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('x', ctypes.c_uint16),
                ('y', ctypes.c_uint16),
                ('r', ctypes.c_uint8),
                ('g', ctypes.c_uint8),
                ('b', ctypes.c_uint8),
                ('i', ctypes.c_uint8)]


POINT_DTYPE = np.dtype([('x', '<u2'), ('y', '<u2'),
                        ('r', 'u1'), ('g', 'u1'),
                        ('b', 'u1'), ('i', 'u1')])


def _lib_path():
    here = os.path.dirname(os.path.abspath(__file__))
    system = platform.system()
    if system == "Windows":
        return os.path.join(here, "HeliosLaserDAC.dll")
    if system == "Darwin":
        return os.path.join(here, "libHeliosDacAPI.dylib")
    return os.path.join(here, "libHeliosDacAPI.so")


class HeliosDAC:
    """Manages one Helios DAC (device 0 by default)."""

    def __init__(self, dac_num=0, lib_path=None):
        self.lib = ctypes.cdll.LoadLibrary(lib_path or _lib_path())
        self.dac_num = dac_num
        self.num_devices = self.lib.OpenDevices()
        if self.num_devices <= dac_num:
            self.lib.CloseDevices()
            raise RuntimeError(
                f"Helios DAC #{dac_num} not found "
                f"({self.num_devices} device(s) detected). "
                "On Linux, check udev rules or run with sudo.")
        self._closed = False
        # Pre-built dark frame, so the panic/blank path is a WriteFrame and
        # nothing else — no allocation, no numpy, safe to call from a
        # watchdog thread or a teardown handler.
        self._blank_frame = (HeliosPoint * 4)()
        for pt in self._blank_frame:
            pt.x = XY_CENTRE
            pt.y = XY_CENTRE
            pt.r = pt.g = pt.b = pt.i = 0

    # ---- output ----

    def write_frame(self, points_np, pps, timeout=0.5):
        """
        points_np: numpy uint16/uint8 structured data as (N,6) int array
                   columns: x, y (0..4095), r, g, b, i (0..255)
        Blocks (polling GetStatus) until the DAC buffer is free, then sends.
        Returns False on timeout or write error.
        """
        n = len(points_np)
        frame = (HeliosPoint * n)()
        # Bulk fill via ctypes.memmove from a packed numpy view
        packed = np.zeros(n, dtype=POINT_DTYPE)
        packed['x'] = points_np[:, 0]
        packed['y'] = points_np[:, 1]
        packed['r'] = points_np[:, 2]
        packed['g'] = points_np[:, 3]
        packed['b'] = points_np[:, 4]
        packed['i'] = points_np[:, 5]
        ctypes.memmove(frame, packed.tobytes(), ctypes.sizeof(frame))

        if not self._wait_ready(timeout):
            return False
        return self.lib.WriteFrame(self.dac_num, int(pps),
                                   HELIOS_FLAGS_DEFAULT, frame, n) == 1

    def _wait_ready(self, timeout):
        """Poll GetStatus until the DAC will accept a frame. Sleeps rather
        than spinning — a naked spin here holds the GIL and starves any
        realtime audio callback sharing the process."""
        deadline = time.monotonic() + timeout
        while self.lib.GetStatus(self.dac_num) != 1:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.0005)
        return True

    # ---- safety ----

    def blank(self, timeout=0.25):
        """Extinguish output NOW.

        Writes a dark frame in *repeat* mode, so the DAC keeps emitting
        darkness even if this process dies immediately afterwards. Bypasses
        the render pipeline entirely — it must work when the rest of the app
        is in a bad state. Returns True if the DAC accepted the frame.
        """
        if self._closed:
            return False
        # Best effort on the wait: if the DAC won't report ready we still try
        # the write, because not-writing is the worse failure here.
        self._wait_ready(timeout)
        return self.lib.WriteFrame(self.dac_num, 1000, HELIOS_FLAGS_DEFAULT,
                                   self._blank_frame, 4) == 1

    def stop(self):
        """Blank, then stop and centre output.

        Failures are reported, not swallowed: a blank that silently failed is
        indistinguishable from one that worked, which is exactly the wrong
        property for this call to have.
        """
        ok = False
        try:
            ok = self.blank()
        except Exception as e:
            print(f"[laser] BLANK FAILED: {e} — beam may still be live")
        if not ok:
            print("[laser] WARNING: DAC did not acknowledge the blank frame")
        try:
            self.lib.Stop(self.dac_num)
        except Exception as e:
            print(f"[laser] Stop() failed: {e}")
        return ok

    def close(self):
        if self._closed:
            return
        self.stop()
        time.sleep(0.02)  # let the dark frame clock out before we let go
        self._closed = True
        try:
            self.lib.CloseDevices()
        except Exception as e:
            print(f"[laser] CloseDevices() failed: {e}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class HeliosOutput:
    """LaserOutput adapter over HeliosDAC (see laser_output.LaserOutput).

    paces_loop is True because write_frame() blocks on GetStatus, so the DAC's
    own point clock times the render loop — no sleep needed by the caller.
    """

    name = "helios"
    paces_loop = True

    def __init__(self, dac_num=0, lib_path=None):
        self.dac = HeliosDAC(dac_num, lib_path=lib_path)
        self.num_devices = self.dac.num_devices
        self.last_points = 0

    def write(self, frame, pps):
        self.last_points = len(frame)
        return self.dac.write_frame(frame, pps)

    def blank(self):
        return self.dac.blank()

    def close(self):
        self.dac.close()

    def diagnostics(self):
        """Device readout for the Settings panel.

        The Helios exposes far less than a networked device: no temperature,
        no interlock reporting, no power state. GetStatus is the one live
        signal — it says whether the DAC is ready for another frame, which at
        least distinguishes a working link from a wedged one. Say so plainly
        rather than padding the panel with unknowns.
        """
        rows = [("device", f"Helios DAC #{self.dac.dac_num}", ""),
                ("devices found", str(self.num_devices),
                 "ok" if self.num_devices else "bad")]
        try:
            status = self.dac.lib.GetStatus(self.dac.dac_num)
            rows.append(("status", "ready" if status == 1 else
                         f"busy ({status})", "ok" if status == 1 else "warn"))
        except Exception as e:
            rows.append(("status", f"query failed: {e}", "bad"))
        try:
            ver = self.dac.lib.GetFirmwareVersion(self.dac.dac_num)
            rows.append(("firmware", str(ver), ""))
        except Exception:
            pass          # older SDK builds do not export it; not worth noise
        rows.append(("points last frame", str(self.last_points), ""))
        rows.append(("telemetry", "none — the Helios reports no temperature, "
                     "interlock or power state", ""))
        return rows
