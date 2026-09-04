"""The arcade shell.

Owns the window, the outputs (on-screen simulator and/or Helios DAC), and the
top-level state machine: a laser-drawn MENU (a one-game-at-a-time carousel,
plus a CONFIG option below it), the running GAME, and the CONFIG screen. Games
are plugged in as `engine.game.Game` subclasses; the shell drives them
generically and knows nothing game-specific.

Reserved keys (the shell eats these; games never see them):
    Esc   -- in a game: back to menu;  in config: save & back;  in menu: quit
    Q     -- quit from anywhere
    P     -- pause / resume the running game
    Tab   -- open/close the live tuner (PPS + POINTS for what's on screen)
    - =   -- with the tuner open: point rate down / up
    [ ]   -- with the tuner open: point budget down / up
    \     -- with the tuner open: SAVE (nothing else writes tuning to disk)
    Bksp  -- with the tuner open: drop back to the defaults
    .     -- DISARM the laser, instantly, from anywhere
    Shift-. -- ARM the laser (press twice to confirm)
In the menu: Left/Right cycle the game carousel, Up/Down move focus between
the carousel and the CONFIG button, Enter launches / opens whatever's focused.
In config: Up/Down move, Left/Right adjust a value, Enter binds a key / resets
/ saves / toggles.

**Keyboard vs gamepad.** A pad is the public-facing control on a cabinet, so it
gets gameplay and nothing else. Quitting was already keyboard-only for that
reason; the CONFIG screen and the whole arm/brightness surface now are too. A
pad cannot open config, cannot move menu focus onto it, is inert inside it, and
can neither arm nor disarm the laser. See `_reserved` and `_kbd_held`.
"""
from __future__ import annotations

from typing import List, Optional, Type

import pygame

from laser_output import SafeOutput, install_panic_handlers

from . import font
from . import pathplan
from . import store
from .audio import SoundBank, blip
from .config import Settings
from .game import Game, InputState
from .joystick import JoystickManager
from .keymap import ACTIONS
from .outputs import Simulator, make_backend, to_frame
from .tuner import Tuner


# World units per second for the stick-driven virtual cursor. The world is 2
# units across, so this crosses the screen in a little over a second.
PAD_CURSOR_SPEED = 1.8

# The live tuner's adjust keys. Chosen to miss every default gameplay binding
# (arrows, WASD, space, shift, H, R) so the game stays playable while you tune,
# and to sit in pairs under one hand.
TUNER_KEYS = frozenset((pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET,
                        pygame.K_MINUS, pygame.K_EQUALS, pygame.K_BACKSPACE,
                        pygame.K_BACKSLASH))


def _blank_stream(cfg):
    """A few dark points parked at the field centre.

    Used when we deliberately don't want the current scene on the laser but
    still must feed it something -- an unfed DAC repeats its last frame
    indefinitely, so "send nothing" leaves the previous image painted.
    """
    c = cfg.dac_range // 2
    return [(c, c, 0, 0, 0)] * 4


def _screen_to_world(mx: float, my: float, size: int, fill: float):
    """Screen pixel -> world [-1, 1] space, matching the on-screen preview's
    upright mapping (independent of invert/swap/keystone, which are DAC-output
    calibration only). This is what a game should use for mouse aiming."""
    c = size / 2.0
    hp = c * fill
    wx = (mx - c) / hp
    wy = -(my - c) / hp
    return (wx, wy)


