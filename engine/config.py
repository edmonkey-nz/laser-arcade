"""Central configuration for Laser Asteroids.

Everything you'll want to tune for your particular galvos / scanner lives here.
The two groups that matter most on real hardware are SCANNER TUNING (point
density, dwell, blanking) and OUTPUT (pps, frame rate). See README for a guide.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .keymap import KeyMap


# ---------------------------------------------------------------------------
# Colour is expressed as an (r, g, b) triple 0..255. On a single-colour laser
# only the intensity matters, so any non-black colour lights the beam.
# ---------------------------------------------------------------------------
Colour = tuple


@dataclass
class Settings:
    # ---- Output selection -------------------------------------------------
    output_kind: str = "none"        # none | helios | lasercube
    use_sim: bool = True             # draw an on-screen preview of the laser
    fullscreen: bool = False

    # ---- Laser safety -----------------------------------------------------
    # The brightness ceiling: a hard cap applied at the very last step before
    # the device, after every scene, colour and geometry transform. Nothing
    # upstream can exceed it -- not `brightness` below, not a game's palette.
    #
    # It is a CREATIVE LIMITER, NOT A SAFETY INTERLOCK. It cannot help against
    # a crash, a driver bug or a stuck buffer, and 5% of a 7.5 W beam held
    # stationary still burns. The key switch, shutter, interlock loop and
    # Remote Stop are the actual safety layer. See SAFETY.md.
    #
    # Deliberately NOT persisted to disk -- see engine/store.py. Every launch
    # starts at bring-up power.
    max_brightness: float = 0.05
    # Output always starts DISARMED. There is no setting to change that, and
    # adding one would defeat the point of the gate.

    # ---- Laser output (LaserCube network backend) ------------------------
    lasercube_ip: str = ""           # "" -> discover by UDP broadcast
    lasercube_dry_run: bool = False  # pack and rate-control, transmit nothing
    lasercube_point_order: str = "xyrgb"   # "rgbxy" if the image comes out wrong

    # ---- Laser output (Helios) -------------------------------------------
    pps: int = 14000                 # points per second sent to the DAC. This is
                                     # the DEFAULT rate: it drives the menu and
                                     # the config screen, and is the fallback for
                                     # any game without its own `game_pps` entry.
                                     # Editable on the config screen and saved.
    dac_device: int = 0              # which Helios (if you have several)
    # Candidate shared-library names, tried in order. Newer SDK builds ship
    # libHeliosLaserDAC.so; older ones libHeliosDacAPI.so. Windows and macOS
    # names are listed too, for the packaged builds -- names that don't exist
    # on the running platform are skipped quietly.
    helios_libs: tuple = (
        "libHeliosDacAPI.so",
        "libHeliosLaserDAC.so",
        "./libHeliosDacAPI.so",
        "./libHeliosLaserDAC.so",
        "HeliosLaserDAC.dll",
        "HeliosDacAPI.dll",
        "libHeliosDacAPI.dylib",
        "libHeliosLaserDAC.dylib",
    )
    dac_max_points: int = 4096       # hard limit of a single Helios frame

    # ---- Frame timing -----------------------------------------------------
    target_fps: int = 40             # game logic + frame build rate

    # ---- Scanner geometry -------------------------------------------------
    # World space is [-1, 1] on both axes. It is mapped into the DAC's
    # 0..4095 square. `fill` leaves a small border so you don't slam the rails.
    dac_range: int = 4095
    fill: float = 0.92
    invert_x: bool = False           # FLIP X on the config screen
    invert_y: bool = False           # flip if your projector shows Y upside-down
    swap_xy: bool = False
    # Overall output size, 0.10..1.00 in 5% steps, applied in the mapper so it
    # shrinks *everything* -- every game, the menu and the config screen -- on
    # both the laser and the preview. It is framing, not distortion correction,
    # which is why (unlike keystone) the preview does show it.
    #
    # Shrinking concentrates the same beam power into a smaller area: the galvos
    # travel less for the same point rate, so dwell per unit area goes up and the
    # image gets hotter, not cooler. Hence the 10% floor, and hence turning this
    # down is not a substitute for turning `max_brightness` down.
    output_scale: float = 1.0

    # ---- Scanner tuning (the knobs that fight flicker & tails) -----------
    # All distances are in DAC units (0..4095).
    max_step: int = 45               # max gap between lit points on a line.
                                     #   smaller  -> brighter/straighter, more points
                                     #   larger   -> fewer points, faster, dimmer
    blank_step: int = 220            # step size while slewing with the beam OFF
    corner_dwell: int = 1            # extra repeated points at each corner
    start_dwell: int = 2             # lit points held at the start of a shape
    end_dwell: int = 2               # lit points held at the end of a shape
    blank_dwell: int = 3             # blanked points held at a jump destination
    # Adaptive density: if a frame would exceed this many LIT points, max_step
    # is grown automatically so the frame rate stays stable.
    lit_budget: int = 600

    # ---- Beam colours (ignored on single-colour lasers) ------------------
    col_ship: Colour = (0, 255, 255)
    col_asteroid: Colour = (0, 255, 255)
    col_bullet: Colour = (255, 255, 255)
    col_saucer: Colour = (255, 80, 80)
    col_text: Colour = (0, 255, 255)
    col_debris: Colour = (255, 180, 0)
    monochrome: bool = False         # force everything to col_ship
    brightness: float = 1.0          # global 0..1 multiplier

    # ---- Simulator window -------------------------------------------------
    sim_size: int = 900              # window is square, side in pixels
    sim_show_blanking: bool = False  # draw the beam-off travel lines faintly
    sim_show_points: bool = False    # dot every emitted sample (debug)
    sim_glow: bool = True

    # ---- Audio ------------------------------------------------------------
    audio: bool = True
    volume: float = 0.7

    # ---- Player config (edited on the CONFIG screen, saved to disk) -------
    game_pps: dict = field(default_factory=dict)   # game key -> pps override
    game_points: dict = field(default_factory=dict)  # game key -> lit_budget override
    keymap: KeyMap = field(default_factory=KeyMap)  # remappable gameplay keys
    # Keystone (trapezoid) correction, applied to the DAC output ONLY (never the
    # on-screen preview). keystone_h pre-widens/narrows top vs bottom (corrects
    # a projector tilted up/down); keystone_v pre-widens/narrows left vs right
    # (corrects a projector offset sideways).
    keystone_h: float = 0.0
    keystone_v: float = 0.0
    # Whether the CONFIG screen itself is sent to the laser, or kept to the
    # on-screen preview only. The config screen is text-heavy and static, which
    # is fine on a monitor but can be a lot for a low-pps laser to sit through.
    # Defaults to screen-only: config is an operator activity at the keyboard,
    # and there is no reason to paint a wall of text with the beam to do it.
    config_laser_output: bool = False

    def pps_for(self, game_key: str) -> int:
        """Configured PPS for a game, falling back to the global default.
        `game_key` None means the menu/config screen, i.e. the default."""
        return int(self.game_pps.get(game_key, self.pps))

    def points_for(self, game_key: str) -> int:
        """Configured point budget for a game, falling back to lit_budget.

        Together with pps_for() this sets the refresh rate the audience sees:
        the DAC plays a frame in points/pps seconds, so tuning these two per
        game is how a heavy scene is stopped from strobing.
        """
        return int(self.game_points.get(game_key, self.lit_budget))

    def beam(self, colour: Colour) -> Colour:
        """Apply monochrome/brightness policy to a requested colour."""
        if self.monochrome:
            colour = self.col_ship
        b = self.brightness
        return (int(colour[0] * b), int(colour[1] * b), int(colour[2] * b))
