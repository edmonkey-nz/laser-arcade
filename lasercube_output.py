"""
lasercube_output.py — LaserCube / LaserCube Ultra output over the network.

CANONICAL: laser-laser-laser/lasercube_output.py — sync, do not fork.
Depends only on `laser_output`, numpy and the standard library, so it copies
into laser-arcade and promptwaver unchanged. Python 3.9 compatible.

It deliberately knows nothing about this project: no engine, no settings, no
web UI. The arm gate, the brightness ceiling and the watchdog all live
upstream in `laser_output.SafeOutput`, which wraps this the same way it wraps
the Helios backend. Nothing in here can bypass them, and nothing in here
touches the Helios path.

    render loop ── SafeOutput ─┬─ HeliosOutput      (USB, blocking)
       (arm gate, ceiling,     └─ LaserCubeOutput   (UDP, non-blocking)
        watchdog, blank-on-exit)

--------------------------------------------------------------------------
Protocol
--------------------------------------------------------------------------
Reverse-engineered. Sources, in descending order of authority:

  [1] Wickedlasers/libLaserdockCore — ldNetworkHardware{,Manager}.cpp.
      Official, but the wire layer (LaserdockNetworkDevice) is not published,
      so this gives semantics and device telemetry, not byte layouts.
  [2] Wickedlasers/laserdocklib — LaserdockSample.h. Official USB sample
      struct. NOTE it is *not* the network layout (see below).
  [3] s4y's "LaserCube controller" gist and its comment thread — a published
      wire-level description, field-proven by several people, but partly
      guessed: several of its full-info offsets are off by one.
  [4] modulaserapp/laser-dac-rs, src/protocols/lasercube_network/ — a
      maintained Rust implementation with a dedicated protocol module and
      unit tests. **The best source of the four**, and the tiebreaker
      wherever it disagrees with [3]. It resolved the point layout, the
      full-info field offsets, the flags byte, and CMD_SET_RATE.

Every constant here is marked with which source it came from. Anything
unconfirmed is called out in a comment rather than quietly assumed.

Ports (UDP) [3]:
    45456  alive/ping      45457  command      45458  data

Commands [3]:
    0x77  get full info         0x80  set output (0/1)
    0x78  buffer-size responses 0x8a  get ringbuffer free
    0xa9  sample data (data port only)

Commands, continued [4]:
    0x27  alive (alive port)   0x82  set DAC rate (u32 LE)
    0x8d  clear ring buffer    0xa0  set buffer threshold (u32 LE, fw > 1.23)

Data datagram [3][4]:
    byte 0   0xa9
    byte 1   0x00
    byte 2   message counter (wraps at 256)
    byte 3   frame counter   (wraps at 256)
    byte 4+  points, 10 bytes each: '<HHHHH' = x, y, r, g, b

    Values are 12-bit (0..4095) in 16-bit little-endian fields. 140 points per
    datagram keeps it inside a 1500-byte MTU.

Buffer-free ack [4]:  byte 0 = 0x8a, byte 1 = status/sequence,
                      bytes 2-3 = free space, u16 LE.

**The network point layout is NOT the USB one.** laserdocklib's
`LaserdockSample` is 8 bytes — {rg, b, x, y}, colour packed 8 bits per channel,
colour *first*. The network firmware takes 10 bytes — {x, y, r, g, b} at 12
bits, position first, confirmed independently by [3] and [4]. Both are correct
for their own transport. Getting this backwards produces a garbage frame at
full power, so `pack_points()` is isolated and unit-testable, and
`--lasercube-point-order` can swap it on the day without a code change.

**Y may need inverting.** [4] applies `coord_to_u12_inverted` to y but not x,
i.e. the LaserCube's Y axis runs opposite to its host convention. Whether that
matches *this* project's convention is a question for the hardware, not the
source — so it is not baked in here. The existing `--hw-flip-y` / HW FLIP Y
control already covers it, live, without a restart.

**Colour is effectively 8-bit regardless.** The wire carries 12-bit fields,
but the hardware does 16.7 million colours (X-Laser spec sheet) — 8 bits per
channel. Sending 12-bit values is right; expecting 12 bits of visible
gradation is not. There is no colour-depth win over the Helios path.

**There is no power/brightness command in the protocol.** The official device
API exposes output enable/disable, DAC rate, ring-buffer queries and telemetry
— and nothing that caps power. Per-point RGB is the only control. That makes
`SafeOutput`'s ceiling the only brightness limiter that exists, so it is
load-bearing rather than a convenience.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import numpy as np

# ---------------------------------------------------------------- protocol

ALIVE_PORT = 45456
CMD_PORT = 45457
DATA_PORT = 45458

CMD_ALIVE = 0x27
CMD_GET_FULL_INFO = 0x77
CMD_ENABLE_BUFFER_SIZE_RESPONSE_ON_DATA = 0x78
CMD_SET_OUTPUT = 0x80
CMD_SET_RATE = 0x82
CMD_CLEAR_RINGBUFFER = 0x8D
CMD_GET_RINGBUFFER_EMPTY_SAMPLE_COUNT = 0x8A
CMD_SET_DAC_BUFFER_THRESHOLD = 0xA0
CMD_SAMPLE_DATA = 0xA9

MAX_POINTS_PER_DATAGRAM = 140          # 140*10 + 4 = 1404 bytes, under MTU
POINT_SIZE_BYTES = 10
DAC_MAX = 4095                          # 12-bit, position and colour alike
XY_CENTRE = 2047                        # [4]; note 2047, not 2048

#: Conservative default. The Ultra's scanners are rated 35K pps at 7°, but
#: starting at the maximum on an unverified transport is how you find out
#: what a scanner failure looks like.
DEFAULT_PPS = 20000

#: X-Laser LaserCube Ultra 7.5W: 455 nm blue (4 W), 525 nm green (2 W),
#: 638 nm red (1.5 W). Eyewear must be rated for all three.
WAVELENGTHS_NM = (455, 525, 638)

#: FAQ says the unit shuts itself down above 40 °C. Warn well before that.
TEMP_WARN_C = 35


def scale_8_to_12(v):
    """0..255 -> 0..4095, preserving full scale at both ends.

    `v << 4` alone would top out at 4080, so full white would not be full
    white. Replicating the high nibble into the low bits maps 255 -> 4095
    exactly, and 0 -> 0.
    """
    v = np.asarray(v, dtype=np.uint16)
    return (v << 4) | (v >> 4)


def pack_points(frame, order="xyrgb"):
    """Pack an (N,6) int32 interchange frame into the wire format.

    frame columns are x, y, r, g, b, i — 12-bit position, 8-bit colour, the
    format every backend in this project takes (see laser_output).

    The `i` column is dropped: the LaserCube has no separate intensity
    channel, brightness lives entirely in RGB. Callers whose `i` carries the
    blanking decision must have already applied it to RGB — this project's
    engine does (`shapes.py` computes both from the same `lit` mask), and
    SafeOutput's arm gate zeroes all four columns together.

    Built as one numpy structured array and taken to bytes in a single call.
    A per-point Python loop here is the exact mistake that cost promptwaver
    431 ms/frame against a 22 ms budget.
    """
    n = len(frame)
    out = np.empty(n, dtype=np.dtype([('a', '<u2'), ('b', '<u2'), ('c', '<u2'),
                                      ('d', '<u2'), ('e', '<u2')]))
    x = np.clip(frame[:, 0], 0, DAC_MAX).astype(np.uint16)
    y = np.clip(frame[:, 1], 0, DAC_MAX).astype(np.uint16)
    r = scale_8_to_12(np.clip(frame[:, 2], 0, 255))
    g = scale_8_to_12(np.clip(frame[:, 3], 0, 255))
    b = scale_8_to_12(np.clip(frame[:, 4], 0, 255))
    if order == "xyrgb":                      # [3], the published layout
        out['a'], out['b'], out['c'], out['d'], out['e'] = x, y, r, g, b
    elif order == "rgbxy":                    # fallback if [2]'s order wins
        out['a'], out['b'], out['c'], out['d'], out['e'] = r, g, b, x, y
    else:
        raise ValueError(f"unknown point order {order!r}")
    return out.tobytes()


def parse_full_info(msg):
    """Decode the 64-byte full-info response.

    Offsets are taken from [4], which parses every field and is the only
    source that documents the flags byte. Deliberately lenient: a short or
    malformed packet yields {} rather than raising, because this runs on the
    sender thread and telemetry must never be able to take output down.

    The flags byte carries `interlock_enabled`, `temperature_warning` and
    `over_temperature`. Those are **reports from the device about its own
    hardware interlock** — reading them lets us refuse to enable output, which
    is worth doing, but the interlock itself is the safety device, not our
    reading of it.
    """
    try:
        if len(msg) < 64 or msg[0] != CMD_GET_FULL_INFO or msg[1] != 0:
            return {}
        if msg[2] != 0:                     # payload version we don't know
            return {}
        fw_major, fw_minor, flags = msg[3], msg[4], msg[5]
        # Firmware 0.13 moved the flag bits. [4]
        if fw_major > 0 or fw_minor >= 13:
            interlock = bool(flags & 0x02)
            temp_warn = bool(flags & 0x04)
            over_temp = bool(flags & 0x08)
            pkt_errors = (flags >> 4) & 0x0F
        else:
            interlock = bool(flags & 0x08)
            temp_warn = bool(flags & 0x10)
            over_temp = bool(flags & 0x20)
            pkt_errors = 0
        point_rate, max_point_rate = struct.unpack_from("<II", msg, 10)
        buf_free, buf_max = struct.unpack_from("<HH", msg, 19)
        battery = msg[23]
        temp_c = struct.unpack_from("<b", msg, 24)[0]     # signed
        conn = msg[25]
        name = msg[38:64].split(b"\0")[0].decode("utf-8", "replace")
        return {
            "fw": f"{fw_major}.{fw_minor}",
            "output_on": bool(flags & 0x01),
            "interlock": interlock,
            "temp_warning": temp_warn,
            "over_temperature": over_temp,
            "packet_errors": pkt_errors,
            "dac_rate": point_rate,
            "max_dac_rate": max_point_rate,
            "buffer_free": buf_free,
            "buffer_size": buf_max,
            # 255 means running on mains, not a 255% battery. [4]
            "battery_pct": None if battery == 255 else battery,
            "mains": battery == 255,
            "temperature_c": temp_c,
            "connection": {0: "wifi", 1: "ethernet"}.get(conn, f"type{conn}"),
            "serial": msg[26:32].hex().upper(),
            "model": msg[37],
            "model_name": name,
        }
    except Exception:
        return {}


def discover(timeout=2.0, broadcast="255.255.255.255"):
    """Broadcast a full-info request and return [(ip, info), ...].

    Emits nothing: this is a status query, and the device's output state is
    untouched by it.
    """
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.25)
    try:
        s.bind(("", CMD_PORT))
    except OSError:
        s.bind(("", 0))          # port busy: replies still arrive on the ephemeral port
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                s.sendto(bytes([CMD_GET_FULL_INFO]), (broadcast, CMD_PORT))
            except OSError:
                pass
            try:
                while True:
                    msg, addr = s.recvfrom(1024)
                    info = parse_full_info(msg)
                    if info and addr[0] not in found:
                        found[addr[0]] = info
            except socket.timeout:
                pass
    finally:
        s.close()
    return sorted(found.items())


# ---------------------------------------------------------------- backend

class LaserCubeOutput:
    """LaserOutput backend for a networked LaserCube.

    `write()` never touches a socket. It swaps the frame into a lock-guarded
    slot and returns — the same latest-wins handoff `WebUI.publish` uses. A
    daemon sender thread streams against the device's reported free buffer
    space, so the render thread cannot be blocked by the network. That matters
    beyond this project: promptwaver runs a realtime audio callback in the same
    process, and a blocked render thread there becomes an audible xrun.

    Because the sender is independent, it also keeps the device buffer topped
    up between frames rather than emitting one burst per frame, which is what
    causes the visible inter-frame pauses people report.
    """

    name = "lasercube"
    #: UDP writes do not block, so the caller must keep its own frame clock.
    paces_loop = False

    def __init__(self, ip=None, pps=DEFAULT_PPS, point_order="xyrgb",
                 dry_run=False, discover_timeout=3.0):
        self.last_points = 0
        self.pps = int(pps)
        self.point_order = point_order
        self.dry_run = bool(dry_run)
        self.info = {}
        self.stats = {"sent": 0, "dropped": 0, "errors": 0, "buffer_free": 0}

        if ip is None:
            found = discover(timeout=discover_timeout)
            if not found:
                raise RuntimeError(
                    "no LaserCube found on the network. Check it is powered, "
                    "on the same subnet, and preferably on Ethernet rather "
                    "than WiFi.")
            ip, self.info = found[0]
            if len(found) > 1:
                print(f"[lasercube] {len(found)} devices found, using {ip}. "
                      f"Pass --lasercube-ip to choose.")
        self.ip = ip

        # Two sockets, deliberately. The sender thread owns _sock (point data
        # and the 10 Hz buffer-free poll); the control path owns _ctl
        # (info queries, output enable/disable, DAC rate) and is the only
        # thing that does a request/response round trip. Sharing one socket
        # across both would let the sender thread swallow the reply that
        # enable() is waiting on, and the failure mode there is believing the
        # laser is off when it is on.
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.2)
        self._ctl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._ctl.settimeout(0.25)
        self._ctl_lock = threading.Lock()
        self._lock = threading.Lock()
        self._latest = None            # packed bytes of the current frame
        self._latest_n = 0
        self._enabled = False
        self._closed = False
        self._msg_num = 0
        self._frame_num = 0
        self._buffer_free = 0
        self._last_info = 0.0
        self._last_heard = time.monotonic()
        self._silent = False

        if not self.info:
            self.info = self._query_info() or {}
        self._report()

        self._stop = threading.Event()
        self._sender = threading.Thread(target=self._send_loop, daemon=True,
                                        name="lasercube-sender")
        self._sender.start()

    # ---- device I/O ----

    def _cmd(self, *payload):
        """Fire a command at the command port. Never raises."""
        if self.dry_run:
            return True
        try:
            with self._ctl_lock:
                self._ctl.sendto(bytes(payload), (self.ip, CMD_PORT))
            return True
        except OSError as e:
            self.stats["errors"] += 1
            print(f"[lasercube] command failed: {e}")
            return False

    def _query_info(self):
        """Round-trip a full-info request. Control socket only."""
        if self.dry_run:
            return {"connection": "DRY RUN — nothing transmitted",
                    "max_dac_rate": self.pps, "buffer_size": 6000}
        with self._ctl_lock:
            try:
                self._ctl.sendto(bytes([CMD_GET_FULL_INFO]),
                                 (self.ip, CMD_PORT))
            except OSError:
                return {}
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                try:
                    msg, _ = self._ctl.recvfrom(1024)
                except (socket.timeout, OSError):
                    return {}
                info = parse_full_info(msg)
                if info:                 # skip acks and anything unparseable
                    return info
        return {}

    def _report(self):
        i = self.info
        if not i:
            print(f"[lasercube] {self.ip} — no info response "
                  "(continuing; telemetry unavailable)")
            return
        # Only report fields the device actually gave us — a line full of
        # "?" and "None" reads like a fault when it is just a dry run.
        bits = [i.get("model_name") or (f"model {i['model']}"
                                        if i.get("model") else None),
                f"fw {i['fw']}" if i.get("fw") else None,
                i.get("connection"),
                "mains" if i.get("mains") else
                (f"battery {i['battery_pct']}%"
                 if i.get("battery_pct") is not None else None),
                f"{i['temperature_c']}°C"
                if i.get("temperature_c") is not None else None,
                f"max {i['max_dac_rate']} pps" if i.get("max_dac_rate") else None]
        print(f"[lasercube] {self.ip} — " +
              ", ".join(b for b in bits if b))
        if i.get("connection") == "wifi":
            print("[lasercube] WARNING: on WiFi. Buffer levels are unstable "
                  "over WiFi — use the Ethernet adapter for live output.")
        temp = i.get("temperature_c")
        if i.get("over_temperature"):
            print(f"[lasercube] OVER TEMPERATURE ({temp}°C) — output will be "
                  "refused until it cools.")
        elif isinstance(temp, int) and temp >= TEMP_WARN_C:
            print(f"[lasercube] WARNING: {temp}°C — the unit shuts down "
                  "above 40°C.")
        if i.get("packet_errors"):
            print(f"[lasercube] device reports {i['packet_errors']} packet "
                  "errors — check cabling if this climbs.")
        if i.get("output_on"):
            print("[lasercube] NOTE: device reports output already enabled; "
                  "disabling until armed.")
            self._set_output(False)

    def _set_output(self, on):
        ok = self._cmd(CMD_SET_OUTPUT, 1 if on else 0)
        if ok:
            self._enabled = bool(on)
        return ok

    def set_rate(self, pps):
        """Tell the device its point clock.

        Without this the device keeps whatever rate it was last left at, and
        every shape comes out at the wrong scan speed — which on a laser is
        not merely a visual bug: too slow means more dwell per point.
        """
        pps = int(pps)
        cap = self.info.get("max_dac_rate") or 0
        if cap and pps > cap:
            print(f"[lasercube] {pps} pps exceeds the device maximum {cap} "
                  "— clamping")
            pps = cap
        self.pps = pps
        return self._cmd(CMD_SET_RATE, *struct.pack("<I", pps))

    # ---- LaserOutput protocol ----

    def write(self, frame, pps):
        """Hand the sender thread a new frame. Returns immediately.

        Always returns True: with a fire-and-forget transport there is no
        per-frame acknowledgement to report, and a False here would make the
        caller print a dropped-frame warning it cannot act on. Real drops are
        counted in self.stats.
        """
        if self._closed:
            return False
        if pps and int(pps) != self.pps:
            self.pps = int(pps)
        self.last_points = len(frame)
        packed = pack_points(frame, self.point_order)
        with self._lock:
            self._latest = packed
            self._latest_n = len(frame)
            self._frame_num = (self._frame_num + 1) & 0xFF
        return True

    def blank(self):
        """Extinguish output now.

        Belt and braces, in that order: replace the streamed frame with
        darkness *first* so whatever the device is currently scanning goes
        dark even if the command is lost, then disable output at the device.
        """
        dark = np.zeros((16, 6), dtype=np.int32)
        dark[:, 0] = dark[:, 1] = XY_CENTRE     # parked at centre, beam off
        with self._lock:
            self._latest = pack_points(dark, self.point_order)
            self._latest_n = len(dark)
        ok = self._set_output(False)
        return ok

    def close(self):
        if self._closed:
            return
        self.blank()
        time.sleep(0.05)              # let the dark frame and the command land
        self._closed = True
        self._stop.set()
        self._sender.join(timeout=1.0)
        self._set_output(False)       # once more, after the sender has stopped
        for s in (self._sock, self._ctl):
            try:
                s.close()
            except OSError:
                pass

    # ---- arming ----

    def enable(self):
        """Enable device output. Called by SafeOutput when armed, never on
        construction — §4.5, nothing emits because an object was created.

        Refuses while the device reports over-temperature. That is not us
        acting as a safety interlock — the device has its own, and shuts down
        above 40 °C on its own account. It is simply that asking a projector
        to fire when it has already told us it is too hot is not a request
        worth forwarding.
        """
        if self.info.get("over_temperature"):
            print(f"[lasercube] REFUSING to enable output: device reports "
                  f"over-temperature ({self.info.get('temperature_c')}°C)")
            return False
        if self.info.get("interlock") is False:
            print("[lasercube] NOTE: device reports its interlock is not "
                  "engaged; enabling anyway, the device decides.")
        self.set_rate(self.pps)
        if not self._set_output(True):
            return False

        # UDP gives no acknowledgement, so a successful sendto() proves only
        # that the datagram left this machine. Read the state back and believe
        # the device, not ourselves — otherwise a refused or lost enable shows
        # up in the UI as a live laser.
        if self.dry_run:
            return True
        for _ in range(3):
            info = self._query_info()
            if info:
                self.info.update(info)
                if info.get("output_on"):
                    return True
            time.sleep(0.05)
        print("[lasercube] device did not confirm output enabled")
        return False

    def disable(self):
        return self._set_output(False)

    # ---- sender thread ----

    def _send_loop(self):
        """Stream the current frame, paced to the DAC rate.

        Two signals govern this, and both are needed:

        * **Time.** The device consumes exactly `pps` points per second, so
          that is the rate we feed it. Without this the loop simply spins and
          oversends by an order of magnitude — measured at ~320 frames/s
          against a 31 fps render loop before pacing was added.
        * **`rx_buffer_free`.** A brake, polled at 10 Hz. Time-pacing alone
          drifts against the device's real clock; the buffer level is the only
          ground truth, and ignoring it is how you overrun the ring buffer.

        Re-sending the current frame when the render loop has not supplied a
        new one is deliberate — it keeps the device buffer topped up, which is
        what avoids the inter-frame pauses people report (§5.2). Drops rather
        than blocks (§5.1): a dropped frame is a visual hiccup, a blocked
        render thread cascades into the audio thread.
        """
        self._credit = 0.0
        self._last_tick = time.monotonic()
        while not self._stop.wait(0.002):
            try:
                self._pump()
            except Exception as e:
                self.stats["errors"] += 1
                print(f"[lasercube] sender error: {e}")
                time.sleep(0.05)

    def _pump(self):
        with self._lock:
            packed, n = self._latest, self._latest_n
        if not packed or n <= 0:
            return

        now = time.monotonic()

        # Poll the ring buffer at 10 Hz. Cheap (a 1-byte datagram) and it is
        # the only real feedback we get.
        if now - self._last_info > 0.1:
            self._last_info = now
            if not self.dry_run:
                try:
                    self._sock.sendto(
                        bytes([CMD_GET_RINGBUFFER_EMPTY_SAMPLE_COUNT]),
                        (self.ip, CMD_PORT))
                except OSError:
                    pass
            self._drain_replies()

        # A device that has stopped answering is a disconnect — an unplugged
        # cable, a crashed unit, a network drop. Keep sending: the datagrams
        # are harmless if nothing is listening, and if it is a transient the
        # stream resumes without a visible gap. What we must not do is claim
        # the buffer state we last saw is still true, so stop applying stale
        # backpressure and let time-pacing alone govern.
        if not self.dry_run:
            silent = (now - self._last_heard) > 2.0
            if silent != self._silent:
                self._silent = silent
                print("[lasercube] device stopped responding — check the "
                      "cable" if silent else "[lasercube] device responding "
                      "again")
            if silent:
                self._buffer_free = 0

        # Credit accrues at the DAC rate: the points the device has consumed
        # since we last looked, and therefore the room it has made for us.
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        self._credit = min(self._credit + dt * self.pps, 2.0 * n)
        if self._credit < n:
            return                      # not a drop — just not our turn yet

        # Backpressure brake. A device that has told us its buffer is short
        # gets believed; one that has told us nothing is paced on time alone.
        if self._buffer_free and self._buffer_free < n:
            self.stats["dropped"] += 1
            self._credit = 0.0          # resync rather than bursting later
            return

        if not self.dry_run:
            frame_num = self._frame_num
            for off in range(0, n, MAX_POINTS_PER_DATAGRAM):
                chunk = packed[off * 10:(off + MAX_POINTS_PER_DATAGRAM) * 10]
                self._msg_num = (self._msg_num + 1) & 0xFF
                head = bytes([CMD_SAMPLE_DATA, 0x00, self._msg_num, frame_num])
                try:
                    self._sock.sendto(head + chunk, (self.ip, DATA_PORT))
                except OSError as e:
                    self.stats["errors"] += 1
                    print(f"[lasercube] send failed: {e}")
                    return
        self.stats["sent"] += 1
        self._credit -= n
        self._buffer_free = max(0, self._buffer_free - n)

    # ---- diagnostics ----

    def diagnostics(self):
        """Live device readout for the Settings panel.

        Re-queries the device rather than returning cached values, so pressing
        TEST actually tests something. Emits nothing — a full-info request
        does not touch the output state.

        Returns [(label, value, severity), ...] with severity in
        "ok"/"warn"/"bad"/"" so the UI can colour it without knowing what any
        of these fields mean.
        """
        info = self._query_info()
        if info:
            self.info.update(info)
        i = self.info
        alive = bool(info)
        rows = [("connection",
                 f"{self.ip} ({i.get('connection','?')})",
                 "ok" if alive else "bad"),
                ("responding", "yes" if alive else "NO — check cable/power",
                 "ok" if alive else "bad")]
        if not alive:
            rows.append(("note", "values below are the last seen", "warn"))
        if i.get("model_name"):
            rows.append(("model", i["model_name"], ""))
        if i.get("serial"):
            rows.append(("serial", i["serial"], ""))
        rows.append(("firmware", i.get("fw", "?"), ""))

        temp = i.get("temperature_c")
        if temp is not None:
            sev = ("bad" if i.get("over_temperature")
                   else "warn" if (i.get("temp_warning") or temp >= TEMP_WARN_C)
                   else "ok")
            note = " — OVER TEMP" if i.get("over_temperature") else ""
            rows.append(("temperature", f"{temp}°C{note}", sev))

        if i.get("interlock") is not None:
            rows.append(("interlock",
                         "engaged" if i["interlock"] else "NOT engaged",
                         "ok" if i["interlock"] else "warn"))

        rows.append(("output", "ENABLED" if i.get("output_on") else "off",
                     "warn" if i.get("output_on") else "ok"))
        rows.append(("power", "mains" if i.get("mains")
                     else f"battery {i.get('battery_pct','?')}%",
                     "warn" if (not i.get("mains") and
                                (i.get("battery_pct") or 100) < 20) else ""))
        rows.append(("scan rate",
                     f"{i.get('dac_rate','?')} pps "
                     f"(max {i.get('max_dac_rate','?')})", ""))
        rows.append(("device buffer",
                     f"{self._buffer_free} free of {i.get('buffer_size','?')}",
                     ""))

        errs = i.get("packet_errors") or 0
        rows.append(("packet errors", str(errs), "warn" if errs else "ok"))

        s = self.stats
        total = s["sent"] + s["dropped"]
        pct = (100.0 * s["dropped"] / total) if total else 0.0
        rows.append(("frames sent", str(s["sent"]), ""))
        rows.append(("frames dropped", f"{s['dropped']} ({pct:.1f}%)",
                     "warn" if pct > 5 else "ok"))
        rows.append(("send errors", str(s["errors"]),
                     "bad" if s["errors"] else "ok"))
        if self.dry_run:
            rows.insert(0, ("MODE", "DRY RUN — nothing is transmitted", "warn"))
        return rows

    def _drain_replies(self):
        """Read whatever the device has sent us without blocking."""
        self._sock.setblocking(False)
        try:
            while True:
                try:
                    msg, _ = self._sock.recvfrom(1024)
                except (BlockingIOError, socket.timeout, OSError):
                    return
                if len(msg) >= 4 and msg[0] == CMD_GET_RINGBUFFER_EMPTY_SAMPLE_COUNT:
                    # [0]=0x8a [1]=status/seq [2:4]=free space u16 LE
                    self._buffer_free = struct.unpack_from('<H', msg, 2)[0]
                    self.stats["buffer_free"] = self._buffer_free
                    self._last_heard = time.monotonic()
                elif len(msg) >= 64:
                    info = parse_full_info(msg)
                    if info:
                        self.info.update(info)
                        self._last_heard = time.monotonic()
                        if "buffer_free" in info:
                            self._buffer_free = info["buffer_free"]
        finally:
            self._sock.setblocking(True)
            self._sock.settimeout(0.2)
