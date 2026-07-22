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
In the menu: Left/Right cycle the game carousel, Up/Down move focus between
the carousel and the CONFIG button, Enter launches / opens whatever's focused.
In config: Up/Down move, Left/Right adjust a value, Enter binds a key / resets
/ saves / toggles.
"""
from __future__ import annotations

from typing import List, Optional, Type

import pygame

from . import font
from . import pathplan
from . import store
from .audio import SoundBank, blip
from .config import Settings
from .game import Game, InputState
from .keymap import ACTIONS
from .outputs import HeliosOutput, Simulator


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
        self.mode = "menu"          # menu | game | config

    # -- lifecycle ----------------------------------------------------------
    def run(self, start_game: Optional[Type[Game]] = None) -> None:
        cfg = self.cfg
        pygame.init()
        flags = pygame.FULLSCREEN | pygame.SCALED if cfg.fullscreen else 0
        self.surface = pygame.display.set_mode((cfg.sim_size, cfg.sim_size), flags)
        pygame.display.set_caption("Laser Arcade")
        clock = pygame.time.Clock()

        self.outputs = []
        self.helios: Optional[HeliosOutput] = None
        self.sim: Optional[Simulator] = None
        if cfg.use_laser:
            helios = HeliosOutput(cfg.helios_libs, cfg.dac_device,
                                  cfg.dac_max_points, settings=cfg)
            if helios.start():
                self.outputs.append(helios)
                self.helios = helios
            else:
                print("[laser] falling back to on-screen simulator only.")
                cfg.use_sim = True
        if cfg.use_sim:
            self.sim = Simulator(self.surface, cfg)
            self.outputs.append(self.sim)

        self.menu_sfx = SoundBank(
            sounds={"move": blip(440, 0.05), "select": blip(760, 0.12)},
            volume=cfg.volume, enabled=cfg.audio)
        self.game: Optional[Game] = None
        self.game_sfx: Optional[SoundBank] = None
        self.game_t = 0.0
        self.paused = False

        if start_game is not None:
            self._launch(start_game)

        text_col = cfg.beam(cfg.col_text)
        self._held = set()
        self._mouse_down = False
        pygame.mouse.set_visible(False)      # games draw their own crosshair
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
                        if e.key == pygame.K_q:
                            running = False
                        elif e.key == pygame.K_ESCAPE:
                            if self.mode == "game":
                                self._exit_game()
                            elif self.mode == "config":
                                store.save_settings(cfg)
                                self.mode = "menu"
                            else:
                                running = False
                        elif e.key == pygame.K_p and self.mode == "game":
                            self.paused = not self.paused
                        else:
                            pressed.add(e.key)
                            self._held.add(e.key)
                    elif e.type == pygame.KEYUP:
                        self._held.discard(e.key)
                    elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                        self._mouse_down = True
                        mouse_click = True
                    elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                        self._mouse_down = False
                    elif e.type == pygame.WINDOWFOCUSLOST:
                        self._held.clear()
                        self._mouse_down = False
                mx, my = pygame.mouse.get_pos()
                mouse_pos = _screen_to_world(mx, my, cfg.sim_size, cfg.fill)
                inp = InputState(set(self._held), pressed, mouse_pos,
                                 self._mouse_down, mouse_click)

                if self.mode == "game":
                    scene = self._game_step(dt, inp, text_col)
                    use_pps = cfg.pps_for(self.game.key)
                elif self.mode == "config":
                    scene = self._config_step(inp)
                    use_pps = cfg.pps
                else:
                    scene = self._menu_step(inp)
                    use_pps = cfg.pps

                save_pps = cfg.pps
                cfg.pps = use_pps
                stream, _ = pathplan.plan(scene, cfg)
                cfg.pps = save_pps
                self.surface.fill((0, 0, 0))
                # the config screen is text-heavy and static -- optionally
                # keep it off the laser and only show it on screen, so it
                # doesn't sit there overloading a low-pps output
                if self.mode == "config" and not cfg.config_laser_output:
                    targets = [o for o in self.outputs if o is self.sim]
                else:
                    targets = self.outputs
                for o in targets:
                    o.send(stream, use_pps)
                pygame.display.flip()
        finally:
            if self.game_sfx:
                self.game_sfx.close()
            self.menu_sfx.close()
            for o in self.outputs:
                o.close()
            pygame.mouse.set_visible(True)
            pygame.quit()

    # -- menu (carousel) -----------------------------------------------------
    def _menu_step(self, inp: InputState):
        n = len(self.games)
        if inp.hit(pygame.K_UP, pygame.K_w, pygame.K_DOWN, pygame.K_s):
            self.menu_focus = "config" if self.menu_focus == "carousel" else "carousel"
            self.menu_sfx.play("move")
        if self.menu_focus == "carousel":
            if inp.hit(pygame.K_LEFT, pygame.K_a):
                self.sel = (self.sel - 1) % n
                self.menu_sfx.play("move")
            if inp.hit(pygame.K_RIGHT, pygame.K_d):
                self.sel = (self.sel + 1) % n
                self.menu_sfx.play("move")
        if inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.menu_sfx.play("select")
            if self.menu_focus == "config":
                self.mode = "config"
                self.cfg_sel = 0
            else:
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
    def _config_rows(self):
        rows = []
        rows.append(("output", None, "CONFIG OUTPUT"))
        for g in self.games:
            rows.append(("pps", g.key, "PPS " + g.name))
        rows.append(("keystone", "keystone_h", "KEYSTONE H"))
        rows.append(("keystone", "keystone_v", "KEYSTONE V"))
        for action, label in ACTIONS:
            rows.append(("key", action, label))
        rows.append(("reset", None, "RESET KEYS"))
        rows.append(("save", None, "SAVE & BACK"))
        return rows

    def _config_step(self, inp: InputState):
        cfg = self.cfg
        rows = self._config_rows()
        n = len(rows)
        if inp.hit(pygame.K_UP):
            self.cfg_sel = (self.cfg_sel - 1) % n
            self.menu_sfx.play("move")
        if inp.hit(pygame.K_DOWN):
            self.cfg_sel = (self.cfg_sel + 1) % n
            self.menu_sfx.play("move")

        kind, key, _ = rows[self.cfg_sel]
        delta = (1 if inp.hit(pygame.K_RIGHT) else 0) - (1 if inp.hit(pygame.K_LEFT) else 0)
        if delta and kind == "pps":
            cur = cfg.pps_for(key)
            cfg.game_pps[key] = int(max(1000, min(60000, cur + delta * 1000)))
            self.menu_sfx.play("move")
        elif delta and kind == "keystone":
            cur = getattr(cfg, key)
            setattr(cfg, key, round(max(-0.50, min(0.50, cur + delta * 0.02)), 3))
            self.menu_sfx.play("move")
        elif delta and kind == "output":
            cfg.config_laser_output = not cfg.config_laser_output
            self.menu_sfx.play("move")

        if inp.hit(pygame.K_RETURN, pygame.K_KP_ENTER):
            if kind == "key":
                self.capture_action = key
            elif kind == "output":
                cfg.config_laser_output = not cfg.config_laser_output
                self.menu_sfx.play("select")
            elif kind == "reset":
                cfg.keymap.reset()
                self.menu_sfx.play("select")
            elif kind == "save":
                store.save_settings(cfg)
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
            elif kind == "keystone":
                text = "%s  %+.2f" % (label, getattr(cfg, key))
            elif kind == "output":
                text = "%s  %s" % (label, "BOTH" if cfg.config_laser_output else "SCREEN ONLY")
            elif kind == "key":
                if self.capture_action == key:
                    text = "%s  <PRESS KEY>" % label
                else:
                    text = "%s  %s" % (label, cfg.keymap.label(key))
            else:
                text = label
            colour = csel if r == self.cfg_sel else ct
            prefix = "> " if r == self.cfg_sel else "  "
            for pl in font.text_polylines(prefix + text, -0.86, y, 0.046):
                scene.append((pl, colour))
            y -= 0.19
        # scroll hint arrows
        if scroll > 0:
            scene.append(([(0.9, 0.66), (0.87, 0.60), (0.93, 0.60), (0.9, 0.66)], ct))
        if scroll + vis < n:
            scene.append(([(0.9, -0.66), (0.87, -0.60), (0.93, -0.60), (0.9, -0.66)], ct))
        for pl in font.text_polylines("ARROWS MOVE/ADJUST  ENTER SET  ESC SAVE",
                                      0.0, -0.92, 0.036, center=True):
            scene.append((pl, ct))
        return scene

    # -- game ---------------------------------------------------------------
    def _launch(self, game_cls: Type[Game]) -> None:
        self.game = game_cls(self.cfg)
        self.game.start()
        self.game_t = 0.0
        self.paused = False
        self.mode = "game"
        sounds, loops = self.game.sound_spec()
        self.game_sfx = SoundBank(sounds=sounds, loops=loops,
                                  volume=self.cfg.volume, enabled=self.cfg.audio)

    def _exit_game(self) -> None:
        if self.game_sfx:
            self.game_sfx.close()
            self.game_sfx = None
        self.game = None
        self.paused = False
        self.mode = "menu"

    def _game_step(self, dt: float, inp: InputState, text_col):
        if not self.paused:
            self.game.update(dt, inp)
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
