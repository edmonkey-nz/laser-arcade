# Porting the output layer to another project

How to adopt `laser_output.py`, `helios.py` and `lasercube_output.py` in
**laser-arcade**, **promptwaver**, or anything else that drives a laser.

Read [SAFETY.md](SAFETY.md) first if you haven't. This document is about
mechanics; that one is about what the code is for.

---

## 1. Why bother

The survey that prompted this: all three projects drive a Helios, none share
any code, and each had a *different* fraction of the safety story.

| | laser-laser-laser | laser-arcade | promptwaver |
|---|---|---|---|
| Real `blank()` primitive | ✗ | ✗ | ✓ |
| Arm/disarm gate | ✗ | ✗ | ✓ |
| Brightness ceiling | ✗ | ✓ | ✗ |
| Bounded DAC wait | ✓ | n/a | ✓ |
| `atexit` / SIGTERM blank | ✗ | ✗ | ✗ |
| Watchdog | ✗ | ✗ | ✗ |

**Nobody had the exit/watchdog half. Everyone had a different half of the
rest.** That is the whole argument: this is the layer where duplication was
costing real safety, so it gets written once.

What is *not* shared, deliberately: path planning. All three planners are
meaningfully different — greedy nearest-neighbour with adaptive coarsening in
laser-arcade, arc-length resampling with aspect letterboxing in promptwaver —
and they should stay that way. Don't let the shared layer grow into a planner.

---

## 2. What you copy

Three flat files, no package, no install step:

| File | Needed when |
|---|---|
| `laser_output.py` | Always. The protocol, `NullOutput`, `SafeOutput`. |
| `helios.py` | You drive a Helios. |
| `lasercube_output.py` | You drive a LaserCube over the network. |

Each carries a `# CANONICAL: laser-laser-laser/<name>` header. **This repo is
upstream.** Fix bugs here and re-copy; don't fork. A bug in
`laser_output.py` is a safety bug in every project that has it.

An installable package was considered and rejected: two of the three projects
freeze with PyInstaller and one has no build step at all, so a path dependency
breaks more than it solves.

### Constraints these files hold to

Keep them true or the copy stops being a copy:

- **numpy + stdlib only.** Nothing from any host project.
- **Python 3.9 compatible** — laser-arcade's floor. `from __future__ import
  annotations` at the top; no `X | None` at runtime, no `match`.
- **Library path is injectable.** `HeliosDAC(lib_path=...)`, because each
  project stages the `.so` differently — bundled as PyInstaller `datas` here,
  deliberately *not* bundled in laser-arcade, `HELIOS_LIB` env var in
  promptwaver.

---

## 3. The contract

```python
class LaserOutput(Protocol):
    name: str
    paces_loop: bool          # True if write() blocks until the device is ready
    last_points: int
    def write(self, frame, pps) -> bool: ...
    def blank(self) -> bool: ...
    def close(self) -> None: ...
    # optional:
    def enable(self) -> bool: ...    # hardware output gate, if the device has one
    def disable(self) -> bool: ...
    def diagnostics(self) -> list: ...  # [(label, value, severity), ...]
```

**Frame format: numpy `(N,6)` int32 — `x, y` 0..4095, `r, g, b, i` 0..255.**

Not normalised floats. All three projects already produce or consume the
Helios point layout, so this avoids rewriting two working pipelines. If your
pipeline *is* float-native, convert at your edge with
`laser_output.from_normalised(xy, rgb, i)`.

`paces_loop` is the one that bites. Helios `write_frame` blocks on `GetStatus`,
so the DAC's point clock times your render loop and you need no sleep. UDP and
Null do not block, so **your loop must keep its own time**:

```python
if not out.paces_loop:
    spare = points / pps - (time.monotonic() - frame_start)
    if spare > 0:
        time.sleep(spare)
```

---

## 4. Wiring it in

```python
from laser_output import NullOutput, SafeOutput, install_panic_handlers

def make_backend(kind):
    if kind == "helios":
        from helios import HeliosOutput
        return HeliosOutput(0, lib_path=my_lib_path)
    if kind == "lasercube":
        from lasercube_output import LaserCubeOutput
        return LaserCubeOutput(ip=None)      # None = discover by broadcast
    return NullOutput()

out = SafeOutput(make_backend(kind), max_brightness=0.05)
install_panic_handlers(out)                  # as late as possible before the loop

try:
    while running:
        frame = ...                          # your (N,6) int32 frame
        out.write(frame, pps)
        if not out.paces_loop:
            ...                              # sleep to hit your frame time
finally:
    out.close()                              # blanks first
