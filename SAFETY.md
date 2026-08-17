# Laser safety

What this software does about safety, what it deliberately does not, and how to
work with it. Read §1 and §2 before anything else.

This is the **laser-arcade** copy. The output layer (`laser_output.py`,
`helios.py`, `lasercube_output.py`) is shared verbatim with the sibling projects
*laser! laser! laser!* and *promptwaver*; the controls described in §3 are this
project's own. Where this document and the upstream one differ, the differences
are deliberate and called out.

---

## 1. The one thing that matters

**Software is not a safety device.** Every control described in this document is
implemented in Python, running in the same process as the render loop it is
supposed to protect you from. When the software is the failure — a crash, a
driver bug, a wedged USB call, a stuck buffer — none of it can help you. That is
not a limitation to be engineered away; it is the nature of the thing.

The actual safety layer is physical, and it is mandatory regardless of what this
code does:

- **Key switch** — output physically disabled when the key is out.
- **Aperture shutter** — blocks the beam at the head.
- **Interlock loop** — kills output when the room is opened.
- **Remote Stop** — in your hand, not across the room, whenever the key is in.
- **Eyewear** rated for *every* wavelength the projector emits, worn by everyone
  present, not just you. For the **LaserCube Ultra 7.5W** that is **455 nm blue
  (4 W), 525 nm green (2 W), 638 nm red (1.5 W)**. Check the figures against
  your own unit's plate — do not trust a number in a document.
- **Controlled beam path** — into a dump or a matte dark surface. Never toward
  eyes, windows, reflective surfaces, or skyward.

A 7.5 W Class 4 laser can cause instant permanent eye injury *including from
diffuse reflections*, plus skin burns and ignition. Treat the beam as dangerous
at all times, not just when it's pointed somewhere obvious.

### An arcade is a room full of members of the public

This project has a hazard the others don't: **people who are not the operator,
holding wireless controllers, in the same room as the beam.** That shapes every
decision in §3. The short version is that a gamepad plays games and does nothing
else — it cannot arm the laser, cannot disarm it, cannot open the config screen,
cannot touch the brightness ceiling, and cannot quit to a desktop.

---

## 2. Daily operating procedure

1. Beam path set up and clear. Dump or matte surface at the end of it.
2. Eyewear on — everyone in the room.
3. Remote Stop in hand.
4. Key in, shutter closed.
5. Start the arcade. It comes up **disarmed** with the ceiling at **5%**.
6. Open the shutter, then **ARM** (`Shift-.`, twice). Confirm the beam lands
   where you expect, at the power you expect.
7. Raise the ceiling only once you're satisfied with the aim.
8. **Then hand over the gamepads**, and put the keyboard somewhere the public
   cannot reach it. The keyboard is the operator's console; the pads are the
   public's.

To stop, in decreasing order of urgency: **Remote Stop** → **key out** →
**shutter** → **DISARM** (`.` on the keyboard). The first three are real; DISARM
is software.

---

## 3. What the software does

### ARM gate

Output starts **disarmed** on every launch and emits nothing until you arm it.
This is never remembered between runs, however the last session ended — there is
no auto-arm flag, deliberately, and `engine/store.py` refuses to persist one.

- **`Shift-.` arms**, and takes two presses. The first press says
  `press Shift-. again to ARM`.
- **`.` disarms**, instantly, with no confirmation and no fade, from any screen.
  A fade on the way down is a fade you are still emitting through.
- There is also a **LASER** row at the top of the config screen.

**Both are keyboard-only, in both directions.** A gamepad cannot arm the laser
and cannot disarm it either. The second half is a real trade-off — someone
holding a pad has no software kill — and it was chosen anyway, because the
Remote Stop is the actual interlock and a nuisance-disarm mid-game is by far the
likelier event on a cabinet. This differs from the upstream project, where a
MIDI pad can disarm.

