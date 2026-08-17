# LaserCubeOutput — network output backend spec

A reusable laser output backend targeting the **LaserCube / LaserCube Ultra**
over its **network (Ethernet/WiFi) protocol**, intended to be shared across
multiple projects (LaserFlow/AuroraVJ, Laser Arcade, Laser! Laser! Laser!)
rather than reimplemented per project.

> ⚠️ **Built in 1.6.0, but not yet run against real hardware.** The protocol
> below is resolved and cited against reference implementations (§2.2), and
> exercised end-to-end against `scripts/lasercube_sim.py` — but agreement
> between sources is not verification. Confirm on the device before trusting
> it. Getting this wrong on a 7.5W Class 4 device is a safety issue, not a
> bug. The one genuinely open question is stream-underrun behaviour (§4.3).

---

## 1. Why network rather than ILDA or USB

| Path | Pros | Cons |
|---|---|---|
| Helios DAC → ILDA in | Already built; well-trodden | Analog; **ghost-beam risk** from DAC offset vs the projector's mute threshold; 8-bit colour; extra hardware + DB-25; DAC keeps emitting if the host app dies |
| USB direct (`laserdocklib`) | Official Wicked Lasers library | C++ → needs `.so` build, ctypes wrapper, libusb, udev rules — same toolchain pain as the Helios build |
| **Network (UDP)** ✅ | **Pure Python** (`socket` + `struct`); digital end-to-end so *zero really is zero*; explicit buffer-level flow control; rich telemetry (temperature, interlock, packet errors) | Reverse-engineered protocol; stream-underrun behaviour must be verified; WiFi jitter (use Ethernet) |

> **Correction:** earlier revisions claimed native 12-bit colour as an
> advantage over the Helios path. That is wrong. The wire format carries
> 12-bit colour fields, but the hardware does 16.7 million colours — 8 bits
> per channel, the same as the Helios. There is no colour-depth win. The real
> wins are digital blanking, no C toolchain, and the telemetry.

**Decision: network over Ethernet.** The decisive advantages are (a) no C
toolchain — significant given repeated PortAudio/pyo/Helios build failures on
the target machine, and (b) digital blanking eliminates the analog ghost-beam
class of failure entirely.

Use the **USB-C-to-Ethernet adapter**, not WiFi. Community reports describe
unstable buffer levels and dropouts over WiFi; realtime laser output cannot
tolerate that.

---

## 2. Protocol summary

### 2.1 What is reasonably established

**Point format** — 5 × little-endian `uint16`:

```python
struct.pack('<HHHHH', x, y, r, g, b)
```

- **12-bit range, 0–4095**, for both position and each colour channel.
- The colour *fields* are 12-bit, but the hardware modulates 16.7 million
  colours — 8 bits per channel. Send 12-bit values; do not expect 12 bits of
  visible gradation. There is no colour-depth win over the Helios path.

**Flow control** — the device reports an `rx_buffer_free` value (points of free
space remaining). The sender must read this and throttle: send only up to the
free space, never blindly. Community reports show the buffer sitting around
~1000 points and occasionally jumping to ~6000 — treat the number as
authoritative per-read rather than assuming a fixed capacity.

**Transport** — UDP, with a separate **command** channel and **data** channel.

### 2.2 Resolved — implemented in 1.6.0

All but one of the original unknowns are settled. The authority is
**`modulaserapp/laser-dac-rs`**, `src/protocols/lasercube_network/` — a
maintained Rust implementation with a dedicated protocol module and unit
tests. Where it disagrees with the s4y gist, it wins; the gist's full-info
offsets are off by one in several places.

The constants live in `lasercube_output.py`, which cites its source per field.

- [x] **Ports** — 45456 alive, 45457 command, 45458 data (UDP).
- [x] **Discovery** — broadcast `0x77` to `255.255.255.255:45457`; devices
      reply with the 64-byte full-info packet.
- [x] **Opcodes** — `0x27` alive, `0x77` get-full-info, `0x78` buffer-size
      responses, `0x80` set-output, `0x82` set-DAC-rate (u32 LE), `0x8a`
      get-ringbuffer-free, `0x8d` clear-ringbuffer, `0xa0` buffer threshold
      (fw > 1.23), `0xa9` sample data.
