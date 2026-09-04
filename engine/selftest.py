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

    def output_scale_shrinks_the_field():
        """OUTPUT SCALE is framing, so unlike the ceiling it is applied in the
        mapper and therefore shows up in the preview too. What must hold is that
        it is upstream of to_frame(), i.e. still in front of SafeOutput."""
        from . import pathplan
        square = [([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)],
                   (255, 255, 255))]
        c = Settings().dac_range / 2.0

        def extent(cfg):
            stream, _ = pathplan.plan(square, cfg)
            return max(max(abs(p[0] - c), abs(p[1] - c)) for p in stream)

        cfg = Settings()
        full = extent(cfg)
        cfg.output_scale = 0.5
        assert abs(extent(cfg) - full * 0.5) <= 2, \
            "50% scale did not halve the field"
        # Floored, because squeezing the whole scan into a spot is hotter, not
        # safer -- a 0 here would park every point on the centre.
        cfg.output_scale = 0.0
        assert abs(extent(cfg) - full * 0.10) <= 2, "output_scale floor is gone"
    check("output scale shrinks the field", output_scale_shrinks_the_field)

    def geometry_round_trips():
        from . import store
        cfg = Settings()
        cfg.pps, cfg.output_scale = 21000, 0.6
        cfg.invert_x, cfg.invert_y = True, False
        cfg.lit_budget = 450
        cfg.game_pps, cfg.game_points = {"pong": 9000}, {"pong": 300}
        fresh = Settings()
        store.apply_to(fresh, store.from_settings(cfg))
        got = (fresh.pps, fresh.output_scale, fresh.invert_x, fresh.invert_y)
        assert got == (21000, 0.6, True, False), \
            "output geometry did not survive a save/load: %r" % (got,)
        assert (fresh.pps_for("pong"), fresh.points_for("pong")) == (9000, 300), \
            "per-game pps/points overrides did not survive a save/load"
        assert (fresh.pps_for(None), fresh.points_for(None)) == (21000, 450), \
            "the defaults behind the overrides did not survive a save/load"
    check("pps/points/scale/flip persist", geometry_round_trips)

    def per_game_points_reach_the_planner():
        """A POINTS override has to land on Settings.lit_budget around the
        plan() call and be put back afterwards -- the config screen renders from
        the same Settings and must show what is stored, not the last frame."""
        from . import pathplan
        cfg = Settings()
        cfg.pps = 14000
        busy = [([(x / 10.0 - 1, -0.9), (x / 10.0 - 1, 0.9)], (255, 255, 255))
                for x in range(20)]

        def points(budget):
            cfg.lit_budget = budget
            return len(pathplan.plan(busy, cfg)[0])

        assert points(1200) > points(300), \
            "the point budget did not change the frame it produced"
        cfg.lit_budget = 600
        cfg.game_points = {"pong": 250}
        assert cfg.points_for("pong") == 250 and cfg.points_for(None) == 600, \
            "points_for() ignored the override or the default"
    check("per-game point budget takes effect", per_game_points_reach_the_planner)

    def ceiling_steps_by_ten():
        """10% steps, snapped to the grid, with the 5% crossing still confirmed."""
        from .shell import Shell
        from games import GAMES

        class _Mute:
            def play(self, *a):
                pass

        sh = Shell(Settings(), GAMES)
        sh.laser = SafeOutput(_SpyBackend(), max_brightness=0.05)
        sh.menu_sfx = _Mute()
        try:
            sh._adjust_ceiling(+1)
            assert sh.laser.max_brightness == 0.05, \
                "the first step past 5% went through without confirming"
            assert sh.confirm_ceiling, "no confirm was armed crossing 5%"
            sh._adjust_ceiling(+1)
            assert abs(sh.laser.max_brightness - 0.10) < 1e-9, \
                "step off 5% did not snap to 10%%: %r" % sh.laser.max_brightness
            for _ in range(20):
                sh._adjust_ceiling(+1)
            assert sh.laser.max_brightness == 1.0, \
                "100%% is not reachable: %r" % sh.laser.max_brightness
            sh._adjust_ceiling(-1)
            assert abs(sh.laser.max_brightness - 0.90) < 1e-9, \
                "down step was not 10%%: %r" % sh.laser.max_brightness
            for _ in range(20):
                sh._adjust_ceiling(-1)
            assert sh.laser.max_brightness == 0.0, "cannot get back to zero"
        finally:
            sh.laser.close()
    check("ceiling steps 10%, confirms at 5%", ceiling_steps_by_ten)

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

    def pad_cannot_reach_the_tuner():
        """The live tuner is an operator control, so it is keyboard-only like
        arm, quit and the config screen -- and its keys must stay reserved only
        while it is open, or a game bound to '[' would silently lose it."""
        from .shell import Shell, TUNER_KEYS
        from games import GAMES

        class _Mute:
            def play(self, *a):
                pass

        cfg = Settings()
        sh = Shell(cfg, GAMES)
        sh.menu_sfx = _Mute()
        sh.mode = "game"
        sh.game = GAMES[0](cfg)

        assert sh._reserved(pygame.K_TAB, 0, from_pad=True) is True
        assert not sh.tuner_open, "a gamepad opened the live tuner"
        for k in TUNER_KEYS:
            assert sh._reserved(k, 0) is False, \
                "tuner key %s was reserved with the tuner closed" % k

        sh._reserved(pygame.K_TAB, 0)
        assert sh.tuner_open, "the keyboard could not open the tuner"

        before = (cfg.pps_for(sh.game.key), cfg.points_for(sh.game.key))
        for k in TUNER_KEYS:
            assert sh._reserved(k, 0, from_pad=True) is True, \
                "pad key %s leaked past the tuner" % k
        assert (cfg.pps_for(sh.game.key), cfg.points_for(sh.game.key)) == before, \
            "a gamepad retuned the running game"

        # ...and the keyboard edits the RUNNING GAME, not the defaults
        sh._reserved(pygame.K_RIGHTBRACKET, 0)
        sh._reserved(pygame.K_EQUALS, 0)
        assert cfg.points_for(sh.game.key) == before[1] + 50, "']' did not adjust"
        assert cfg.pps_for(sh.game.key) == before[0] + 1000, "'=' did not adjust"
        assert cfg.lit_budget == Settings().lit_budget and cfg.pps == Settings().pps, \
            "tuning a game moved the global defaults"
        sh._reserved(pygame.K_BACKSPACE, 0)
        assert (cfg.pps_for(sh.game.key), cfg.points_for(sh.game.key)) == before, \
            "BKSP did not drop the game back onto the defaults"

        # BKSP at the menu restores the shipped defaults -- the only way back
        # out of a tuning session that went somewhere silly.
        sh.mode, sh.game = "menu", None
        sh._reserved(pygame.K_MINUS, 0)
        sh._reserved(pygame.K_LEFTBRACKET, 0)
        sh._reserved(pygame.K_BACKSPACE, 0)
        assert (cfg.pps, cfg.lit_budget) == (Settings().pps, Settings().lit_budget), \
            "BKSP at the menu did not restore the shipped defaults"

        # ...and closing must NOT write to disk. Checked via the dirty flag
        # rather than the filesystem: the test must not touch the operator's
        # real ~/.laser-arcade/config.json, and _save_tuning is the only thing
        # that clears it.
        sh.tuner_dirty = True
        sh._reserved(pygame.K_TAB, 0)
        assert not sh.tuner_open, "TAB did not close the tuner"
        assert sh.tuner_dirty, "closing the tuner persisted an experiment to disk"
    check("gamepad cannot reach the tuner", pad_cannot_reach_the_tuner)

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