> **"Disarmed" does not mean the DAC goes quiet.** It means it is actively
> streaming darkness. A Helios repeats its last frame until a new one arrives,
> so a DAC that simply stopped being fed would sit there replaying whatever was
> on screen when you disarmed. Silence would be the *less* safe state. This is
> why the arcade keeps writing frames even while the config screen is up and
> even when `CONFIG OUTPUT` is set to `SCREEN ONLY`.

### Brightness ceiling — **MAX BRIGHTNESS** on the config screen

A hard cap applied at the very last step before the DAC — after the scene, the
path planner, the keystone warp, and every other transform. Nothing upstream can
exceed it: not a game's palette, not `--brightness`, not a scanner setting.

Defaults to **5%**. It lives on the **config screen**, which is keyboard-only,
so a player cannot nudge it mid-show. The first press that would take it above
5% asks for confirmation; once above, further presses just adjust by 1%, and the
row always shows the value you are setting. **RESET MAX BRIGHTNESS = 5** snaps
it back.

The armed/disarmed state and the current ceiling are also shown as a badge on
the preview window, which is where to look for them at a glance.

> **It does not persist.** Every launch starts at 5% and the operator raises it
> on purpose. This is deliberately stricter than upstream *laser! laser! laser!*,
> which persists the value and lists "raise it once and it stays raised" as a
> known gap. A cabinet gets power-cycled by people who are not the operator, so
> the safer default is to forget.

On the LaserCube this ceiling matters more than it looks. The network protocol
has **no power-limit command** — per-point RGB is the only brightness control
the device exposes — so this is the only limiter in the system. There is no
firmware cap sitting downstream of a host-side bug.

It is separate from `--brightness` and `Settings.beam()`, which remain purely
creative controls operating underneath the ceiling.

At 5% on an 8-bit colour channel you have about 13 levels. Plenty for aiming,
coarse for content.

### Watchdog

A daemon thread blanks the output if the render loop stops feeding it — a GC
pause, a deadlock, a logic bug, a stalled network write.

The stall threshold adapts to the frame time: `max(250 ms, 3 × points/pps)`. A
fixed threshold would be wrong, because at 4000 points and 5000 pps a single
frame legitimately takes 800 ms. It also doesn't start guarding until the first
frame is written, since startup routinely takes longer than the threshold.

If the watchdog cannot take the device lock, it treats that as evidence the
render thread is alive inside a write, not as a stall. If the driver is genuinely
wedged inside a C call, no software can blank that device — that is what the key
switch is for.

### Blank on every exit path

`SIGINT` (Ctrl-C), `SIGTERM` (`kill`), unhandled exceptions in the render loop,
closing the window, and an `atexit` backstop all blank before releasing the
device. All four paths are verified by `--selftest`.

**SIGTERM was the real gap.** Without a handler the default action terminates the
process outright, so teardown never ran and the DAC sat replaying its last frame
indefinitely.

Blanking writes a dark frame in *repeat* mode rather than merely calling
`Stop()`, so the DAC keeps emitting darkness even if the process dies
immediately afterwards.

### Device gate (LaserCube only)

The LaserCube has a hardware output gate (`CMD_SET_OUTPUT`), so ARM and DISARM
drive the device itself, not just our frame stream. A disarmed LaserCube stops
emitting even if this process is killed mid-frame.

- **Arming can fail.** If the device refuses — it reports over-temperature, say
  — the arcade stays **disarmed** and says so. It will never show ARMED over a
  device that is dark.
- **Enable is verified, not assumed.** UDP has no acknowledgement, so a
  successful send proves only that the datagram left this machine. `enable()`
  reads the state back from the device and believes the device.
- **Switching device always disarms.** Arming is a statement about one specific
  projector; it is never carried across a device change.

### Device diagnostics

**Config → TEST DEVICE** queries the attached device and reports what it says
about itself. It emits nothing, so it is safe to press at any time, armed or not.