- [x] **Framing** — data datagram is `[0xa9, 0x00, msg#, frame#]` then points.
      Buffer-free ack is `[0x8a, status, free_u16_le]`.
- [x] **Points** — 10 bytes, `<HHHHH` = x, y, r, g, b, 12-bit in 16-bit LE
      fields. **Not** the USB `LaserdockSample` layout (8 bytes, {rg,b,x,y}).
- [x] **Max per datagram** — 140 points (1404 bytes, inside a 1500 MTU).
- [x] **Coordinates** — 0..4095, origin at a corner, centre 2047. The Rust
      implementation inverts Y relative to its host convention; whether that
      matches ours is a hardware question, so `--hw-flip-y` covers it live.
- [x] **Enable output** — yes, `0x80 01` is required, and it is not
      acknowledged, so `enable()` reads the state back before believing it.
- [ ] **Stream underrun behaviour** — still open. Only hardware can answer;
      see §4.3.

Two findings that change the design, both documented in §1 and §4.4:

- **There is no power/brightness command.** The device API exposes output
  enable/disable, DAC rate, buffer queries and telemetry, and nothing that
  caps power. Per-point RGB is the only control there is.
- **Colour is effectively 8-bit.** The wire carries 12-bit fields, but the
  hardware does 16.7 million colours — 8 bits per channel.

### 2.3 Telemetry the device reports

The full-info response carries more than the gist suggested, including a flags
byte at offset 5 (bit layout changed at firmware 0.13):

| Field | Use |
|---|---|
| `output_enabled` | verify `enable()` actually took |
| `interlock_enabled` | the device's own interlock state |
| `temperature_warning`, `over_temperature` | thermal state; we refuse to enable output while over-temperature |
| `packet_errors` (4 bits) | cabling health |
| `point_rate`, `point_rate_max` | clamp requested PPS to the device maximum |
| `buffer_free`, `buffer_max` | flow control |
| `battery_percent` | **255 means mains-powered**, not 255% |
| `temperature_c` | **signed** int8 |
| serial, model number, model name, firmware | identification |

All of it is surfaced by **TEST DEVICE** in Settings → Laser output.

---

## 3. Interface design (the reusable part)

The point of this spec is a backend that drops into any of the laser projects.
All of them already produce a list of paths/points per frame, so the shared
contract should be small and output-agnostic.

```python
class LaserOutput(Protocol):
    """Minimal contract every backend implements (Helios, LaserCube, Null)."""

    name: str
    last_points: int

    def write(self, frame, pps: int) -> None:
        """Render one frame. Must be safe to call at frame rate."""

    def blank(self) -> None:
        """Explicitly extinguish output NOW. Must bypass all scene/colour
        logic and any brightness state — this is the 'stop emitting' path
        and must work even if the rest of the app is in a bad state."""

    def close(self) -> None:
        """Release the device. MUST blank first (see §4)."""
```

~~Keep frame geometry in normalised float coordinates (-1..1) in shared
code.~~ **Not adopted** — see *Frame interchange* below. All three projects
already produce or consume the Helios point layout, so converting to float and
back per frame would rewrite two working pipelines for no gain. The protocol
as built also carries `paces_loop` and optional `enable()`/`disable()`, which
this sketch predates.

### Actual layout (as built)

An installable package was considered and **rejected**: none of the three
projects can install one without breaking their builds (two freeze with
PyInstaller, one has no build step at all), and it conflicts with this repo's
flat-root and no-build-step constraints. Instead, flat single files copied
between projects, each marked `# CANONICAL: laser-laser-laser/<name>`:

```
laser_output.py       # LaserOutput protocol, NullOutput, SafeOutput
                      # (arm gate, brightness ceiling, watchdog,
                      # blank-on-exit), from_normalised()
helios.py             # ctypes backend + HeliosOutput adapter
lasercube_output.py   # this spec — not yet written
```

`laser_output.py` imports nothing from any host project and stays Python 3.9
compatible, because laser-arcade's floor is 3.9.

### Frame interchange

The normalised-float contract above was **not** adopted. All three projects
already produce or consume the Helios point layout, so the shared contract is
the concrete one: **numpy `(N,6)` int32 — `x,y` 0..4095, `r,g,b,i` 0..255.**
Float-native callers convert at their own edge with
`laser_output.from_normalised()`. LaserCube upscales 8→12-bit colour inside
its own backend (`v << 4 | v >> 4`).

