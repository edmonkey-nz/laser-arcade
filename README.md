# Laser Arcade

[![build](https://github.com/edmonkey-nz/laser-arcade/actions/workflows/build.yml/badge.svg)](https://github.com/edmonkey-nz/laser-arcade/actions/workflows/build.yml)
[![release](https://img.shields.io/github/v/release/edmonkey-nz/laser-arcade?label=release&sort=semver)](https://github.com/edmonkey-nz/laser-arcade/releases/latest)
[![platforms](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macos-blue)](https://github.com/edmonkey-nz/laser-arcade/releases/latest)
[![python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

A tiny **arcade for a laser projector**. Vector games drawn as a stream of galvo
points and streamed to a [Helios DAC](https://bitlasers.com/helios-laser-dac/),
with a laser-drawn menu to switch between them. Everything runs on an on-screen
simulator too, so you can build and play without hardware in front of you.

> I've been wanting to play games big outside for years. This was created with
> Claude.ai — with many hours crafting, testing and orchestrating by a human.
>
> Shoutouts to the creators of the original games and the technicians who
> designed and built the actual hardware. Hats off. AI couldn't have built any
> of this if that code and all the open source awesomeness wasn't available.
> Don't make money from this, just use it and have fun.

| Asteroids | Pong | Slipstream |
|---|---|---|
| ![asteroids](docs/asteroids.png) | ![pong](docs/pong.png) | ![slipstream](docs/slipstream.png) |

| Missile Command | Gyruss | Defender |
|---|---|---|
| ![missile](docs/missile.png) | ![gyruss](docs/gyruss.png) | ![defender](docs/defender.png) |

| Snake | Menu | Config |
|---|---|---|
| ![snake](docs/snake.png) | ![menu](docs/menu.png) | ![config](docs/config.png) |

## The games

- **Asteroids** — thrust, wrap, splitting rocks, saucers, hyperspace.
- **Pong** — 2-player, paddle-angle deflection, first to 7.
- **Slipstream** — a Wipeout-style hover racer down a wireframe track. Time
  trial; gets longer and gnarlier every level.
- **Missile Command** — aim a crosshair, detonate counter-missiles, defend your
  cities through escalating waves.
- **Gyruss** — radial tunnel shooter; you orbit the edge and fire inward.
- **Defender** — side-scrolling rescue over a wraparound world.
- **Snake** — eat, grow, speed up, don't bite yourself.

## Download

Prebuilt executables for Linux, Windows and macOS are on the
[releases page](https://github.com/edmonkey-nz/laser-arcade/releases/latest) —
no Python needed. Unpack and run `laser-arcade`.

For real laser output, put your Helios shared library (`libHeliosDacAPI.so`,
`HeliosLaserDAC.dll`, or the `.dylib`) **next to the executable**; see
[TECHNICAL.md](TECHNICAL.md). Without it the app runs on the on-screen
simulator.

## Install from source

Python 3.9+, plus `pygame` and `numpy`. A Helios DAC is only needed for real
laser output — the simulator is the default.

```bash
cd laser-arcade
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python run.py                     # menu, on-screen simulator
python run.py --laser             # stream to the Helios (falls back to sim if no DAC)
python run.py --game pong         # jump straight into a game
python run.py --selftest          # headless check that every game runs
python run.py --version
```

(Running a downloaded build? Use `./laser-arcade` in place of `python run.py`.)

If `--laser` can't find a DAC it prints why and drops back to the simulator, so
it always runs.

## Controls

The menu is a carousel — one game at a time, so the laser only ever draws a
single title.

| Key | Action |
|-----|--------|
| ← → / A D | cycle games |
| ↑ ↓ / W S | move between the carousel and **CONFIG** |
| Enter / Space | launch, or open CONFIG |
| Esc | back to the menu from a game; quit from the menu |
| P | pause |
| Q | quit |

In game: **arrows / WASD** move, **Space** fires, **Shift** is the alternate
action (Asteroids hyperspace), **R** retries. Missile Command uses the mouse —
move to aim, click to fire.

**Gamepads work too**, alongside the keyboard, and are tested against both a
generic USB pad and a DualShock 4:

| Control | Action |
|---|---|
| D-pad / either stick | steer |
| Cross (button 0) | fire — also starts a game |
| Circle (button 1) | alternate / hyperspace |
| Options, or button 6 | back to the menu |
| Share, or button 10 | retry |

In Pong the **left stick is player 1 and the right stick is player 2**, so two
people can share one pad; in Missile Command the left stick moves the crosshair
and fire clicks.

Quitting is deliberately keyboard-only, so nobody can drop a cabinet to the
desktop mid-game.

USB or Bluetooth both work — the app just uses whatever the OS exposes as a
joystick, and pads can be plugged or unplugged while it's running. Several pads
can be connected at once; their input is combined, and the default button map
covers generic USB pads and DualShock 4s together, so you can mix them.

Pads disagree wildly about button numbering. If yours is mapped wrongly, run
`python controller_test.py`, press the buttons you care about, and put the
numbers it prints into `DEFAULT_BUTTON_MAP` in [`engine/joystick.py`](engine/joystick.py).
Stick axes are detected automatically. If a Bluetooth pad pairs but never shows
up, see the troubleshooting notes in [TECHNICAL.md](TECHNICAL.md) — that's
usually BlueZ refusing an unbonded HID connection, before the app is involved.

## Config

**CONFIG** from the main menu (Down from the carousel, then Enter). Saved to
`~/.laser-arcade/config.json` and reloaded next launch.

- **Config Output** — send the config screen to the laser, or keep it on screen
  only (it's text-heavy and hard work at low PPS).
- **PPS per game** — each game can have its own point rate.
- **Key bindings** — rebind any gameplay action. Esc, Q and P stay reserved.
- **Keystone H / V** — trapezoid correction for a projector that isn't square-on.
  Warps the DAC output only; the preview stays true.
- **Reset highscores** — clears them all. Asks for a second press to confirm.

High scores are kept per game in `~/.laser-arcade/highscores.json`. Pong (two
players) and Slipstream (a time trial) don't have one.

## More

Architecture, adding a game, Helios DAC setup, calibration, scanner tuning and
troubleshooting all live in **[TECHNICAL.md](TECHNICAL.md)**.

## Licence / credits

MIT licensed — see [LICENSE](LICENSE). Helios DAC and SDK by Gitle Mikkelsen
(Grix); udev/build notes per bitlasers.com. Asteroids, Pong, Missile Command,
Gyruss, Defender and Snake are homages to their respective arcade originals.