```

Use a literal `if/elif` with direct imports, not `importlib` — PyInstaller
cannot trace `importlib` and will silently drop the backend from the bundle.

`SafeOutput` owns the arm gate, the brightness ceiling, the watchdog, the
device lock and blank-on-exit. Nothing downstream of it can bypass those, which
is the point — so **do not** apply your own transforms after `out.write()`.

### The one hard rule

Your geometry correction, orientation flips and any other output transform go
**before** `out.write()`. The ceiling and arm gate are the last thing before
the device. Anything you put after them can exceed the ceiling, which defeats
the entire mechanism.

---

## 5. Per-project notes

### promptwaver (LaserFlow)

Nearly free — its output interface already matches (`write`/`blank`/`close`/
`.name`/`.last_points`/`make_output`); the protocol was derived from it.

- Replace the inline `HeliosPoint` and `_wait_ready` in
  `promptwaver/output/ilda.py` with the shared backend. Its docstring already
  says "drop your existing laserx3 wrapper in here if you prefer" — that is
  the seam.
- **Keep the planner untouched.** Arc-length resampling, aspect letterboxing
  and keystone stay where they are, upstream of `out.write()`.
- Its `laser_on` gate becomes `SafeOutput.arm()`/`disarm()`. Keep the
  behaviour where disarming is unfaded and same-tick.
- `i` convention there is 255 lit / 0 blank. Keep it; just be consistent.
- **Watch the audio thread.** The render loop shares a process with a realtime
  `sounddevice` callback, and two regressions are documented in-tree from
  exactly this: a per-point Python packing loop holding the GIL, and a naked
  `while: pass` spin on `GetStatus`. The shared code sleeps rather than spins
  and packs with numpy in one shot — don't undo either. Prefer the LaserCube
  backend here if you have the choice: its `write()` never touches a socket.

Gains: atexit/SIGTERM blanking, watchdog, brightness ceiling, diagnostics.

### laser-arcade

Needs a small adapter, because its output path is pure Python — a list of
`(x, y, r, g, b)` 5-tuples already in DAC units, with no `i` channel.

```python
import numpy as np

def to_frame(points):
    """laser-arcade LaserPoint list -> (N,6) int32."""
    a = np.asarray(points, dtype=np.int32)        # (N,5): x,y,r,g,b
    frame = np.empty((len(a), 6), dtype=np.int32)
    frame[:, :5] = a
    frame[:, 5] = a[:, 2:5].max(axis=1)           # i = max(r,g,b), its convention
    return frame
```

numpy is already a dependency (`numpy>=1.24`), it just isn't used in the
output path yet.

- **`i = max(r, g, b)` is its existing convention** — keep it, or brightness
  changes on that rig for no reason anyone will remember.
- Its `HeliosOutput` already runs a writer thread with latest-wins buffering.
  Either keep that and set `paces_loop = False`, or drop it and let the
  blocking Helios path pace the pygame loop — but not both.
- Its global `brightness` multiplier in `Settings.beam()` is a *creative*
  control, the same as this project's. Leave it; the ceiling is separate and
  sits below it.
- It ships PyInstaller `--onefile` with the `.so` *not* bundled, so pass
  `lib_path` explicitly from its existing `_search_dirs` logic.
- Python 3.9 floor comes from here. It is why the shared files avoid modern
  syntax.

Gains: atexit/SIGTERM blanking, watchdog, a real `blank()`, arm gate,
diagnostics, and the LaserCube backend for free.

### ilda_export

**Out of scope.** It is a Cinema 4D plugin that writes `.ild` files and must
stay Python 2.7 compatible with no third-party deps. It does not drive a DAC.
Its overlap with `ilda.py` is file-codec duplication — a separate question,
and the 2.7 constraint would poison this package if you merged them.

---

## 6. Verifying the port

Per project, before you trust it. None of this needs a laser:

1. App starts, comes up **disarmed**, ceiling 5%.
2. Disarmed produces zero-colour frames — not an absence of frames.
3. Ceiling holds with your brightness control at maximum.
4. All four exit paths blank: Ctrl-C, `kill -TERM`, an exception in the loop,
   `atexit` alone.
5. Watchdog fires on an injected `sleep(2)` and releases on recovery.
6. Frame rate unregressed versus before the port.
7. For LaserCube: `scripts/lasercube_sim.py` with `--stall-after`, `--drop`,
   `--refuse-enable`, `--tiny-buffer`, `--temperature 45`.

Then, and only then, [SAFETY.md](SAFETY.md) §5 on real hardware.

---

## 7. Keeping copies in sync

There is no tooling for this and deliberately no build step. The discipline is
manual:

```bash
diff laser_output.py ../laser-arcade/laser_output.py
sha256sum laser_output.py ../*/laser_output.py
```

If you find yourself wanting to change one of these files *for one project*,
that is the signal the change belongs behind a constructor argument instead —
`lib_path` and `max_brightness` are both there for exactly that reason.
