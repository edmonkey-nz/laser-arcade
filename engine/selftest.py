"""Headless self-check: build every game, run it, and plan real frames.

Lives in the engine (not tools/) so it is bundled into the packaged builds --
CI runs the *executable* with `--selftest`, which is what proves a release
binary actually works rather than merely that PyInstaller exited 0.
"""
from __future__ import annotations

import os
import sys


def run(frames: int = 120, verbose: bool = True) -> int:
    """Returns a process exit code: 0 all good, 1 something failed."""
    # must be set before pygame initialises the display/audio subsystems
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    import pygame

    from . import __version__, pathplan
    from .config import Settings
    from .game import InputState
    from .keymap import KeyMap

    failures = []
    if verbose:
        print("Laser Arcade %s self-test (python %s)"
              % (__version__, sys.version.split()[0]))

    pygame.init()
    pygame.display.set_mode((64, 64))
    try:
        from games import GAMES

        cfg = Settings()
        cfg.keymap = KeyMap()
        for cls in GAMES:
            try:
                game = cls(cfg)
                game.start()
                game.set_high_score(1234)
                points = 0
                for i in range(frames):
                    keys = {pygame.K_SPACE, pygame.K_LEFT} if i % 5 else set()
                    game.update(1.0 / 60.0, InputState(
                        held=keys, pressed=keys, mouse_pos=(0.2, -0.1),
                        mouse_down=bool(i % 5), mouse_click=(i % 11 == 0)))
                    scene = game.scene(i / 60.0)
                    stream, _ = pathplan.plan(scene, cfg)
                    points = len(stream)
                score = game.score()
                if verbose:
                    print("  %-11s ok  %5d points/frame  score=%s"
                          % (cls.name, points, "-" if score is None else score))
            except Exception as exc:                     # noqa: BLE001
                failures.append((cls.name, exc))
                if verbose:
                    print("  %-11s FAILED: %r" % (cls.name, exc))
    finally:
        pygame.quit()

    if verbose:
        print("self-test %s" % ("PASSED" if not failures else
                                "FAILED (%d)" % len(failures)))
    return 1 if failures else 0