For a LaserCube: firmware, serial, model, connection type, temperature and
thermal warnings, **interlock state**, output state, power source, scan rate and
its maximum, buffer occupancy, the device's own packet error count, and our
frame sent/dropped/error counters. For a Helios: device count and link status —
the Helios reports no temperature, interlock or power state at all, and the panel
says so rather than showing blanks.

Reading a device's interlock state is not the same as having an interlock. The
hardware loop is the interlock; this is a readout of it.

### Keyboard-only operator controls

On a cabinet the pad is the public-facing control, so it gets gameplay and
nothing else. Enforced in `Shell._reserved` and `Shell._menu_step`:

| Control | Keyboard | Gamepad |
|---|---|---|
| Play games, menu carousel, Esc out of a game | ✓ | ✓ |
| Arm / disarm (`Shift-.` / `.`) | ✓ | ✗ |
| Open the config screen | ✓ | ✗ |
| Move menu focus *onto* CONFIG | ✓ | ✗ |
| Anything at all while config is open | ✓ | ✗ (inert) |
| Quit (`Q`, or Esc at the menu) | ✓ | ✗ |

Focus is gated as well as the button press. If a pad could highlight CONFIG but
Enter silently did nothing, that reads as a broken cabinet rather than a locked
one.

The gate works by tracking a separate keyboard-only key set (`_kbd_held`)
alongside the merged set games see, rather than subtracting the pad's keys —
so a key genuinely held on both devices at once still counts as keyboard input.

### Point generation

The path planner only emits closed shapes joined by blanked travel moves; there
are no static points. A parked beam is a burn and fire risk, not just a visual
artifact.

---

## 4. What the software does *not* protect against

Be specific about this, because vague reassurance is worse than none:

| Control | Does not help against |
|---|---|
| ARM gate | Anything after you've armed it. It is a deliberate-action gate, not a monitor. |
| Brightness ceiling | A crash, a driver bug, a stuck buffer, or a protocol error. It is a **creative limiter**. It also cannot stop a beam that is parked — 5% of a 7.5 W beam, stationary, still burns. |
| Watchdog | A driver wedged inside a C call, or anything that kills the process without running Python (`SIGKILL`, power loss, a kernel panic). |
| Blank-on-exit | `kill -9`. Nothing in userspace survives that. |
| Keyboard-only gating | Anyone who can reach the keyboard. It is a physical-access assumption — if the keyboard is on the cabinet, it is not a control. |
| Keystone correction | Anything. It is applied to the DAC copy only, for aiming. |

---

## 5. Bringing up new hardware

Do these **in order**. Do not skip ahead. This applies to a new projector, a new
DAC, a new output backend, or a rig you haven't used in a while.

1. **Dry run, no hardware.** `python run.py --selftest` — this checks that the
   output comes up disarmed, that the ceiling clamps, that the watchdog fires,
   that all four exit paths blank, and that a gamepad cannot reach any of it.
2. **Hardware connected, output disabled.** Confirm the device is detected and
   responding (**TEST DEVICE**). Confirm nothing is emitting.
3. **First light — minimum power, beam dump, eyewear, Remote Stop in hand.**
   Ceiling at 5%. Use `python tools/testpattern.py --output <kind>`, which comes
   up disarmed the same way. Confirm a blanked frame is *genuinely dark* — this
   is the check an analog ILDA path can fail, because DAC offset versus the
   projector's mute threshold produces a ghost beam.
4. **Stall test.** Deliberately stall the sender and observe what the hardware
   does. Three possibilities:
   - blanks automatically — ideal;
   - repeats the last buffered points — acceptable, still scanning;
   - **holds the final point — hazardous.** A stationary beam is a burn and fire
     risk. If this is the answer, the watchdog is load-bearing safety-critical
     code and must be treated as such.

   The **Helios repeats** (case 2), because `WriteFrame()` without
   `HELIOS_FLAG_SINGLE_MODE` replays its frame until the next one arrives. The
   LaserCube's behaviour is **not yet known** — see
   [lasercubeoutput.md §4.3](lasercubeoutput.md). Until it is answered on
   hardware, assume the hazardous case.
