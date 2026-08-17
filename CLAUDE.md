# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A vector arcade that drives a **real Class 4 laser projector**. Read
[SAFETY.md](SAFETY.md) before touching anything in the output path. The rest of
this file is the short version of what will bite you.

## Commands

```bash
python run.py                          # menu, on-screen simulator
python run.py --output helios          # Helios DAC
python run.py --output lasercube       # LaserCube over the network
python run.py --game pong              # straight into a game
python tools/testpattern.py            # calibration pattern (also arms/disarms)

python scripts/lasercube_sim.py        # fake LaserCube, no photons
python run.py --output lasercube --lasercube-ip 127.0.0.1
python run.py --list-lasercubes        # discover on the network, then exit
```

### Tests

`--selftest` is the whole test suite. There is no pytest, no linter config, and
no build step. It runs headless and needs no hardware:

```bash
python run.py --selftest               # everything: games + safety layer
```

CI runs it against the **frozen binary** as well as the source, because
packaging that succeeds while producing a binary that can't start is the failure
mode worth catching.

To run one half rather than all of it — `engine/selftest.py` exposes both:

```bash
# just the laser safety layer (arm gate, ceiling, watchdog, pad gating)
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python -c "
import pygame; pygame.init(); pygame.display.set_mode((64,64))
from engine.selftest import _safety_checks
raise SystemExit(1 if _safety_checks() else 0)"

# just the games, with a shorter run
SDL_VIDEODRIVER=dummy python -c "
from engine.selftest import run; raise SystemExit(run(frames=5))"
```

Individual safety checks are closures inside `_safety_checks()`; to isolate one,
run the block above and read its per-check `ok` / `FAILED` lines rather than
trying to invoke a single closure.

## The rules that matter

**1. Never weaken the safety layer to make something easier.**

Output starts **disarmed at a 5% ceiling on every launch**, and neither is
persisted. There is no auto-arm flag and `engine/store.py` actively refuses to
save one. If a task seems to want "just start it armed", that is the task being
wrong.

**2. Output transforms go BEFORE `SafeOutput.write()`, never after.**

The brightness ceiling and arm gate are the last thing before the device.
Anything downstream of them can exceed the ceiling, which defeats the entire
mechanism. This is why the keystone warp lives in
`engine/outputs/laser.py:to_frame()` and not in a backend — it used to be in the
Helios backend and had to move.

**3. Disarmed means *streaming darkness*, not silence.**

A DAC that stops being fed replays its last frame forever. Always keep writing
frames; `SafeOutput` zeroes the colour columns and keeps the geometry. Never
"optimise" by skipping the write.

**4. Three root files are copies. Do not edit them here.**

`laser_output.py`, `helios.py`, `lasercube_output.py` (and
`scripts/lasercube_sim.py`) are verbatim from the sibling `laser-laser-laser`
repo, which is upstream. A bug in `laser_output.py` is a safety bug in three
repositories. Fix it there and re-copy:

```bash
diff laser_output.py ../laser-laser-laser/laser_output.py
```

Wanting to change one of them *for this project only* is the signal the change
belongs behind a constructor argument (`lib_path`, `max_brightness` both exist
for exactly that reason).

**5. A gamepad must never reach the operator's controls.**

Arm/disarm, the config screen, and quitting are **keyboard-only**, because on a
cabinet the pad is in a stranger's hands. The shell keeps a separate
`_kbd_held` set for this — see *Input provenance* in [TECHNICAL.md](TECHNICAL.md).
Never feed the merged `inp` to `_config_step`.

**6. The simulator shows the truth.**

It is fed the untouched, full-brightness point list regardless of arm state or
ceiling. Dimming the preview would make the limiter invisible instead of
obvious. The arm badge is the one bitmap-font element in the engine, on purpose
— it is an operator readout, not something to burn into the beam.

## Architecture

Two codebases meet in this repo, and the boundary between them is the thing to
understand first:

```
engine/ + games/         written here, owned here
─────────────────────────────────────────────────  the adapter is the seam
laser_output.py          copied verbatim from ../laser-laser-laser (upstream)
helios.py                  "
lasercube_output.py        "
```

`engine/outputs/laser.py` is the only place the two touch.

### The frame pipeline

One pass per tick, all of it inside `Shell.run()`:

```
Game.scene(t)              world-space polylines + colour, [-1,1]
  -> pathplan.plan()       greedy nearest-neighbour ordering, adaptive point
                           density, blanked travel moves  ->  list[(x,y,r,g,b)]
                           already in 0..4095 DAC units
  -> Simulator.send()      the preview: untouched, full brightness, unwarped
  -> to_frame()            keystone warp + numpy (N,6) int32, i = max(r,g,b)
  -> SafeOutput.write()    ARM GATE and BRIGHTNESS CEILING applied here
  -> backend.write()       Helios (ctypes) / LaserCube (UDP) / Null
```

The two branches after `pathplan` are deliberate and must not be merged: the
simulator shows what the content *is*, the laser gets what is *safe to emit*.
Dimming the preview would make the ceiling invisible instead of obvious.

Frame formats differ either side of the adapter:

- planner / simulator: `list[(x, y, r, g, b)]`, already in DAC units
- shared layer: numpy `(N,6)` int32 — `x, y` 0..4095, `r, g, b, i` 0..255,
  with `i = max(r, g, b)` (this repo's long-standing convention — keep it)

### The state machine

`Shell.mode` is only ever `menu | game | config`. Per-game states (attract,
serving, dying, game-over) live inside each `games/<name>/world.py` and the
shell knows nothing about them — it just calls `update(dt, inp)` and `scene(t)`.

Adding a game means adding `games/<name>/` with a `Game` subclass and
registering it in `games/__init__.py`; the shell needs no changes.

### Where the layers live

```
run.py                     CLI + entry; owns the CLI-vs-persisted precedence
engine/shell.py            window, state machine, input gating, render loop
engine/config.py           Settings — every tunable, one dataclass
engine/pathplan.py         scene -> point stream (the clever bit)
engine/outputs/laser.py    the adapter: to_frame(), make_backend(), lib search
engine/outputs/simulator.py preview + the arm badge
engine/store.py            ~/.laser-arcade/{config,highscores}.json
engine/selftest.py         the test suite
```

## Constraints

- **Python 3.9 floor.** CI builds on 3.9. No `X | None` at runtime, no `match`.
  Every module uses `from __future__ import annotations`.
- **Dependencies are `pygame` and `numpy` only.** The shared files must stay
  importable standalone (numpy + stdlib, nothing project-specific).
- **PyInstaller `--onefile`, no spec file.** Use literal `if/elif` with direct
  imports for backends — PyInstaller cannot trace `importlib` and will silently
  drop them from the bundle.
- **The Helios `.so` is deliberately not bundled.** The user drops it beside the
  executable, which is why `lib_path` injection and the `sys.frozen` search
  branch in `engine/outputs/laser.py` exist.
- **All in-game text is the stroke vector font** (`engine/font.py`), because it
  has to be drawable by galvos. The only exception is the simulator's arm badge.

## Conventions

- Comments explain *why*, especially where something looks redundant or
  over-careful — most of those places are load-bearing safety decisions and a
  future reader will otherwise "clean them up".
- Two-press confirms for destructive or dangerous actions (arm, raising the
  ceiling past 5%, clearing high scores). Moving the config cursor cancels any
  pending confirm.
- Disarming, by contrast, is never confirmed and never faded — a fade on the way
  down is a fade you are still emitting through.
- Releases: bump `__version__` in `engine/__init__.py`, then tag. See
  *Builds and releases* in [TECHNICAL.md](TECHNICAL.md).
