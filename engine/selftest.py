"""Headless self-check: build every game, run it, and plan real frames, then
verify the laser safety layer holds.

Lives in the engine (not tools/) so it is bundled into the packaged builds --
CI runs the *executable* with `--selftest`, which is what proves a release
binary actually works rather than merely that PyInstaller exited 0.

The safety half matters more than the game half. It is the only automated
check that the output comes up disarmed, that the brightness ceiling actually
clamps, that the watchdog fires, and that a gamepad cannot reach the arm gate
or the config screen. None of it needs a laser attached.
"""
from __future__ import annotations

import os
import sys
import time


class _SpyBackend:
    """A LaserOutput that records what it was asked to emit, and emits nothing."""

    name = "spy"
    paces_loop = False

    def __init__(self, refuse_enable=False, has_gate=False):
        self.last_points = 0
        self.frames = []
        self.blanks = 0
        self.closed = False
        self.enabled = False
        self._refuse = refuse_enable
        if has_gate:
            self.enable = self._enable
            self.disable = self._disable

    def _enable(self):
        if self._refuse:
            return False
        self.enabled = True
        return True

    def _disable(self):
        self.enabled = False
        return True

    def write(self, frame, pps):
        self.last_points = len(frame)
        self.frames.append(frame.copy())
        return True

    def blank(self):
        self.blanks += 1
        return True

    def close(self):
        self.closed = True


