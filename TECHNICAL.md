# Laser Arcade — technical notes

Architecture, hardware setup and tuning. For installing and playing, see
[README.md](README.md).

The interesting part isn't the games — it's the shared **engine** that turns
shapes into a scanner-friendly point stream (interpolation, corner dwell,
blanked slews, adaptive density) so the beam looks clean instead of smeared.

## Layout

```
laser-arcade/
  run.py                    entry point + CLI
  controller_test.py        gamepad diagnostic (prints live button/axis numbers)
  laser_output.py           SHARED: arm gate, brightness ceiling, watchdog
  helios.py                 SHARED: Helios ctypes backend
  lasercube_output.py       SHARED: LaserCube UDP network backend
  scripts/lasercube_sim.py  SHARED: fake LaserCube, for dry runs
  engine/                   reusable, game-agnostic core
    config.py               all tunables (Settings)
    vec.py                  2-vector
    font.py                 stroke vector font (shared by games + menu)
    pathplan.py             scene -> scanner point stream (the clever bit)
    audio.py                synth primitives + a name-driven SoundBank
    game.py                 the Game interface + Scene/InputState types
    shell.py                window, outputs, and the menu / game / config loop
    keymap.py               remappable gameplay actions
    joystick.py             gamepads, translated into key codes
    store.py                config + high score JSON (~/.laser-arcade)
    outputs/                base / simulator / laser (adapter to the shared layer)
  games/
    __init__.py             GAMES registry (menu order lives here)
    asteroids/              world.py, shapes.py, render.py, sfx.py + adapter
    pong/                   world.py, render.py, sfx.py + adapter
    slipstream/             world.py, render.py, sfx.py + adapter
    missile/                world.py, render.py, sfx.py + adapter (mouse input)
    gyruss/                 world.py, render.py, sfx.py + adapter
    defender/               world.py, render.py, sfx.py + adapter (wraparound world)
    snake/                  world.py, render.py, sfx.py + adapter
  tools/testpattern.py      calibration pattern
```

Three ideas hold it together:

- **A game is anything that implements `engine.game.Game`.** It advances its own
  simulation in `update(dt, input)`, hands back a `scene()` (world-space
  polylines + colour) to draw, and declares its sounds. It owns its own internal
  states (attract, serving, game-over); the shell only knows *menu vs game*.
- **The shell is generic.** It builds the menu, runs the selected game, routes
  input, plans each frame to points, and manages audio. It contains nothing
  game-specific.
- **The engine never mentions a game.** `pathplan`, `outputs`, `font`, `audio`
  and `config` know about lasers and drawing, not about ships or paddles.

Games are decoupled from raw keys by an `InputState` (which keys are `down()`
this frame, which were `hit()` as edges), so each game maps its own controls.

A fourth idea arrived with laser output:

- **The output layer is borrowed, not owned.** `laser_output.py`, `helios.py`
  and `lasercube_output.py` are verbatim copies from the upstream
  `laser-laser-laser` repo and must not be forked here. The arcade meets them
  through one adapter, `engine/outputs/laser.py`. See *The laser output layer*.

## Input provenance (keyboard vs gamepad)

The shell keeps **two** key sets per frame, and this is a safety boundary rather
than a convenience:

| Set | Contents | Who sees it |
|---|---|---|
| `_held` / `pressed` | keyboard **and** pad, merged | games, the menu carousel |
| `_kbd_held` / `kbd_pressed` | keyboard only | the config screen, the arm gate, quit |

`engine/joystick.py` deliberately makes a pad look like a keyboard, which is
what keeps games ignorant of joysticks — but it also means that by the time an
`InputState` is built, a pad's Space is indistinguishable from a real one. On a
cabinet the pad is in a stranger's hands and the keyboard is the operator's
console, so anything that can point a Class 4 laser has to be able to tell them
apart.

The keyboard-only set is **tracked separately** rather than derived by
subtracting the pad's keys from the merged set. Subtraction would drop a key
that happened to be held on both devices at once, silently failing open for the
operator at exactly the wrong moment.

Three gates consume it, all in `engine/shell.py`:

1. `_menu_step(inp, kbd_inp)` — the carousel takes either source; moving focus
   onto **CONFIG** takes `kbd_inp`. Gating the *focus* and not merely the Enter
   press is deliberate: a pad that could highlight CONFIG and then find Enter
   does nothing reads as a broken cabinet rather than a locked one.
2. `_config_step(kbd_inp)` — one call site, always the keyboard-only view, so a
   config row added later cannot accidentally become pad-reachable.