5. **Disconnection test.** Pull the cable mid-stream. Does the device blank?
6. **Only then** raise power incrementally.

For the LaserCube, steps 1–2 can be done properly without the projector:

```bash
python scripts/lasercube_sim.py            # a fake device, no photons
python run.py --output lasercube --lasercube-ip 127.0.0.1
```

The simulator can be told to misbehave — `--stall-after`, `--drop`,
`--refuse-enable`, `--tiny-buffer`, `--temperature 45`, `--interlock-open` — so
the watchdog, the backpressure path and the arm-refusal path can all be driven
to completion before the hardware arrives. Use `--list-lasercubes` to check
discovery against the real unit, and `--lasercube-dry-run` to pack and
rate-control against real hardware while transmitting nothing.

---

## 6. If you are changing the code

Anything touching the DAC stream — point generation, blanking, geometry,
brightness — must preserve these properties:

- **Closed shapes, blanked travel moves, blank on exit.** Non-negotiable.
- **Test with the simulator before a real laser.** The preview runs the same
  planned point stream, so bugs in it surface without hardware.
- **The ceiling and the arm gate are the last transform before the device.** If
  you add an output transform, it goes *before* `SafeOutput.write()`, never
  after. Anything downstream of the ceiling can bypass it, which defeats the
  entire mechanism. This is why the keystone warp lives in
  `engine/outputs/laser.py:to_frame()` and not in a backend.
- **The ceiling is DAC-only.** The simulator keeps showing what the content
  actually is. Dimming the preview would make the ceiling invisible rather than
  obvious.
- **New config rows are keyboard-only by construction** — `_config_step` is
  handed the keyboard-only `InputState` at its single call site. Don't add a
  second call site.

`laser_output.py`, `helios.py` and `lasercube_output.py` are **copied verbatim
from the upstream laser-laser-laser repo**. Fix bugs there and re-copy; do not
fork. A bug in `laser_output.py` is a safety bug in three repositories at once.

```bash
diff laser_output.py ../laser-laser-laser/laser_output.py
sha256sum laser_output.py ../*/laser_output.py
```

The full requirements these implement are
[lasercubeoutput.md §4](lasercubeoutput.md).

---

## 7. Known gaps

Honest list, kept current:

- **None of this has been run against real hardware.** The Helios path was
  developed with no DAC attached; the LaserCube path was developed against a
  simulator. Verify per §5 before trusting either.
- **The LaserCube wire format is reverse-engineered.** Three independent sources
  agree on the point layout, but agreement is not the same as verification. The
  first frame on real hardware is the test. If it comes out as garbage, try
  `--lasercube-point-order rgbxy` — the field order is the most likely thing to
  be wrong, and it is a flag, not a code change.
- **LaserCube underrun behaviour is unknown** (§5 step 4). Until it is answered
  on hardware, assume the hazardous case.
- **The LaserCube has no power-limit command**, so the software ceiling is the
  only brightness limiter. A firmware cap would have been strictly better.
- **Y axis orientation is unconfirmed** for the LaserCube. If the image is
  upside down, use `--invert-y`.
- **A frame larger than the device ring buffer will drop continuously.** Real
  buffers are ~6000 points and typical frames are 300–700, so this needs a
  deliberately odd configuration to hit, but the failure mode is a nearly static
  image rather than an error.
- **Keyboard-only gating assumes the keyboard is out of reach.** If the keyboard
  is sitting on the cabinet where players can reach it, none of §3's gating is
  doing anything for you.
- **The Helios write now blocks the game loop.** The old latest-wins writer
  thread was removed so the watchdog's heartbeat tracks the real render loop. A
  wedged DAC therefore costs up to 500 ms per frame (the bounded wait) rather
  than being absorbed silently — which is the intended trade, but it is a
  behaviour change on that rig.