def _safety_checks(verbose: bool = True):
    """Returns a list of (name, exception) failures -- empty means all passed."""
    import numpy as np
    import pygame

    from laser_output import SafeOutput
    from .config import Settings
    from .game import InputState
    from .outputs.laser import to_frame

    failures = []

    def check(name, fn):
        try:
            fn()
            if verbose:
                print("  %-34s ok" % name)
        except Exception as exc:                         # noqa: BLE001
            failures.append((name, exc))
            if verbose:
                print("  %-34s FAILED: %r" % (name, exc))

    white = np.array([[100, 200, 255, 255, 255, 255]] * 8, dtype=np.int32)

    # -- the arm gate --
    def starts_disarmed():
        spy = _SpyBackend()
        out = SafeOutput(spy, max_brightness=1.0)
        assert not out.armed, "constructed ARMED -- must always start disarmed"
        out.write(white, 20000)
        sent = spy.frames[-1]
        assert (sent[:, 2:6] == 0).all(), "disarmed output emitted colour"
        assert (sent[:, 0:2] == white[:, 0:2]).all(), \
            "disarmed output dropped geometry (it must keep scanning, dark)"
        out.close()
    check("starts disarmed, emits darkness", starts_disarmed)

    def ceiling_clamps():
        spy = _SpyBackend()
        out = SafeOutput(spy, max_brightness=0.05)
        out.arm()
        out.write(white, 20000)
        sent = spy.frames[-1]
        assert sent[:, 2:6].max() <= 13, \
            "ceiling did not clamp: max=%d" % sent[:, 2:6].max()
        out.close()
    check("5% ceiling clamps full white", ceiling_clamps)

    def refused_arm_stays_disarmed():
        spy = _SpyBackend(refuse_enable=True, has_gate=True)
        out = SafeOutput(spy, max_brightness=0.05)
        assert out.arm() is False, "arm() reported success over a refusing device"
        assert not out.armed, "showing ARMED over a device that refused"
        out.close()
    check("device refusing arm stays disarmed", refused_arm_stays_disarmed)

    # -- watchdog --
    def watchdog_fires():
        spy = _SpyBackend()
        out = SafeOutput(spy, max_brightness=0.05)
        out.arm()
        out.write(white, 20000)
        before = spy.blanks
        time.sleep(0.6)                      # threshold is max(250ms, 3 frames)
        assert spy.blanks > before, "watchdog did not blank a stalled loop"
        out.write(white, 20000)              # recovery releases it
        out.close()
    check("watchdog blanks a stalled loop", watchdog_fires)

    # -- teardown --
    def close_blanks():
        spy = _SpyBackend()
        out = SafeOutput(spy, max_brightness=0.05)
        out.arm()
        out.write(white, 20000)
        out.close()
        assert spy.blanks >= 1, "close() released the device without blanking"
        assert spy.closed, "close() did not close the backend"
        out.close()                          # idempotent: atexit calls it again
    check("close() blanks, and is idempotent", close_blanks)

    # -- the frame adapter --
    def frame_conversion():
        cfg = Settings()
        pts = [(0, 0, 10, 20, 30), (4095, 4095, 255, 0, 0), (2047, 100, 0, 0, 7)]
        f = to_frame(pts, cfg)
        assert f.shape == (3, 6), "wrong frame shape %r" % (f.shape,)
        assert f.dtype == np.int32, "wrong dtype %r" % f.dtype
        assert list(f[:, 5]) == [30, 255, 7], \
            "i must be max(r,g,b), got %r" % list(f[:, 5])
        assert list(f[:, 0]) == [0, 4095, 2047], "keystone-free x was not identity"
        assert list(f[:, 1]) == [0, 4095, 100], "keystone-free y was not identity"
        assert to_frame([], cfg).shape == (0, 6), "empty frame mishandled"
    check("to_frame: i=max(rgb), identity warp", frame_conversion)

    def keystone_applies_and_stays_in_range():
        cfg = Settings()
        cfg.keystone_h, cfg.keystone_v = 0.3, -0.2
        pts = [(0, 0, 255, 255, 255), (4095, 4095, 255, 255, 255),
               (2047, 2047, 255, 255, 255)]
        f = to_frame(pts, cfg)
        assert f[:, 0:2].min() >= 0 and f[:, 0:2].max() <= 4095, \
            "keystone pushed points outside the 12-bit field"
        # the centre point is the warp's fixed point
        assert abs(int(f[2, 0]) - 2047) <= 1 and abs(int(f[2, 1]) - 2047) <= 1, \
            "keystone moved the field centre"
        assert list(f[:, 0]) != [0, 4095, 2047], "keystone had no effect"
    check("keystone warps, stays in field", keystone_applies_and_stays_in_range)

    # -- keyboard-only gating (the cabinet's public-facing rule) --
    def pad_cannot_touch_the_laser():
        from .shell import Shell
        from games import GAMES

        cfg = Settings()
        sh = Shell(cfg, GAMES)
        spy = _SpyBackend()
        sh.laser = SafeOutput(spy, max_brightness=0.05)
        try:
            sh.mode = "menu"
            # a pad pressing shift-. must not arm, and must not even arm the
            # two-press confirm
            assert sh._reserved(pygame.K_PERIOD, pygame.KMOD_SHIFT,
                                from_pad=True) is True
            assert not sh.confirm_arm, "pad reached the ARM confirm"
            assert not sh.laser.armed, "a gamepad armed the laser"

            # ...nor disarm one that is armed (keyboard-only, both directions)
            sh.laser.arm()
            assert sh._reserved(pygame.K_PERIOD, 0, from_pad=True) is True
            assert sh.laser.armed, "a gamepad disarmed the laser"

            # the keyboard can do both
            sh._reserved(pygame.K_PERIOD, 0, from_pad=False)
            assert not sh.laser.armed, "keyboard '.' failed to disarm"
            sh._reserved(pygame.K_PERIOD, pygame.KMOD_SHIFT)
            assert sh.confirm_arm, "shift-. did not arm the confirm"
            sh._reserved(pygame.K_PERIOD, pygame.KMOD_SHIFT)
            assert sh.laser.armed, "two shift-. presses failed to arm"

            # quitting stays keyboard-only (pre-existing rule, guard it)
            sh._reserved(pygame.K_q, 0, from_pad=True)
            assert not sh.quit_requested, "a gamepad quit the arcade"
        finally:
            sh.laser.close()
    check("gamepad cannot arm/disarm/quit", pad_cannot_touch_the_laser)

    def pad_cannot_reach_config():
        from .shell import Shell
        from games import GAMES

        class _Mute:
            def play(self, *a):
                pass

        cfg = Settings()
        sh = Shell(cfg, GAMES)
        spy = _SpyBackend()
        sh.laser = SafeOutput(spy, max_brightness=0.05)
        sh.menu_sfx = _Mute()
        try:
            nothing = InputState(set(), set(), (0.0, 0.0), False, False)
            pad_up = InputState({pygame.K_UP}, {pygame.K_UP}, (0.0, 0.0), False, False)
            pad_go = InputState({pygame.K_SPACE}, {pygame.K_SPACE},
                                (0.0, 0.0), False, False)

            # pad Up/Down must not move focus onto CONFIG
            sh._menu_step(pad_up, nothing)
            assert sh.menu_focus == "carousel", \
                "a gamepad moved menu focus onto CONFIG"

            # even with focus somehow on config, a pad press must not open it
            sh.menu_focus = "config"
            sh._menu_step(pad_go, nothing)
            assert sh.mode == "menu", "a gamepad opened the CONFIG screen"

            # the keyboard opens it
            sh._menu_step(nothing, pad_go)
            assert sh.mode == "config", "the keyboard could not open CONFIG"

            # inside config the pad is completely inert
            before = (cfg.keystone_h, cfg.output_kind, sh.cfg_sel)
            for k in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT,
                      pygame.K_RIGHT, pygame.K_RETURN, pygame.K_SPACE,
                      pygame.K_ESCAPE):
                assert sh._reserved(k, 0, from_pad=True) is True, \
                    "pad key %d leaked into the config screen" % k
            assert sh.mode == "config", "a gamepad escaped the CONFIG screen"
            assert (cfg.keystone_h, cfg.output_kind, sh.cfg_sel) == before, \
                "a gamepad changed a config value"
        finally:
            sh.laser.close()
    check("gamepad cannot reach CONFIG", pad_cannot_reach_config)

    def config_screen_renders_in_every_state():
        """Every row, in every confirm/armed/raised state, actually draws.

        The config screen is now safety-relevant, and its rows are built with
        printf-style formatting -- an unescaped '%' in a label is a crash on
        exactly the path an operator takes to raise the ceiling. Walk the whole
        thing rather than trusting inspection.
        """
        from .shell import Shell
        from games import GAMES

        class _Mute:
            def play(self, *a):
                pass

        cfg = Settings()
        sh = Shell(cfg, GAMES)
        sh.laser = SafeOutput(_SpyBackend(), max_brightness=0.05)
        sh.menu_sfx = _Mute()
        try:
            rows = sh._config_rows()
            for armed in (False, True):
                sh.laser._armed = armed
                for ceiling in (0.05, 0.60):
                    sh.laser.max_brightness = ceiling
                    for flags in ((0, 0, 0), (1, 1, 1)):
                        sh.confirm_scores, sh.confirm_arm, sh.confirm_ceiling = flags
                        for i in range(len(rows)):
                            sh.cfg_sel = i
                            sh.laser_msg = "TEST MESSAGE 100%"
                            assert sh._config_scene(rows), \
                                "config row %d rendered nothing" % i
            sh.diag_rows = sh.laser.diagnostics()
            assert sh._diag_scene(), "TEST DEVICE screen rendered nothing"
        finally:
            sh.laser.close()
    check("config screen renders in all states", config_screen_renders_in_every_state)

    def ceiling_is_not_persisted():
        from . import store
        cfg = Settings()
        cfg.max_brightness = 0.85
        data = store.from_settings(cfg)
        assert "max_brightness" not in data, \
            "the brightness ceiling was persisted -- it must reset to 5% each launch"
        assert "armed" not in data, "an arm state was persisted"
        fresh = Settings()
        store.apply_to(fresh, {"max_brightness": 0.85, "armed": True})
        assert fresh.max_brightness == 0.05, \
            "a stored ceiling was loaded: %r" % fresh.max_brightness
    check("ceiling/arm never persist to disk", ceiling_is_not_persisted)

    return failures


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

        if verbose:
            print("laser safety layer:")
        failures.extend(_safety_checks(verbose))
    finally:
        pygame.quit()

    if verbose:
        print("self-test %s" % ("PASSED" if not failures else
                                "FAILED (%d)" % len(failures)))
    return 1 if failures else 0
