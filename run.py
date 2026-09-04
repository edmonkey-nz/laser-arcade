#!/usr/bin/env python3
"""Laser Arcade -- entry point.

    python run.py                            # menu (on-screen simulator)
    python run.py --output helios            # menu, streaming to a Helios DAC
    python run.py --output lasercube         # ...or a LaserCube, over the network
    python run.py --game pong --output helios
    python run.py --pps 30000 --max-step 35 --invert-x --output helios

Reserved keys everywhere: Esc (game -> menu, menu -> quit), Q (quit),
P (pause), Tab (live tuner: PPS/POINTS for whatever is on screen, adjusted with
- = and [ ]), '.' (DISARM the laser), Shift-'.' (ARM it, twice to confirm).
In the menu: Up/Down choose, Enter launches.

Laser output always starts DISARMED with the brightness ceiling at 5%, and
neither is remembered between runs. Arming, the brightness ceiling and the
whole CONFIG screen are keyboard-only -- a gamepad plays games and nothing
else. Read SAFETY.md before connecting a projector.
"""
from __future__ import annotations

import argparse

from engine import __version__, store
from engine.config import Settings
from engine.shell import Shell
from games import GAMES, BY_KEY


def parse_args():
    s = Settings()
    p = argparse.ArgumentParser(description="Laser Arcade for the Helios DAC")
    p.add_argument("--version", action="version",
                   version="Laser Arcade %s" % __version__)
    p.add_argument("--game", choices=sorted(BY_KEY), help="launch straight into a game")
    p.add_argument("--selftest", action="store_true",
                   help="run a headless check of every game and exit")

    out = p.add_argument_group("output")
    out.add_argument("--output", choices=("none", "helios", "lasercube"),
                     default=None, help="laser backend (default: remembered, "
                     "else none). Output always starts DISARMED.")
    out.add_argument("--laser", action="store_true",
                     help="alias for --output helios")
    out.add_argument("--no-sim", action="store_true", help="disable the on-screen preview")
    out.add_argument("--fullscreen", action="store_true")
    out.add_argument("--sim-size", type=int, default=s.sim_size)

    safe = p.add_argument_group("laser safety")
    safe.add_argument("--max-brightness", type=float, default=s.max_brightness,
                      help="brightness ceiling 0..1 (default %(default)s). A "
                           "creative limiter, NOT a safety interlock -- read "
                           "SAFETY.md.")

    lc = p.add_argument_group("lasercube")
    lc.add_argument("--lasercube-ip", default=s.lasercube_ip,
                    help="device IP (default: discover by broadcast)")
    lc.add_argument("--lasercube-dry-run", action="store_true",
                    help="pack and rate-control, but transmit nothing")
    lc.add_argument("--lasercube-point-order", choices=("xyrgb", "rgbxy"),
                    default=s.lasercube_point_order)
    lc.add_argument("--list-lasercubes", action="store_true",
                    help="discover LaserCubes on the network and exit")

    dac = p.add_argument_group("dac / timing")
    dac.add_argument("--pps", type=int, default=None,
                     help="default point rate (default: remembered, else %d)"
                          % s.pps)
    dac.add_argument("--fps", type=int, default=s.target_fps)
    dac.add_argument("--device", type=int, default=s.dac_device)

    tune = p.add_argument_group("scanner tuning")
    tune.add_argument("--max-step", type=int, default=s.max_step)
    tune.add_argument("--corner-dwell", type=int, default=s.corner_dwell)
    tune.add_argument("--blank-dwell", type=int, default=s.blank_dwell)
    tune.add_argument("--lit-budget", type=int, default=s.lit_budget)
    tune.add_argument("--fill", type=float, default=s.fill)
    tune.add_argument("--scale", type=float, default=None,
                      help="overall output size 0.10..1.0 (default: remembered, "
                           "else 1.0)")
    tune.add_argument("--invert-x", action="store_true")
    tune.add_argument("--invert-y", action="store_true")
    tune.add_argument("--swap-xy", action="store_true")

    look = p.add_argument_group("look / sound")
    look.add_argument("--mono", action="store_true")
    look.add_argument("--brightness", type=float, default=s.brightness)
    look.add_argument("--show-blanking", action="store_true")
    look.add_argument("--no-audio", action="store_true")
    look.add_argument("--volume", type=float, default=s.volume)

    a = p.parse_args()
    if a.selftest:
        from engine.selftest import run as selftest
        raise SystemExit(selftest())
    if a.list_lasercubes:
        from lasercube_output import discover
        found = discover()
        for d in found:
            print(d)
        if not found:
            print("no LaserCubes found on the network")
        raise SystemExit(0 if found else 1)
    # Settings the config screen also owns are collected here rather than
    # written onto `s` directly: they are re-applied in __main__ *after* the
    # persisted config, which would otherwise clobber an explicit choice on the
    # command line. Not passing one means "whatever this cabinet was last set
    # to", which for a fresh install is the Settings default.
    #
    # --output wins over --laser, the old spelling of --output helios.
    explicit = {}
    if a.output is not None:
        explicit["output_kind"] = a.output
    elif a.laser:
        explicit["output_kind"] = "helios"
    if a.pps is not None:
        explicit["pps"] = a.pps
    if a.scale is not None:
        explicit["output_scale"] = max(0.10, min(1.0, a.scale))
    # store_true, so these can only turn a flip *on* from the command line;
    # turning one off again is the config screen's job.
    if a.invert_x:
        explicit["invert_x"] = True
    if a.invert_y:
        explicit["invert_y"] = True
    s.max_brightness = max(0.0, min(1.0, a.max_brightness))
    s.lasercube_ip = a.lasercube_ip
    s.lasercube_dry_run = a.lasercube_dry_run
    s.lasercube_point_order = a.lasercube_point_order
    s.use_sim = not a.no_sim
    s.fullscreen = a.fullscreen
    s.sim_size = a.sim_size
    s.target_fps = a.fps
    s.dac_device = a.device
    s.max_step = a.max_step
    s.corner_dwell = a.corner_dwell
    s.blank_dwell = a.blank_dwell
    s.lit_budget = a.lit_budget
    s.fill = a.fill
    s.swap_xy = a.swap_xy
    s.monochrome = a.mono
    # Clamped: beam() multiplies straight into the 0..255 colour channels, and
    # an unclamped multiplier used to overflow silently through the DAC's
    # uint8 fields -- --brightness 5.0 wrapped rather than saturating.
    s.brightness = max(0.0, min(1.0, a.brightness))
    s.sim_show_blanking = a.show_blanking
    s.audio = not a.no_audio
    s.volume = a.volume
    if not s.use_sim and explicit.get("output_kind", "none") == "none":
        s.use_sim = True
    return s, a.game, explicit


if __name__ == "__main__":
    cfg, game_key, explicit = parse_args()
    store.apply_to(cfg, store.load())     # pps, key bindings, output geometry
    for name, value in explicit.items():  # command line beats the saved config
        setattr(cfg, name, value)
    if not cfg.use_sim and cfg.output_kind == "none":
        cfg.use_sim = True                # otherwise there is nothing to see
    start = BY_KEY[game_key] if game_key else None
    Shell(cfg, GAMES).run(start_game=start)