class Shell:
    def __init__(self, cfg: Settings, games: List[Type[Game]]):
        self.cfg = cfg
        self.games = games
        self.sel = 0                 # carousel index into self.games
        self.menu_focus = "carousel"  # carousel | config
        self.cfg_sel = 0
        self.capture_action: Optional[str] = None
        self.confirm_scores = False  # armed "RESET HIGHSCORES" row
        self.confirm_arm = False     # armed "LASER -> ARM" row / Shift-. press
        self.confirm_ceiling = False  # armed "raise the ceiling above 5%" step
        self.diag_rows: Optional[list] = None   # TEST DEVICE readout, or None
        self.laser_msg = ""          # one-line status shown on the config screen
        self.mode = "menu"          # menu | game | config
        # The tuner is an OVERLAY, not a mode: the game underneath keeps running
        # while it is up, which is the whole point of it.
        self.tuner: Optional[Tuner] = None   # built in run(), needs pygame.font
        self.tuner_open = False
        self.tuner_dirty = False
        self.quit_requested = False
        # populated from disk in run(); defined here so every method can rely
        # on them existing
        self.highscores: dict = {}
        self._scores_dirty = False

    # -- laser ---------------------------------------------------------------
    def _backend(self, kind: str):
        """Build a backend, falling back to NullOutput if the device isn't
        there. A missing DAC must not stop the cabinet booting -- but it must
        also not leave a half-open object around whose blank() silently does
        nothing, which is why the fallback is an explicit NullOutput."""
        from laser_output import NullOutput
        try:
            return make_backend(kind, self.cfg)
        except Exception as e:
            if kind != "none":
                print("[laser] could not open %s: %s" % (kind, e))
                print("[laser] output is NONE; the on-screen preview still works.")
                self.cfg.use_sim = True
            return NullOutput()

    def _set_ceiling(self, value: float) -> None:
        self.laser.set_max_brightness(max(0.0, min(1.0, value)))
        self.cfg.max_brightness = self.laser.max_brightness

    def _arm(self) -> None:
        if self.laser.arm():
            self.laser_msg = "ARMED at %.0f%%" % (self.laser.max_brightness * 100)
            print("[laser] ARMED  ceiling=%.0f%%" % (self.laser.max_brightness * 100))
        else:
            # arm() already printed why. Never show ARMED over a dark device.
            self.laser_msg = "ARM REFUSED BY DEVICE - STILL DISARMED"

    def _disarm(self) -> None:
        self.laser.disarm()
        self.confirm_arm = False
        self.laser_msg = "DISARMED"
        print("[laser] DISARMED")

    def _status_line(self):
        """(text, colour) for the simulator's operator badge, or None."""
        if self.laser.name == "none":
            return None
        if self.laser.armed:
            return ("ARMED  %d%%  %s" % (round(self.laser.max_brightness * 100),
                                         self.laser.name.upper()),
                    (255, 170, 0))
        return ("DISARMED  %s" % self.laser.name.upper(), (255, 60, 60))

    def _disarm_confirms(self) -> None:
        """Moving the config cursor cancels every pending two-press confirm, so
        one can never be completed by accident several rows later."""
        self.confirm_scores = False
        self.confirm_arm = False
        self.confirm_ceiling = False

    BRING_UP_CEILING = 0.05
    CEILING_STEP_PCT = 10

    def _adjust_ceiling(self, delta: int) -> None:
        """Left/Right by 10%, snapped onto the 10% grid.

        It steps to the next multiple of 10 rather than adding 10 to whatever is
        there, so the bring-up 5% doesn't leave you on 15/25/35 forever, and so
        100% is actually reachable. Down from 5% therefore lands on 0, which is
        the safe direction; RESET MAX BRIGHTNESS = 5 is how you get back to
        bring-up power.

        Confirming is about *crossing* bring-up power, not about being above it:
        the first press that would take you past 5% asks, and once you are past
        it the following presses just adjust. Re-asking on every increment made
        the row permanently show the prompt instead of the value, so you could
        not see what you were setting.
        """
        cur = self.laser.max_brightness
        step = self.CEILING_STEP_PCT
        pct = int(round(cur * 100))
        if delta > 0:
            pct = pct - (pct % step) + step
        else:
            pct = pct + ((-pct) % step) - step
        target = max(0, min(100, pct)) / 100.0
        crossing = (cur <= self.BRING_UP_CEILING + 1e-9
                    and target > self.BRING_UP_CEILING + 1e-9)
        if crossing and not self.confirm_ceiling:
            self.confirm_ceiling = True
            self.laser_msg = "PRESS AGAIN TO RAISE ABOVE 5%"
            self.menu_sfx.play("move")
            return
        self._set_ceiling(target)
        self.confirm_ceiling = False
        self.menu_sfx.play("move")

    # -- point rate / point budget -------------------------------------------
    # Shared by the CONFIG screen and the live tuner, so the two cannot drift
    # apart on clamps or step sizes. `ctx` is a game key, or None for the
    # defaults (the menu, the config screen, and any game with no override).

    def _adjust_pps(self, ctx, delta: int) -> None:
        cfg = self.cfg
        new = int(max(1000, min(60000, cfg.pps_for(ctx) + delta * 1000)))
        if ctx is None:
            cfg.pps = new
        else:
            cfg.game_pps[ctx] = new

    def _adjust_points(self, ctx, delta: int) -> None:
        # Capped at dac_max_points: the budget is a floor under the planner's
        # own pps/fps budget, and anything past the DAC's per-frame limit is
        # truncated downstream anyway.
        cfg = self.cfg
        new = int(max(100, min(cfg.dac_max_points,
                               cfg.points_for(ctx) + delta * 50)))
        if ctx is None:
            cfg.lit_budget = new
        else:
            cfg.game_points[ctx] = new

    def _tune_ctx(self):
        """(game key or None, display label) for whatever is on screen."""
        if self.mode == "game" and self.game is not None:
            return self.game.key, self.game.name
        return None, "DEFAULT"

    def _tune(self, key: int) -> None:
        """One keypress from the live tuner. Keyboard-only; see _reserved."""
        cfg = self.cfg
        ctx, _ = self._tune_ctx()
        if key == pygame.K_BACKSLASH:
            self._save_tuning()
            self.menu_sfx.play("select")
            return
        if key == pygame.K_BACKSPACE:
            # Back to defaults for whatever is on screen. At the menu that means
            # the shipped defaults, which is the case that matters most: it is
            # the only way out of a tuning session that went somewhere silly,
            # and leaving it inert here (as this first did) left an operator
            # with a slow cabinet and no way back short of editing JSON.
            if ctx is None:
                fresh = Settings()
                cfg.pps, cfg.lit_budget = fresh.pps, fresh.lit_budget
            else:
                cfg.game_pps.pop(ctx, None)
                cfg.game_points.pop(ctx, None)
            self.menu_sfx.play("select")
        elif key in (pygame.K_MINUS, pygame.K_EQUALS):
            self._adjust_pps(ctx, 1 if key == pygame.K_EQUALS else -1)
            self.menu_sfx.play("move")
        else:
            self._adjust_points(ctx, 1 if key == pygame.K_RIGHTBRACKET else -1)
            self.menu_sfx.play("move")
        self.tuner_dirty = True

    def _save_tuning(self) -> None:
        """Write the tuned values to disk. ONLY ever called from an explicit
        save -- the tuner's own '\\' key, or the config screen's SAVE.

        It deliberately does not save on close, on quit, or per keypress. A live
        tuner is something you explore with, and the first version of this wrote
        on every close: an experiment became the cabinet's permanent state, with
        the direction that feels like "better" (more points) being the one that
        costs refresh. Same reasoning as the config screen's SAVE & BACK.
        """
        if store.save_settings(self.cfg):
            self.tuner_dirty = False

    def _switch_device(self, kind: str) -> None:
        """Swap the live backend. Always disarms, and stays disarmed: arming is
        a statement about one specific projector and is never carried across a
        device change."""
        if kind == self.cfg.output_kind:
            return
        ok, msg = self.laser.swap_backend(lambda: make_backend(kind, self.cfg),
                                          kind=kind)
        self.cfg.output_kind = kind if ok else "none"
        self.laser_msg = msg.upper()
        self.menu_sfx.play("select")

    # -- lifecycle ----------------------------------------------------------
    def run(self, start_game: Optional[Type[Game]] = None) -> None:
        cfg = self.cfg
        pygame.init()
        joy_mgr = JoystickManager()
        joy_mgr.init()
        flags = pygame.FULLSCREEN | pygame.SCALED if cfg.fullscreen else 0
        self.surface = pygame.display.set_mode((cfg.sim_size, cfg.sim_size), flags)
        pygame.display.set_caption("Laser Arcade")
        clock = pygame.time.Clock()

        # The laser and the simulator are no longer interchangeable "outputs":
        # the laser goes through SafeOutput and the shared (N,6) frame format,
        # the simulator keeps consuming the raw planned point list.
        self.sim: Optional[Simulator] = Simulator(self.surface, cfg) if cfg.use_sim else None
        # Built even with --no-sim: it draws on the monitor, not into the scene,
        # so it is just as usable over a black window on a blind cabinet.
        self.tuner = Tuner()
        self.laser = SafeOutput(self._backend(cfg.output_kind),
                                max_brightness=cfg.max_brightness,
                                armed=False)   # ALWAYS. No override, ever.
        print("[laser] output=%s  DISARMED  ceiling=%.0f%%"
              % (self.laser.name, self.laser.max_brightness * 100))

        self.menu_sfx = SoundBank(
            sounds={"move": blip(440, 0.05), "select": blip(760, 0.12)},
            volume=cfg.volume, enabled=cfg.audio)
        self.highscores = store.load_highscores()
        self.game: Optional[Game] = None
        self.game_sfx: Optional[SoundBank] = None
        self.game_t = 0.0
        self.paused = False

        if start_game is not None:
            self._launch(start_game)

        text_col = cfg.beam(cfg.col_text)
        self._held = set()        # keyboard + pad, merged: what games see
        self._kbd_held = set()    # keyboard ONLY: what config/arm/quit see
        self._mouse_down = False
        self.quit_requested = False
        self._pad_cursor = None                       # world-space, or None
        self._last_mouse = pygame.mouse.get_pos()
        pygame.mouse.set_visible(False)      # games draw their own crosshair
        # As late as possible, so nothing installed afterwards displaces the
        # handlers. SIGTERM is the one that matters: without it `kill` skips
        # teardown entirely and the DAC keeps replaying its last frame.
        install_panic_handlers(self.laser)
        running = True
        try:
            while running:
                dt = min(clock.tick(cfg.target_fps) / 1000.0, 0.05)
                pressed = set()
                mouse_click = False
                for e in pygame.event.get():
                    if e.type == pygame.QUIT:
                        running = False
                    elif e.type == pygame.KEYDOWN:
                        # key capture (config rebind) grabs everything but Esc
                        if self.capture_action is not None:
                            if e.key != pygame.K_ESCAPE:
                                cfg.keymap.rebind(self.capture_action, e.key)
                            self.capture_action = None
                            continue
                        if not self._reserved(e.key, e.mod):
                            pressed.add(e.key)
                            self._held.add(e.key)
                            self._kbd_held.add(e.key)
                    elif e.type == pygame.KEYUP:
                        self._held.discard(e.key)
                        self._kbd_held.discard(e.key)
                    elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                        self._mouse_down = True
                        mouse_click = True
                    elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                        self._mouse_down = False
                    elif e.type == pygame.WINDOWFOCUSLOST:
                        self._held.clear()
                        self._kbd_held.clear()
                        self._mouse_down = False

                # Everything pressed so far came from a real key event. Snapshot
                # it before the pad merge below, so the config screen and the
                # arm gate can be given a provably keyboard-only view.
                kbd_pressed = set(pressed)

                # Pad input, merged in as if it were the keyboard. Polled after
                # the event pump (which is what refreshes joystick state), and
                # `held` is authoritative so the merge re-heals itself after a
                # focus-loss clear.
                joy_pressed, joy_released = joy_mgr.update()
                self._held.difference_update(joy_released)
                self._held.update(joy_mgr.held)
                # the pad synthesises keys rather than events, so reserved keys
                # have to be offered the same chance to consume an edge
                for k in joy_pressed:
                    if self._reserved(k, 0, from_pad=True):
                        self._held.discard(k)
                    else:
                        pressed.add(k)
                if self.quit_requested:
                    running = False
                # pad fire doubles as the mouse button, so Missile is playable
                # without a mouse. Read from the pad only -- binding the
                # keyboard's fire key to a click would change desktop play.
                fire_keys = set(cfg.keymap.bindings.get("fire", ()))
                pad_fire_down = bool(fire_keys & joy_mgr.held)
                pad_fire_click = bool(fire_keys & joy_pressed)

                mx, my = pygame.mouse.get_pos()
                if (mx, my) != self._last_mouse:
                    self._last_mouse = (mx, my)
                    self._pad_cursor = None      # real mouse takes back control
                mouse_pos = _screen_to_world(mx, my, cfg.sim_size, cfg.fill)
                # The left stick steers a virtual cursor for mouse-driven games.
                # It picks up from wherever the pointer already is, so handing
                # over from the mouse doesn't jump.
                ax, ay = joy_mgr.left_stick
                if ax or ay:
                    cx, cy = self._pad_cursor if self._pad_cursor else mouse_pos
                    step = PAD_CURSOR_SPEED * dt
                    self._pad_cursor = (max(-1.0, min(1.0, cx + ax * step)),
                                        max(-1.0, min(1.0, cy + ay * step)))
                if self._pad_cursor:
                    mouse_pos = self._pad_cursor

                inp = InputState(set(self._held), pressed, mouse_pos,
                                 self._mouse_down or pad_fire_down,
                                 mouse_click or pad_fire_click)
                # The keyboard-only view. Tracked as its own set rather than
                # subtracting the pad's keys from the merged one, so a key
                # genuinely held on both devices at once still counts here.
                kbd_inp = InputState(set(self._kbd_held), kbd_pressed,
                                     mouse_pos, self._mouse_down, mouse_click)

                if self.mode == "game":
                    scene = self._game_step(dt, inp, text_col)
                elif self.mode == "config":
                    scene = self._config_step(kbd_inp)   # keyboard only
                else:
                    scene = self._menu_step(inp, kbd_inp)
                # Resolved after the step, not inside it, so a menu Enter that
                # has just launched a game already plans with that game's
                # settings rather than one frame of the menu's.
                ctx, label = self._tune_ctx()

                # The planner reads pps/lit_budget off Settings, so the per-game
                # overrides are swapped in around the one call rather than
                # threaded through its signature. Restored immediately: the
                # config screen renders from the same Settings and must show
                # what is stored, not whatever the last frame happened to use.
                use_pps = cfg.pps_for(ctx)
                saved = (cfg.pps, cfg.lit_budget)
                cfg.pps, cfg.lit_budget = use_pps, cfg.points_for(ctx)
                stream, _ = pathplan.plan(scene, cfg)
                cfg.pps, cfg.lit_budget = saved

                self.surface.fill((0, 0, 0))
                if self.sim:
                    # Always the untouched, full-brightness stream. The ceiling
                    # and the arm gate are DAC-only: dimming the preview would
                    # make the limiter invisible instead of obvious.
                    self.sim.set_status(self._status_line())
                    self.sim.send(stream, use_pps)

                # The config screen is text-heavy and static -- optionally keep
                # its *content* off the laser so a low-pps output isn't left
                # grinding through it. Note we still write a frame either way:
                # a DAC that stops being fed replays its last frame forever, so
                # silence is the less safe state, not the safer one.
                if self.mode == "config" and not cfg.config_laser_output:
                    self.laser.write(to_frame(_blank_stream(cfg), cfg), use_pps)
                else:
                    self.laser.write(to_frame(stream, cfg), use_pps)

                # Last, so it sits over the preview -- and after the laser
                # write, to make it obvious it costs the beam nothing. It
                # reports len(stream): the frame the DAC actually got, dwells
                # and blanked travel included, not the budget that was asked for.
                if self.tuner_open and self.mode != "config":
                    self.tuner.draw(self.surface, cfg, ctx, label,
                                    len(stream), use_pps, self.tuner_dirty)
                pygame.display.flip()
        finally:
            # Note there is no _save_tuning() here on purpose: unsaved tuning
            # dies with the session. High scores are the player's and are kept;
            # an untested point budget is not something to inherit on next boot.
            self._flush_scores()      # quitting mid-game still keeps the score
            if self.game_sfx:
                self.game_sfx.close()
            self.menu_sfx.close()
            self.laser.close()        # blanks first; idempotent with atexit
            joy_mgr.close()
            pygame.mouse.set_visible(True)
            pygame.quit()

    def _reserved(self, key: int, mod: int = 0, from_pad: bool = False) -> bool:
        """Handle a shell-reserved key; return True if it was consumed (and so
        must not reach the menu or a game).

        Several things here are deliberately keyboard-only. On a cabinet the pad
        is the public-facing control, so it gets gameplay and nothing else:

        * **Quit** -- a player must not be able to drop the arcade to a desktop.
        * **The config screen** -- while it is up the pad is inert entirely, so
          nobody can nudge the brightness ceiling, the scanner calibration or
          the key bindings, and nobody can kick the operator out mid-edit.
        * **Arm and disarm** -- a wireless pad must not be able to light a Class
          4 laser. It cannot disarm either: that is a real trade-off (a pad
          holder has no software kill) but the Remote Stop is the actual
          interlock, and a nuisance-disarm mid-game is the likelier event.

        From the pad, Escape still backs out of a game -- it just stops short of
        quitting at the menu.
        """
        # While config is open the pad does nothing at all. Checked first so it
        # covers Escape, Q and the arm keys in one place rather than three.
        if from_pad and self.mode == "config":
            return True
        if key == pygame.K_TAB:
            # The live tuner. Not offered over the config screen, which already
            # has these rows and owns the arrow keys.
            if from_pad or self.mode == "config":
                return True
            # Closing does NOT write to disk -- '\' does. Values stay live for
            # the session either way, so nothing is lost by closing.
            self.tuner_open = not self.tuner_open
            return True
        if self.tuner_open and key in TUNER_KEYS:
            # Only reserved *while the tuner is open*, so these four keys are
            # ordinary rebindable keys the rest of the time -- the same bargain
            # the arrow keys already make with the config screen.
            if not from_pad:
                self._tune(key)
            return True
        if key == pygame.K_q:
            if not from_pad:
                self.quit_requested = True
            return True
        if key == pygame.K_PERIOD:
            # Always consumed, whatever the source -- the key is reserved by the
            # shell, and only the *action* is keyboard-gated. If it merely fell
            # through for a pad, a future button map that happened to emit
            # K_PERIOD would silently turn it into a gameplay key.
            if from_pad:
                return True
            if mod & pygame.KMOD_SHIFT:
                # Arming is a deliberate act, so it takes two presses. Disarming
                # never does -- see below.
                if self.confirm_arm:
                    self.confirm_arm = False
                    self._arm()
                else:
                    self.confirm_arm = True
                    self.laser_msg = "PRESS SHIFT-. AGAIN TO ARM"
                    print("[laser] press Shift-. again to ARM")
            else:
                # Instant, unconfirmed, no fade, from any mode. A fade on the
                # way down is a fade you are still emitting through.
                self._disarm()
            return True
        if key == pygame.K_ESCAPE:
            if self.mode == "game":
                self._exit_game()
            elif self.mode == "config":
                if self.diag_rows is not None:
                    self.diag_rows = None      # leave TEST DEVICE, stay in config
                else:
                    self._save_tuning()   # also clears the tuner's UNSAVED mark
                    self.mode = "menu"
            elif not from_pad:
                self.quit_requested = True
            return True
        if key == pygame.K_p and self.mode == "game":
            self.paused = not self.paused
            return True
        return False

    # -- menu (carousel) -----------------------------------------------------
    def _menu_step(self, inp: InputState, kbd_inp: InputState):
        """`inp` is keyboard+pad, `kbd_inp` is keyboard only.

        The carousel takes either, so a pad plays games normally. Focus can only
        move onto CONFIG from the keyboard, which is what keeps the whole config
        surface -- brightness ceiling included -- away from a wireless pad.
        Gating the *focus* rather than just the Enter press matters: otherwise a
        pad could highlight CONFIG and then find Enter silently does nothing,
        which reads as a broken cabinet rather than a locked one.
        """
        n = len(self.games)
        if kbd_inp.hit(pygame.K_UP, pygame.K_w, pygame.K_DOWN, pygame.K_s):
            self.menu_focus = "config" if self.menu_focus == "carousel" else "carousel"
            self.menu_sfx.play("move")
        if self.menu_focus == "carousel":
            if inp.hit(pygame.K_LEFT, pygame.K_a):
                self.sel = (self.sel - 1) % n
                self.menu_sfx.play("move")
            if inp.hit(pygame.K_RIGHT, pygame.K_d):
                self.sel = (self.sel + 1) % n
                self.menu_sfx.play("move")
        if self.menu_focus == "config":
            if kbd_inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.menu_sfx.play("select")
                # The config screen lists the same PPS/POINTS values, so leaving
                # the overlay up would give you two live views of one setting.
                # Unsaved tuning stays live and is shown there; SAVE & BACK is
                # what commits it, same as any other row.
                self.tuner_open = False
                self.mode = "config"
                self.cfg_sel = 0
                self.diag_rows = None
                self.confirm_arm = False
                self.confirm_ceiling = False
                self.laser_msg = ""      # don't show a stale note from last time
                return []
        elif inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.menu_sfx.play("select")
            self._launch(self.games[self.sel])
            return []
        return self._menu_scene()

    def _menu_scene(self):
        cfg = self.cfg
        ct = cfg.beam(cfg.col_text)
        csel = cfg.beam(cfg.col_saucer)
        scene = []
        for pl in font.text_polylines("LASER ARCADE", 0.0, 0.72, 0.13, center=True):
            scene.append((pl, ct))

        g = self.games[self.sel]
        icon_col = csel if self.menu_focus == "carousel" else ct

        # icon, scaled into a small box above the name
        icon_cy, icon_r = 0.24, 0.24
        for stroke in getattr(g, "icon", []):
            scene.append(([(x * icon_r, icon_cy + y * icon_r) for (x, y) in stroke], icon_col))

        label = g.name + ("  2P" if getattr(g, "players", 1) >= 2 else "")
        name_y = -0.14
        for pl in font.text_polylines(label, 0.0, name_y, 0.11, center=True):
            scene.append((pl, icon_col))

        # left/right carousel arrows, either side of the icon/name column
        ax = 0.62
        ay = icon_cy
        scene.append(([(-ax, ay), (-ax - 0.09, ay + 0.09), (-ax - 0.09, ay - 0.09), (-ax, ay)], csel))
        scene.append(([(ax, ay), (ax + 0.09, ay + 0.09), (ax + 0.09, ay - 0.09), (ax, ay)], csel))

        # CONFIG button, underneath
        conf_y = -0.55
        conf_col = csel if self.menu_focus == "config" else ct
        for pl in font.text_polylines("CONFIG", 0.0, conf_y, 0.09, center=True):
            scene.append((pl, conf_col))
        if self.menu_focus == "config":
            w = font.text_width("CONFIG") * (0.09 / 6.0) / 2.0 + 0.06
            scene.append(([(-w, conf_y + 0.09), (-w - 0.04, conf_y + 0.09),
                           (-w - 0.04, conf_y - 0.02), (-w, conf_y - 0.02)], csel))
            scene.append(([(w, conf_y + 0.09), (w + 0.04, conf_y + 0.09),
                           (w + 0.04, conf_y - 0.02), (w, conf_y - 0.02)], csel))
        return scene

    # -- config -------------------------------------------------------------
    # Reachable from the keyboard only -- see _menu_step and _reserved. That is
    # what lets the brightness ceiling live here safely.
    def _config_rows(self):
        rows = []
        rows.append(("laser_arm", None, "LASER"))
        rows.append(("ceiling", None, "MAX BRIGHTNESS"))
        rows.append(("ceiling_reset", None, "RESET MAX BRIGHTNESS = 5"))
        rows.append(("device", None, "LASER DEVICE"))
        rows.append(("diag", None, "TEST DEVICE"))
        rows.append(("output", None, "CONFIG OUTPUT"))
        # key=None is the global default: the menu, the config screen, and any
        # game with no override of its own. PPS and POINTS are interleaved
        # rather than listed in two blocks because they are one setting in
        # practice -- the refresh rate the audience sees is points/pps, so you
        # always end up adjusting them against each other.
        rows.append(("pps", None, "PPS DEFAULT"))
        rows.append(("points", None, "POINTS DEFAULT"))
        for g in self.games:
            rows.append(("pps", g.key, "PPS " + g.name))
            rows.append(("points", g.key, "POINTS " + g.name))
        rows.append(("scale", None, "OUTPUT SCALE"))
        rows.append(("flip", "invert_x", "FLIP X"))
        rows.append(("flip", "invert_y", "FLIP Y"))
        rows.append(("keystone", "keystone_h", "KEYSTONE H"))
        rows.append(("keystone", "keystone_v", "KEYSTONE V"))
        for action, label in ACTIONS:
            rows.append(("key", action, label))
        rows.append(("reset", None, "RESET KEYS"))
        rows.append(("reset_scores", None, "RESET HIGHSCORES"))
        rows.append(("save", None, "SAVE & BACK"))
        return rows

    def _config_step(self, inp: InputState):
        """`inp` here is ALWAYS the keyboard-only InputState (see run()).

        Nothing on this screen may be driven by a gamepad. Enforcing it at the
        single call site rather than per-row means a row added later cannot
        accidentally become pad-reachable.
        """
        cfg = self.cfg
        if self.diag_rows is not None:
            return self._diag_scene()          # Esc (in _reserved) backs out

        rows = self._config_rows()
        n = len(rows)
        if inp.hit(pygame.K_UP):
            self.cfg_sel = (self.cfg_sel - 1) % n
            self._disarm_confirms()           # moving away cancels any confirm
            self.menu_sfx.play("move")
        if inp.hit(pygame.K_DOWN):
            self.cfg_sel = (self.cfg_sel + 1) % n
            self._disarm_confirms()
            self.menu_sfx.play("move")

        kind, key, _ = rows[self.cfg_sel]
        delta = (1 if inp.hit(pygame.K_RIGHT) else 0) - (1 if inp.hit(pygame.K_LEFT) else 0)
        if delta and kind == "pps":
            self._adjust_pps(key, delta)
            self.menu_sfx.play("move")
        elif delta and kind == "points":
            self._adjust_points(key, delta)
            self.menu_sfx.play("move")
        elif delta and kind == "scale":
            # 5% steps. Floored at 10%: below that the whole scan is squeezed
            # into a spot, which is hotter rather than safer (see Settings).
            cfg.output_scale = round(max(0.10, min(1.0,
                                     cfg.output_scale + delta * 0.05)), 2)
            self.menu_sfx.play("move")
        elif delta and kind == "flip":
            setattr(cfg, key, not getattr(cfg, key))
            self.menu_sfx.play("move")
        elif delta and kind == "keystone":
            cur = getattr(cfg, key)
            setattr(cfg, key, round(max(-0.50, min(0.50, cur + delta * 0.02)), 3))
            self.menu_sfx.play("move")
        elif delta and kind == "output":
            cfg.config_laser_output = not cfg.config_laser_output
            self.menu_sfx.play("move")
        elif delta and kind == "ceiling":
            self._adjust_ceiling(delta)
        elif delta and kind == "device":
            kinds = ("none", "helios", "lasercube")
            i = kinds.index(cfg.output_kind) if cfg.output_kind in kinds else 0
            self._switch_device(kinds[(i + delta) % len(kinds)])

        if inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            if kind == "key":
                self.capture_action = key
            elif kind == "output":
                cfg.config_laser_output = not cfg.config_laser_output
                self.menu_sfx.play("select")
            elif kind == "flip":
                setattr(cfg, key, not getattr(cfg, key))
                self.menu_sfx.play("select")
            elif kind == "laser_arm":
                if self.laser.armed:
                    self._disarm()             # never needs confirming
                    self.menu_sfx.play("select")
                elif self.confirm_arm:
                    self.confirm_arm = False
                    self._arm()
                    self.menu_sfx.play("select")
                else:
                    self.confirm_arm = True
                    self.menu_sfx.play("move")
            elif kind == "ceiling_reset":
                self._set_ceiling(0.05)
                self.confirm_ceiling = False
                self.laser_msg = "CEILING BACK TO 5%"
                self.menu_sfx.play("select")
            elif kind == "diag":
                # Emits nothing, so it is safe to press at any time, armed
                # or not.
                self.diag_rows = self.laser.diagnostics()
                self.menu_sfx.play("select")
            elif kind == "reset":
                cfg.keymap.reset()
                self.menu_sfx.play("select")
            elif kind == "reset_scores":
                # destructive and unrecoverable, so make it deliberate
                if self.confirm_scores:
                    self.highscores.clear()
                    store.save_highscores(self.highscores)
                    self._scores_dirty = False
                    self.confirm_scores = False
                    self.menu_sfx.play("select")
                else:
                    self.confirm_scores = True
                    self.menu_sfx.play("move")
            elif kind == "save":
                self._save_tuning()   # also clears the tuner's UNSAVED mark
                self.menu_sfx.play("select")
                self.mode = "menu"
                return []
        return self._config_scene(rows)

    def _config_scene(self, rows):
        cfg = self.cfg
        ct = cfg.beam(cfg.col_text)
        csel = cfg.beam(cfg.col_saucer)
        scene = []
        for pl in font.text_polylines("CONFIG", 0.0, 0.82, 0.10, center=True):
            scene.append((pl, ct))

        vis = 6
        n = len(rows)
        scroll = max(0, min(self.cfg_sel - vis // 2, max(0, n - vis)))
        y = 0.56
        for r in range(scroll, min(scroll + vis, n)):
            kind, key, label = rows[r]
            if kind == "pps":
                text = "%s  %d" % (label, cfg.pps_for(key))
            elif kind == "points":
                # The point count on its own means nothing standing at a
                # cabinet; the refresh rate it buys is the number you are
                # actually chasing, so show that next to it.
                pts = cfg.points_for(key)
                text = "%s  %d  %dFPS" % (label, pts, cfg.pps_for(key) / pts)
            elif kind == "scale":
                text = "%s  %d%%" % (label, round(cfg.output_scale * 100))
            elif kind == "flip":
                text = "%s  %s" % (label, "ON" if getattr(cfg, key) else "OFF")
            elif kind == "keystone":
                text = "%s  %+.2f" % (label, getattr(cfg, key))
            elif kind == "output":
                text = "%s  %s" % (label, "BOTH" if cfg.config_laser_output else "SCREEN ONLY")
            elif kind == "key":
                if self.capture_action == key:
                    text = "%s  <PRESS KEY>" % label
                else:
                    text = "%s  %s" % (label, cfg.keymap.label(key))
            elif kind == "reset_scores":
                text = "CLEAR ALL - CONFIRM?" if self.confirm_scores else label
            elif kind == "laser_arm":
                if self.confirm_arm:
                    text = "%s  ARM - CONFIRM?" % label
                else:
                    text = "%s  %s" % (label,
                                       "ARMED" if self.laser.armed else "DISARMED")
            elif kind == "ceiling":
                # The value is always shown. It used to be replaced by the
                # confirm prompt, which meant you could not read what you were
                # setting while you set it.
                text = "%s  %d%%" % (label, round(self.laser.max_brightness * 100))
                if self.confirm_ceiling:
                    text += "  RAISE ABOVE 5%? - PRESS AGAIN"
            elif kind == "device":
                text = "%s  %s" % (label, cfg.output_kind.upper())
            else:
                text = label
            colour = csel if r == self.cfg_sel else ct
            # ARMED is the one state worth spotting from across the room, so it
            # keeps the warning colour whether or not the row is selected. The
            # max-brightness row reads like every other row; its value, and the
            # badge on the preview window, carry that signal instead.
            if kind == "laser_arm" and self.laser.armed:
                colour = cfg.beam(cfg.col_saucer)
            prefix = "> " if r == self.cfg_sel else "  "
            for pl in font.text_polylines(prefix + text, -0.86, y, 0.046):
                scene.append((pl, colour))
            y -= 0.19
        # scroll hint arrows
        if scroll > 0:
            scene.append(([(0.9, 0.66), (0.87, 0.60), (0.93, 0.60), (0.9, 0.66)], ct))
        if scroll + vis < n:
            scene.append(([(0.9, -0.66), (0.87, -0.60), (0.93, -0.60), (0.9, -0.66)], ct))
        if self.laser_msg:
            for pl in font.text_polylines(self.laser_msg[:44], 0.0, -0.84, 0.040,
                                          center=True):
                scene.append((pl, cfg.beam(cfg.col_debris)))
        for pl in font.text_polylines("ARROWS MOVE/ADJUST  ENTER SET  ESC SAVE"
                                      "  -  KEYBOARD ONLY",
                                      0.0, -0.92, 0.030, center=True):
            scene.append((pl, ct))
        return scene

    def _diag_scene(self):
        """TEST DEVICE: what the attached device says about itself.

        Reading a device's interlock state is not the same as having an
        interlock -- the hardware loop is the interlock; this is a readout of
        it. Emits nothing, so it is safe to open at any time.
        """
        cfg = self.cfg
        ct = cfg.beam(cfg.col_text)
        by_severity = {"warn": cfg.beam(cfg.col_debris),
                       "bad": cfg.beam(cfg.col_saucer)}
        scene = []
        for pl in font.text_polylines("TEST DEVICE", 0.0, 0.86, 0.080, center=True):
            scene.append((pl, ct))
        y = 0.66
        for row in (self.diag_rows or [])[:16]:
            label, value = str(row[0]), str(row[1])
            severity = row[2] if len(row) > 2 else ""
            text = ("%s  %s" % (label, value)).upper()[:52]
            for pl in font.text_polylines(text, -0.94, y, 0.036):
                scene.append((pl, by_severity.get(severity, ct)))
            y -= 0.095
        for pl in font.text_polylines("ESC BACK", 0.0, -0.92, 0.036, center=True):
            scene.append((pl, ct))
        return scene

    # -- game ---------------------------------------------------------------
    def _launch(self, game_cls: Type[Game]) -> None:
        self.game = game_cls(self.cfg)
        self.game.start()
        self.game.set_high_score(self.highscores.get(self.game.key, 0))
        self.game_t = 0.0
        self.paused = False
        self.mode = "game"
        sounds, loops = self.game.sound_spec()
        self.game_sfx = SoundBank(sounds=sounds, loops=loops,
                                  volume=self.cfg.volume, enabled=self.cfg.audio)

    def _record_score(self) -> None:
        """Fold the running game's score into the table. Called every frame --
        writing to disk is left to _flush_scores, on the way out of a game."""
        if self.game is None:
            return
        s = self.game.score()
        if s is None:
            return
        if int(s) > self.highscores.get(self.game.key, 0):
            self.highscores[self.game.key] = int(s)
            self._scores_dirty = True

    def _flush_scores(self) -> None:
        if self._scores_dirty and store.save_highscores(self.highscores):
            self._scores_dirty = False

    def _exit_game(self) -> None:
        self._flush_scores()
        if self.game_sfx:
            self.game_sfx.close()
            self.game_sfx = None
        self.game = None
        self.paused = False
        self.mode = "menu"

    def _game_step(self, dt: float, inp: InputState, text_col):
        if not self.paused:
            self.game.update(dt, inp)
            self._record_score()
            self.game_t += dt
            for ev in self.game.audio_events():
                self.game_sfx.play(ev)
            self.game_sfx.apply_loops(self.game.active_loops())
        else:
            self.game_sfx.apply_loops(set())

        scene = self.game.scene(self.game_t)
        if self.paused:
            for pl in font.text_polylines("PAUSED", 0.0, 0.0, 0.16, center=True):
                scene.append((pl, text_col))
        return scene