---

## 4. Safety requirements (mandatory)

These are **requirements, not suggestions**. Target hardware is a 7.5W Class 4
laser: capable of instant permanent eye injury including from diffuse
reflections, plus skin burns and ignition.

> **Status:** §4.1–4.2 and §4.4–4.5 are **implemented for the Helios path in
> 1.5.0**, in `laser_output.py` (`SafeOutput`, `install_panic_handlers`) and
> `helios.py` (`blank()`). The LaserCube backend must reuse `SafeOutput`
> rather than reimplementing any of it. §4.3 remains open — it can only be
> answered on hardware.

### 4.1 Blank on every exit path

The Helios backend used to have this bug — `close()` released the device
without a genuine blank, and there was no SIGTERM handler at all, so `kill`
skipped teardown entirely. **Fixed in 1.5.0. Do not reintroduce it.**
Required:

- `close()` blanks before releasing.
- `SIGINT` (Ctrl+C) and `SIGTERM` handlers blank before exit.
- `atexit` handler as a backstop.
- Wrap the render loop so an unhandled exception blanks before propagating.
- Use a context manager (`__enter__`/`__exit__`) so `with LaserCubeOutput(...)`
  blanks on any exit path including exceptions.

### 4.2 Watchdog

If the render loop stops feeding frames (GC pause, deadlock, logic bug, network
stall), output must not continue indefinitely.

- A watchdog thread tracks time since last `write()`.
- If it exceeds a threshold (start conservative — **250 ms**), send blank
  frames continuously until feeding resumes.
- The watchdog must run independently of the render loop it's watching —
  a stalled render thread must not stall the watchdog.

### 4.3 Stream-underrun behaviour **[VERIFY ON HARDWARE]**

**This is the single most important unknown.** If the sender stalls, does the
LaserCube:

- (a) blank automatically — ideal; or
- (b) repeat the last buffered points — acceptable (still scanning); or
- (c) hold the final point — **hazardous**: a stationary 7.5W beam is a burn
  and fire risk, not just a visual artifact.

**Verify this at minimum power, aimed into a beam dump, before any other
testing.** If the answer is (c), the watchdog in §4.2 becomes load-bearing
safety-critical code and must be tested accordingly.

For reference, **the Helios is category (b)**: `WriteFrame()` without
`HELIOS_FLAG_SINGLE_MODE` repeats the frame until the next one arrives. Still
scanning, so not a burn risk — but the app can die with an image still
painted, which is exactly why `blank()` writes its dark frame in repeat mode
rather than merely calling `Stop()`.

### 4.4 Brightness cap

- A global cap applied at the **final packing stage**, after all scene and
  colour logic, so nothing upstream can bypass it.
- **Default to a low value (≈5%) for a new backend**, not full power.
- Hard-clamp so no scene/generated content can exceed the configured ceiling.
- Document explicitly: **this is a creative limiter, not a safety interlock.**
  It cannot protect against a crash, a stuck buffer, or a protocol bug. Only
  the hardware key switch, aperture shutter, interlock loop and Remote Stop do
  that.

### 4.5 Sane defaults on construction

- Output **disabled** until explicitly enabled — never emit on object creation.
- Conservative default PPS; do not assume the device's maximum.
- Explicit `enable()` call required, mirroring the master Start/Stop gate.

---

## 5. Implementation notes

### 5.1 Flow control loop

```
read rx_buffer_free
if free < len(points_to_send):
    send only what fits, or skip this frame (prefer skipping a frame
    over blocking the render thread)
send points
```

