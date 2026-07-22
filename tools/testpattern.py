#!/usr/bin/env python3
"""Calibration / bring-up test pattern.

Run this first when hooking up a real projector. It streams a static frame with
a full-field border, a centre cross, diagonals and a roundness circle, so you
can set scale/offset on your galvo amp and figure out whether you need
--invert-x / --invert-y / --swap-xy before launching the game.

    python tools/testpattern.py            # on-screen preview
    python tools/testpattern.py --laser     # stream to the Helios DAC
    python tools/testpattern.py --laser --invert-y --fill 0.9
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import pathplan
from engine.config import Settings
from engine.outputs import HeliosOutput, Simulator


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
    p.add_argument("--laser", action="store_true")
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

    scene = build_scene(cfg)
    stream, _ = pathplan.plan(scene, cfg)
    print(f"test pattern: {len(stream)} points @ {cfg.pps} pps "
          f"-> {cfg.pps/len(stream):.0f} fps")

    outputs = []
    if a.laser:
        h = HeliosOutput(cfg.helios_libs, cfg.dac_device, cfg.dac_max_points)
        if h.start():
            outputs.append(h)
        else:
            print("[laser] no DAC; showing preview instead.")

    import pygame
    pygame.init()
    surf = pygame.display.set_mode((cfg.sim_size, cfg.sim_size))
    pygame.display.set_caption("Laser test pattern  (Esc to quit)")
    outputs.append(Simulator(surf, cfg))
    clock = pygame.time.Clock()

    try:
        running = True
        while running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (
                        e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q)):
                    running = False
            surf.fill((0, 0, 0))
            for o in outputs:
                o.send(stream, cfg.pps)
            pygame.display.flip()
            clock.tick(60)
    finally:
        for o in outputs:
            o.close()
        pygame.quit()


if __name__ == "__main__":
    main()