3. `_reserved(key, mod, from_pad)` — the pre-existing hook that already made
   quitting keyboard-only. It now also owns `.` / `Shift-.` (disarm/arm), `Tab`
   and the live tuner's `- = [ ] \ Bksp`, and returns early for *any* pad key
   while `mode == "config"`.

The tuner (`engine/tuner.py`) is an **overlay, not a fourth mode** — `Shell.mode`
stays `menu | game | config` and the game underneath keeps running. Its four
adjust keys are reserved *only while it is open*, so they remain ordinary
rebindable keys the rest of the time; that is the same bargain the arrow keys
already make with the config screen. It edits through `_adjust_pps` /
`_adjust_points`, shared with the CONFIG rows so clamps and step sizes cannot
drift apart, and it draws with the bitmap font onto the monitor — putting it in
the scene would add points to the frame and move the very number being tuned.

`_save_tuning()` is called **only** from an explicit save — the tuner's `\` or
CONFIG's SAVE & BACK. Not on close, not on quit, not per keypress. The first
version wrote on every close, and an afternoon's experimenting duly became the
cabinet's permanent state at 5 FPS; `POINTS` is the trap, because the direction
that reads as "better quality" is the one that costs refresh.

`--selftest` drives all of this through the real merge path with a synthetic
pad; see `_safety_checks` in `engine/selftest.py`.

## Gamepads

`engine/joystick.py` makes a pad look like a keyboard: it polls every connected
device and synthesises pygame key codes, so games and the keymap need to know
nothing about joysticks.

Each frame the held set is rebuilt from scratch as the union of buttons, hat and
axes, then diffed against the previous frame to produce edges. Rebuilding rather
than mutating matters — several sources map onto the same key (a pad may report
its D-pad as a hat *and* as buttons), and with incremental updates whichever
source ran last would clobber the others.

Two details earn their keep on cheap hardware:

- **Sticky releases.** A held hat direction can drop to centre for a frame or
  two. Releases are held back for `STICKY_MS` so chatter doesn't read as a
  release.
- **Repeat is directions-only.** Held directions auto-repeat for menu
  navigation; a repeating fire button would re-launch from the menu, and a
  repeating Escape would walk straight out of the app.

The two sticks map to different keys on purpose — left to WASD, right to arrows
— which is what lets one pad drive both Pong paddles, since the keymap already
binds `p1_*` to W/S and `p2_*` to the arrows. The shell turns the left stick
into a virtual mouse cursor for Missile Command, handing control back the moment
a real mouse moves.

Stick axes are worked out per pad rather than assumed, because guessing is not
subtle: an analogue trigger rests at -1.0, so mistaking one for a stick axis
reads as a direction held down forever. Measured on real hardware, a generic
USB pad rests with its triggers on axes 4/5 (right stick 2/3) while a DualShock
4 rests with them on 2/5 (right stick 3/4) — no fixed pair is right for both.

For the same reason several button numbers map to the same action: one map
covers pads that number their buttons differently, so a generic pad and a
DualShock 4 can be connected simultaneously and both work. The D-pad button
entries (12-15) are a fallback for pads with no hat, and are ignored whenever a
hat is present — on a DualShock 4, button 12 is the right-stick click, which
would otherwise read as UP.

Input from all connected pads is unioned. That means two people with two pads
both drive player 1 from their left stick in Pong; sharing a single pad (left
stick vs right stick) is the supported two-player arrangement. Per-pad player
assignment would need the manager to keep its keys separated by device.

## High scores

`Game.score()` returns the current score or `None`, and `Game.set_high_score()`
seeds the best from previous sessions. The shell records every frame into memory
and writes to `~/.laser-arcade/highscores.json` on leaving a game (and in its
`finally` block, so quitting mid-game still keeps the score). A corrupt or
missing file reads as an empty table rather than raising — a cabinet should
still boot.

Pong returns `None` because two players have no single score; Slipstream because
a time trial's record is a *low* time, which an integer table gets backwards.

## Adding a game

Drop a package under `games/`, implement the interface, and register it. A
minimal game:

```python
# games/breakout/__init__.py
import pygame
from engine.game import Game, InputState, Scene

class BreakoutGame(Game):
    name = "BREAKOUT"
    key = "breakout"      # used for --game and the per-game PPS setting
    players = 1
    blurb = "LEFT RIGHT MOVE"
    icon = [[(-0.8, 0.6), (0.8, 0.6), (0.8, 0.3), (-0.8, 0.3), (-0.8, 0.6)],
            [(0.0, -0.6), (0.15, -0.4), (0.0, -0.2), (-0.15, -0.4), (0.0, -0.6)]]

    def start(self):
        self.world = BreakoutWorld()

    def update(self, dt: float, inp: InputState):
        km = self.cfg.keymap                # respects the player's rebound keys
        d = (1 if km.down(inp, "right") else 0) - (1 if km.down(inp, "left") else 0)
        self.world.step(dt, d, launch=km.hit(inp, "fire"))

    def scene(self, t: float) -> Scene:
        return render_breakout(self.world, self.cfg, t)   # [(polyline, colour), ...]

    def score(self):
        return self.world.score           # optional; omit if there's no score

    # optional: sound_spec() -> (one_shots, loops); audio_events(); active_loops()
```

Prefer `self.cfg.keymap.down(inp, "action")` / `.hit(inp, "action")` over raw key
codes so the player's CONFIG rebindings apply automatically; see `engine/keymap.py`
for the action names. For a mouse-driven game (Missile Command is the example),
`inp.mouse_pos` is a world-space `(x, y)` or `None`, and `inp.mouse_down` /
`inp.mouse_click` mirror `down()`/`hit()` for the primary button — the shell
hides the OS cursor during play, so draw your own crosshair from `mouse_pos`.
`icon` is optional but recommended: a short list of polylines in local space
roughly bounded to `[-1, 1]`, shown in the main menu carousel above the game's
name. Skip it and the carousel just shows the name with no pictogram.

Then add it to the registry:

```python
# games/__init__.py
from .breakout import BreakoutGame
GAMES = [AsteroidsGame, PongGame, SlipstreamGame, MissileGame, GyrussGame, DefenderGame, SnakeGame, BreakoutGame]
```

It now appears in the menu and is launchable with `--game breakout`. Use
`self.cfg.beam(colour)` for every colour so `--mono` / `--brightness` keep
working, and keep coordinates in the `[-1, 1]` world square.

## Builds and releases

`.github/workflows/build.yml` runs on every push and pull request to `main`,
and on `v*` tags.

1. **test** — installs the requirements and runs `python run.py --selftest` on
   Linux, Windows and macOS, plus Python 3.9 on Linux (the oldest version the
   README claims).
2. **build** — PyInstaller `--onefile` on each platform, then runs the *built
   executable* with `--selftest` and `--version`. Packaging that succeeds but
   produces a binary that can't start is the failure mode worth catching, and
   only running the artifact catches it.
3. **release** — on a `v*` tag only, attaches the archives to a GitHub release.

To cut a release, bump `__version__` in `engine/__init__.py`, then:

```bash
git tag v1.3.0 && git push origin v1.3.0
```

The build is deliberately **not** `--windowed`: the app prints diagnostics
(`[joystick] detected ...`, `[helios] shared library not found`) that people
need when setting up a cabinet, and hiding the console throws those away.

Nothing outside the Python packages is bundled — the fonts are stroke geometry
in `engine/font.py` and the sounds are synthesised at launch, so there are no
data files to collect. The Helios library is deliberately *not* bundled: it is
platform-specific and licensed separately, so it is loaded at runtime from
beside the executable.

`engine/selftest.py` lives in the engine rather than `tools/` precisely so it
ends up inside the packaged binary.

## The laser output layer

Read [SAFETY.md](SAFETY.md) before changing anything in this section.

Four files at the repo root are **copies from the upstream `laser-laser-laser`
repo, shared verbatim with it and with promptwaver**:

| File | What it is |
|---|---|
| `laser_output.py` | `LaserOutput` protocol, `NullOutput`, `SafeOutput` (arm gate, brightness ceiling, watchdog, blank-on-exit), `install_panic_handlers` |
| `helios.py` | Helios ctypes backend |
| `lasercube_output.py` | LaserCube UDP network backend |
| `scripts/lasercube_sim.py` | a fake LaserCube for dry runs |

**This repo is downstream. Fix bugs upstream and re-copy; do not fork them.** A
bug in `laser_output.py` is a safety bug in three repositories at once. There is
no tooling for the sync and deliberately no build step — the discipline is
manual:

```bash
diff laser_output.py ../laser-laser-laser/laser_output.py
sha256sum laser_output.py ../*/laser_output.py
```

If you want to change one of these files *for this project only*, that is the
signal the change belongs behind a constructor argument instead — `lib_path` and
`max_brightness` both exist for exactly that reason.

### How the arcade plugs into it

The shared layer speaks a numpy `(N,6)` int32 frame (`x, y, r, g, b, i`); the
arcade's planner emits a list of `(x, y, r, g, b)` 5-tuples. The seam is
`engine/outputs/laser.py`:

- `to_frame(points, cfg)` — converts, derives `i = max(r, g, b)` (this repo's
  long-standing convention), and applies the **keystone warp**.
- `make_backend(kind, cfg)` — a literal `if/elif` with direct imports, because
  PyInstaller cannot trace `importlib` and would silently drop the backend.

The keystone warp used to live inside the Helios backend. It moved because
**every output transform must run before `SafeOutput.write()`** — anything
downstream of the brightness ceiling can exceed it, which defeats the ceiling
entirely. If you add a transform, it goes in `to_frame`, not in a backend.

The simulator is fed the *untouched* point list, so the preview shows what the
content actually is: full brightness, unwarped, regardless of arm state. Dimming
it would make the ceiling invisible rather than obvious.

`HeliosOutput.paces_loop` is `True`, so `write()` blocks on `GetStatus` (bounded
at 500 ms) and the DAC's point clock helps time the loop. The old latest-wins
writer thread was **removed** rather than kept alongside it: with a thread in
between, the watchdog's heartbeat would track the writer rather than the render
loop, and a stalled game loop would go undetected.

## Helios DAC setup (for real laser output)

The shared library is loaded at runtime via `ctypes`; it is **not** a pip
dependency. Put `libHeliosDacAPI.so` next to `run.py` and it's found
automatically (the loader also checks the current directory and `~/.local/lib`).
Until it's there, `--laser` prints `[helios] shared library not found` and falls
back to the simulator — that message is expected, not a crash.

**Already had it working for Laser Asteroids?** Copy that same file across:

```bash
cp ~/snap/laser-asteroids/libHeliosDacAPI.so ~/snap/laser-arcade/
```

**Or use the prebuilt library.** The repo ships an x86-64 build:

```bash
git clone https://github.com/Grix/helios_dac
cp helios_dac/sdk/examples/python/libHeliosDacAPI.so .   # into the laser-arcade folder
```

**Or build from source.** Mind the current repo layout: the flat C-API wrapper
lives in `shared_library/`, and `HeliosDac.cpp` pulls in the `idn/` sources, so
those must be compiled and linked too (miss them and you get file-not-found or
undefined-reference errors). Build from `sdk/cpp`:

```bash
sudo apt install libusb-1.0-0-dev        # provides the -lusb-1.0 link symlink
cd helios_dac/sdk/cpp
g++ -std=c++14 -fPIC -O2 -I. -c HeliosDac.cpp
g++ -std=c++14 -fPIC -O2 -I. -c idn/idn.cpp
g++ -std=c++14 -fPIC -O2 -I. -c idn/idnServerList.cpp
g++ -std=c++14 -fPIC -O2 -I. -c idn/plt-posix.cpp
g++ -std=c++14 -fPIC -O2 -I. -I.. -c shared_library/HeliosDacAPI.cpp
g++ -shared -o libHeliosDacAPI.so *.o -lusb-1.0
# copy libHeliosDacAPI.so into the laser-arcade folder (or a system lib dir)
```

USB access without root, via udev:

```bash
# /etc/udev/rules.d/99-helios.rules
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="e500", MODE="0660", GROUP="plugdev"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

The config tries `libHeliosDacAPI.so` and `libHeliosLaserDAC.so` (with and
without a `./` prefix). Newer SDK builds also want `libusb-1.0.so` alongside.

## Calibrate first

Stream the test pattern and sort out scale / offset / orientation on your galvo
amp before playing:

```bash
python tools/testpattern.py --laser
# add --invert-x / --invert-y / --swap-xy / --fill 0.9 until the square is
# square, the circle is round, and the doubled corner tick is top-right.
```

Then carry the same flags to `run.py`. The on-screen preview stays upright no
matter which orientation flags you set, so it never mirrors when you flip the
beam for your optics.

Orientation, overall size and the default point rate are also rows on the CONFIG
screen (FLIP X / FLIP Y, OUTPUT SCALE, PPS DEFAULT) and are saved to
`~/.laser-arcade/config.json`, so a cabinet only needs calibrating once. A flag
on the command line beats the saved value for that run — see the `explicit` dict
in `run.py`, which is re-applied after `store.apply_to()` for exactly that
reason.

## Scanner tuning guide

Everything lives in `engine/config.py`; the most useful are also CLI flags. All
distances are in DAC units (the field is `0..4095`).

| Setting / flag | What it does |
|---|---|
| `--pps` | Points per second to the DAC. Higher = less flicker, but your galvos have a ceiling (cheaper scanners ~20–30k). Per game on the CONFIG screen and under Tab. |
| `--lit-budget` / POINTS | Floor under the planner's per-frame budget, and per game the main lever on refresh rate: the DAC plays a frame in `points/pps` seconds. It bounds *interpolation* only — dwells and blanked travel land on top, so a busy multi-shape scene emits well over its budget. |
| `--fps` / `target_fps` | Frame build rate. The planner budgets **total points ≈ pps/fps**, so these two together cap how much detail a frame holds before it auto-coarsens. |
| `--max-step` | Finest gap between lit points. Smaller = brighter, straighter lines and more points; larger = fewer, faster, fainter. It's a floor — the planner only ever goes *coarser* when busy. |
| `corner_dwell` | Points held at corners so mirrors settle instead of rounding them. Raise if corners look mushy; it costs points. |
| `start_dwell` / `end_dwell` | Points held at each shape's start/end so the beam doesn't smear on/off. |
| `blank_dwell` / `blank_step` | Beam-off jumps between shapes. Raise `blank_dwell` if faint "tails" appear before a mirror arrives; lower `blank_step` if long jumps leave tails. |
| `--fill` | Field usage (0..1). Leave a border so you don't slam the galvo rails. |
| `--scale` / `output_scale` | Overall image size (0.10..1.0), on top of `--fill`. Framing for the room, and the one geometry setting the preview *does* show. A smaller image is a hotter one — same power, less ground covered — so it is not a power control. |
| `--invert-x/-y`, `--swap-xy` | Orientation, per your projector. DAC only; the preview stays upright. |
| `--mono` / `--brightness` | Single-colour output / global colour scale. |
| `--show-blanking` | Draw the beam-off travel faintly in the simulator. |

Rule of thumb for a scanner rated *N* kpps: start `--pps` at ~0.8·N,
`--max-step 40`, `corner_dwell 1`. If corners round off, raise `corner_dwell`. If
it flickers, raise `--pps` or lower `--fps`, or accept a larger `--max-step`.
Pong is very light (a few hundred points a frame), so it stays flicker-free even
at conservative point rates; Asteroids is heavier when the field is full.

## Troubleshooting

- **`[helios] shared library not found` / falls back to the simulator** — the
  Helios library isn't beside `run.py`. Copy `libHeliosDacAPI.so` into the folder
  (see *Helios DAC setup*). The message lists exactly which directories it
  searched.
- **Loaded but "no DAC found"** — the library is fine but the device isn't
  visible: check the udev rule, the USB cable, and `--device` if you have more
  than one Helios.
- **`RuntimeWarning: ... avx2 capable but pygame was not built with support`** —
  harmless. It's a performance note from the prebuilt pygame wheel, not an error;
  the game runs fine.
- **Preview looks mirrored on the wall** — that's the calibration flags doing
  their job on the DAC; the preview is deliberately kept upright. Adjust
  `--invert-x/-y` / `--swap-xy` against the wall, not the screen.
- **Corners rounded / lines smeared** — lower `--pps` or raise `--max-step` (your
  scanners are being asked for more than they can track), then nudge dwell.
- **Gamepad does nothing, or only some directions work** — run
  `python controller_test.py` and check the button numbers against
  `DEFAULT_BUTTON_MAP` in `engine/joystick.py`. Stick axes are auto-detected,
  so triggers no longer read as a stuck direction; if a pad still gets it
  wrong, set `AUTODETECT_STICKS = False` and fill in the axes by hand.
- **A Bluetooth pad pairs but the arcade never sees it** — the transport is
  irrelevant to us: SDL enumerates whatever the OS exposes as a joystick, so if
  `/dev/input/js*` or a `/proc/bus/input/devices` entry doesn't appear, the pad
  never reached userspace and no application can see it. Check with:

  ```bash
  bluetoothctl info <MAC>            # Connected: yes but Bonded: no is the tell
  journalctl -u bluetooth -n 40 | grep -i input
  ```

  `hidp_add_connection() Rejected connection from !bonded device` means BlueZ
  refused the HID channel because the pad paired without bonding — the default
  `ClassicBondedOnly=true` in `/etc/bluetooth/input.conf`. Re-pair the pad
  properly (remove it first, then put it in pairing mode) so it bonds; DualShock
  4 pairing mode is SHARE + PS held until the lightbar double-flashes. Failing
  that, setting `ClassicBondedOnly=false` under `[General]` in that file and
  restarting `bluetooth.service` lets unbonded HID devices connect — it is a
  deliberate security relaxation, so prefer bonding if you can get it.