Never block the render thread waiting for buffer space — drop the frame
instead. A dropped frame is a visual hiccup; a blocked render thread cascades
into the audio thread (see LaserFlow's history of exactly this class of bug).

### 5.2 Avoid inter-frame gaps

Community reports note visible pauses when frames arrive with gaps between
them. Prefer keeping the device buffer topped up over sending discrete
frame-sized bursts — send continuously against `rx_buffer_free` rather than
waiting for a frame boundary.

### 5.3 Performance

Pack with numpy, not per-point Python. LaserFlow already learned this the hard
way: a per-point Python loop in the planner took **431 ms/frame against a 22 ms
budget** and starved the audio thread. Build the whole frame as a numpy
structured array and take `.tobytes()` once.

### 5.4 Dry-run mode

Support `--output lasercube --dry-run`, which does all packing and rate control
but sends to a local socket (or `/dev/null`). This allows validating framing,
throughput and the watchdog **with zero photons emitted**. Use it for all
development and CI.

---

## 6. Bring-up procedure

Do these **in order**. Do not skip ahead.

1. **Dry run.** Verify packet framing, sizes, rates, and that the watchdog
   fires on a simulated stall. No hardware connected.
2. **Loopback/inspection.** Optionally confirm packet bytes against a reference
   implementation's output for the same input.
3. **Hardware, output disabled.** Connect over Ethernet, confirm discovery,
   device-info response and `rx_buffer_free` reads. Confirm no emission.
4. **First light — minimum power, beam dump, eyewear, Remote Stop in hand.**
   - Eyewear rated for **all three wavelengths**. For the LaserCube Ultra
     7.5W that is **455 nm blue (4 W), 525 nm green (2 W), 638 nm red
     (1.5 W)** — per the X-Laser spec sheet, `docs/laser-units/`. (Earlier
     revisions of this document said 445/520 nm. They were wrong; check the
     figures against your own unit's plate rather than trusting either.)
   - Aim into a dump or matte dark surface. Never toward eyes, windows,
     reflective surfaces, or skyward.
   - Verify: a blanked frame is **genuinely dark** (this is the check the ILDA
     path fails).
5. **Underrun test (§4.3).** Deliberately stall the sender and observe. This
   determines whether the watchdog is load-bearing.
6. **Only then** raise power incrementally.

---

## 7. Open questions

- [x] Does the unit expose a **max-power/brightness limit over the network
      protocol**? **No.** The device API has output enable/disable, DAC rate,
      buffer queries and telemetry — no power cap. This is the answer we did
      not want: `SafeOutput`'s ceiling is the *only* brightness limiter in the
      system, so it is load-bearing rather than a convenience, and it cannot
      be moved downstream of host-side bugs the way a firmware cap could.
- [ ] Is the unit MK2? (MK2 adds dedicated DMX/ILDA/MIDI ports and may differ
      in firmware/protocol.)
- [x] Does the interlock loop / aperture shutter state report over the
      network? **Yes** — `interlock_enabled` in the full-info flags byte,
      along with `temperature_warning` and `over_temperature`. We read all
      three, show them in TEST DEVICE, and refuse to enable output while the
      device reports over-temperature. Reading a device's interlock state is
      not the same as having an interlock; the hardware loop is the interlock.
- [ ] Behaviour when the Ethernet cable is unplugged mid-stream — does the
      device blank on connection loss? (Test this too; same category as §4.3.)
- [ ] Does the device enforce its own scan-fail / thermal protections
      independently? (FAQ mentions automatic shutoff above 40 °C.)

---

## 8. References

Read these for the actual protocol constants — they are the authority, not
this document:

- **`Wickedlasers/laserdocklib`** — official Wicked Lasers client library (C++,
  USB). Useful for command semantics even when targeting network.
- **`sebleedelisle/ofxLaser`** — openFrameworks addon supporting Ether Dream,
  Helios and LaserCube/LaserDock. A mature, working reference implementation
  with real device handling. Note: **non-commercial licence** — read for
  understanding, mind the licence terms before copying code.
- **Community Python gist** — "LaserCube controller"
  (`gist.github.com/s4y/0675595c2ff5734e927d68caf652e3af`), source of the
  point-packing format and `rx_buffer_free` behaviour described above,
  including a discussion thread on inter-frame gaps.
- **`hypertechnic/helios_lasercube_api`** — Helios→LaserCube via ILDA, for
  comparison with the path being replaced.
- **LaserOS FAQ** (`laseros.com/faq`) — mute threshold / ghost beam behaviour
  with third-party DACs; thermal shutoff.

---

## 9. Safety disclaimer

This spec describes software for controlling a Class 4 laser. Software controls
— including every limiter described here — are **not** safety devices. They
cannot be relied on when the software itself is the failure. Physical
protections (key switch, aperture shutter, interlock loop, Remote Stop,
appropriate eyewear, controlled beam path) are the actual safety layer and are
mandatory regardless of what this code does.