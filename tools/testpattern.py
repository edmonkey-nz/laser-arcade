#!/usr/bin/env python3
"""Calibration / bring-up test pattern.

Run this first when hooking up a real projector. It streams a static frame with
a full-field border, a centre cross, diagonals and a roundness circle, so you
can set scale/offset on your galvo amp and figure out whether you need
--invert-x / --invert-y / --swap-xy before launching the game.

    python tools/testpattern.py                  # on-screen preview
    python tools/testpattern.py --output helios  # stream to the Helios DAC
    python tools/testpattern.py --output lasercube --lasercube-ip 10.0.0.5

It comes up **DISARMED at a 5% ceiling**, like everything else here. Press
Shift-. to arm (twice, to confirm) and . to disarm. This is the tool you use at
first light, so read SAFETY.md 5 before pointing it at anything: minimum power,
beam dump, eyewear for every wavelength, Remote Stop in hand.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from laser_output import SafeOutput, install_panic_handlers

from engine import pathplan
from engine.config import Settings
from engine.outputs import Simulator, make_backend, to_frame


def build_scene(cfg: Settings):
    c = cfg.beam((0, 255, 255))
    w = cfg.beam((255, 80, 80))
    g = cfg.beam((0, 255, 0))
    scene = []
    # full-field border (shows clipping / over-scan)
    scene.append(([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)], c))
    # centre cross
    scene.append(([(-0.12, 0), (0.12, 0)], g))
    scene.append(([(0, -0.12), (0, 0.12)], g))
    # diagonals
    scene.append(([(-1, -1), (1, 1)], w))
    scene.append(([(-1, 1), (1, -1)], w))
    # roundness circle (32-gon)
    circ = [(math.cos(a) * 0.6, math.sin(a) * 0.6)
            for a in [i / 32 * math.tau for i in range(33)]]
    scene.append((circ, c))
    # small corner ticks so you can identify orientation (top-right is doubled)
    scene.append(([(0.8, 1.0), (1.0, 1.0), (1.0, 0.8)], w))
    scene.append(([(0.86, 1.0), (1.0, 1.0), (1.0, 0.86)], w))
    return scene


def main():
    p = argparse.ArgumentParser(description="Laser test pattern")
    p.add_argument("--output", choices=("none", "helios", "lasercube"),
                   default="none")
    p.add_argument("--laser", action="store_true",
                   help="alias for --output helios")
    p.add_argument("--max-brightness", type=float, default=0.05,
                   help="brightness ceiling 0..1 (default %(default)s)")
    p.add_argument("--lasercube-ip", default="")
    p.add_argument("--lasercube-dry-run", action="store_true")
    p.add_argument("--pps", type=int, default=25000)
    p.add_argument("--fill", type=float, default=0.95)
    p.add_argument("--invert-x", action="store_true")
    p.add_argument("--invert-y", action="store_true")
    p.add_argument("--swap-xy", action="store_true")
    p.add_argument("--mono", action="store_true")
    a = p.parse_args()

    cfg = Settings()
    cfg.pps = a.pps
    cfg.fill = a.fill
    cfg.invert_x = a.invert_x
    cfg.invert_y = a.invert_y
    cfg.swap_xy = a.swap_xy
    cfg.monochrome = a.mono
    cfg.output_kind = "helios" if a.laser else a.output
    cfg.lasercube_ip = a.lasercube_ip
    cfg.lasercube_dry_run = a.lasercube_dry_run

    scene = build_scene(cfg)
    stream, _ = pathplan.plan(scene, cfg)
    frame = to_frame(stream, cfg)
    print(f"test pattern: {len(stream)} points @ {cfg.pps} pps "
          f"-> {cfg.pps/len(stream):.0f} fps")

    try:
        backend = make_backend(cfg.output_kind, cfg)
    except Exception as e:
        print(f"[laser] could not open {cfg.output_kind}: {e}")
        print("[laser] showing preview only.")
        from laser_output import NullOutput
        backend = NullOutput()

    import pygame
    pygame.init()
    surf = pygame.display.set_mode((cfg.sim_size, cfg.sim_size))
    pygame.display.set_caption("Laser test pattern  (Esc to quit)")
    sim = Simulator(surf, cfg)
    clock = pygame.time.Clock()

    laser = SafeOutput(backend, max_brightness=max(0.0, min(1.0, a.max_brightness)),
                       armed=False)
    print("[laser] output=%s  DISARMED  ceiling=%.0f%%  "
          "(shift-. arms, . disarms)"
          % (laser.name, laser.max_brightness * 100))
    install_panic_handlers(laser)

    confirm_arm = False
    try:
        running = True
        while running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.KEYDOWN:
                    if e.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif e.key == pygame.K_PERIOD:
                        if e.mod & pygame.KMOD_SHIFT:
                            if confirm_arm:
                                confirm_arm = False
                                if laser.arm():
                                    print("[laser] ARMED at %.0f%%"
                                          % (laser.max_brightness * 100))
                            else:
                                confirm_arm = True
                                print("[laser] press shift-. again to ARM")
                        else:
                            confirm_arm = False
                            laser.disarm()
                            print("[laser] DISARMED")
            surf.fill((0, 0, 0))
            if laser.name == "none":
                sim.set_status(None)
            elif laser.armed:
                sim.set_status(("ARMED  %d%%  %s"
                                % (round(laser.max_brightness * 100),
                                   laser.name.upper()), (255, 170, 0)))
            else:
                sim.set_status(("DISARMED  %s" % laser.name.upper(), (255, 60, 60)))
            sim.send(stream, cfg.pps)
            laser.write(frame, cfg.pps)
            pygame.display.flip()
            clock.tick(60)
    finally:
        laser.close()          # blanks first
        pygame.quit()


if __name__ == "__main__":
    main()
