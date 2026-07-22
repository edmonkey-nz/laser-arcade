#!/usr/bin/env python3
"""Laser Arcade -- entry point.

    python run.py                       # menu (on-screen simulator)
    python run.py --laser               # menu, streaming to the Helios DAC
    python run.py --game pong --laser   # jump straight into a game
    python run.py --pps 30000 --max-step 35 --invert-x --laser

Reserved keys everywhere: Esc (game -> menu, menu -> quit), Q (quit),
P (pause). In the menu: Up/Down choose, Enter launches.
"""
from __future__ import annotations

import argparse

from engine.config import Settings
from engine.shell import Shell
from engine import store
from games import GAMES, BY_KEY


def parse_args():
    s = Settings()
    p = argparse.ArgumentParser(description="Laser Arcade for the Helios DAC")
    p.add_argument("--game", choices=sorted(BY_KEY), help="launch straight into a game")

    out = p.add_argument_group("output")
    out.add_argument("--laser", action="store_true", help="stream to the Helios DAC")
    out.add_argument("--no-sim", action="store_true", help="disable the on-screen preview")
    out.add_argument("--fullscreen", action="store_true")
    out.add_argument("--sim-size", type=int, default=s.sim_size)

    dac = p.add_argument_group("dac / timing")
    dac.add_argument("--pps", type=int, default=s.pps)
    dac.add_argument("--fps", type=int, default=s.target_fps)
    dac.add_argument("--device", type=int, default=s.dac_device)

    tune = p.add_argument_group("scanner tuning")
    tune.add_argument("--max-step", type=int, default=s.max_step)
    tune.add_argument("--corner-dwell", type=int, default=s.corner_dwell)
    tune.add_argument("--blank-dwell", type=int, default=s.blank_dwell)
    tune.add_argument("--lit-budget", type=int, default=s.lit_budget)
    tune.add_argument("--fill", type=float, default=s.fill)
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
    s.use_laser = a.laser
    s.use_sim = not a.no_sim
    s.fullscreen = a.fullscreen
    s.sim_size = a.sim_size
    s.pps = a.pps
    s.target_fps = a.fps
    s.dac_device = a.device
    s.max_step = a.max_step
    s.corner_dwell = a.corner_dwell
    s.blank_dwell = a.blank_dwell
    s.lit_budget = a.lit_budget
    s.fill = a.fill
    s.invert_x = a.invert_x
    s.invert_y = a.invert_y
    s.swap_xy = a.swap_xy
    s.monochrome = a.mono
    s.brightness = a.brightness
    s.sim_show_blanking = a.show_blanking
    s.audio = not a.no_audio
    s.volume = a.volume
    if not s.use_sim and not s.use_laser:
        s.use_sim = True
    return s, a.game


if __name__ == "__main__":
    cfg, game_key = parse_args()
    store.apply_to(cfg, store.load())     # per-game pps, key bindings, pincushion
    start = BY_KEY[game_key] if game_key else None
    Shell(cfg, GAMES).run(start_game=start)
