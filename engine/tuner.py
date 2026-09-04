"""The live tuner: an operator overlay for point rate and point budget.

Flipping between CONFIG and a running game to chase flicker doesn't work -- the
thing you are judging only exists while a game is actually drawing a busy frame,
and by the time you have walked back to the config screen it is gone. This puts
PPS and POINTS for *whatever is on screen right now* under four keys, and shows
the number you are really chasing: the measured frame size and the refresh rate
it produces.

Three decisions worth keeping:

* **It is drawn on the monitor with the bitmap font and never enters the scene.**
  The simulator's arm badge is the engine's only other bitmap-font element and
  this is the same category -- an operator readout. Drawing it with the stroke
  font would put it in the beam, which would add points to the frame and so move
  the very number being tuned. It would also, on a low-pps output, cost more
  refresh than the tuning could win back.

* **It reports the measured frame, not the budget.** `lit_budget` is a floor
  under the planner's interpolation budget; dwells and blanked travel land on
  top of it, so a 600-point budget routinely emits 800-1400 points. Showing the
  setting alone would be showing a number that is not what the DAC received.

* **Keyboard only**, like the rest of the operator surface. On a cabinet the pad
  is in a stranger's hands -- see `Shell._reserved`.

It is an overlay, not a mode: `Shell.mode` stays menu | game | config, and the
game underneath keeps running so you can watch the change land. Press P to pause
first if you want a still frame to read the point count off.
"""
from __future__ import annotations

import pygame

# Below this the beam visibly strobes. Laser flicker fusion sits around 25-30Hz
# for a full-field image -- lower than film because the beam is redrawing rather
# than holding -- so the readout warns before it reaches "obviously broken".
FLICKER_FPS = 24.0

TITLE = (0, 255, 255)
VALUE = (235, 235, 235)
WARN = (255, 170, 0)
BAD = (255, 60, 60)


class Tuner:
    """Owns the font and the drawing. The values live in Settings, and the
    shell owns the open/closed flag -- this class is deliberately stateless so
    there is only ever one copy of what PPS currently is."""

    def __init__(self) -> None:
        try:
            self._font = pygame.font.SysFont(None, 22, bold=True)
        except Exception:
            # Same guard as the simulator's badge: a machine with no usable
            # system font must still boot and play, just without the readout.
            self._font = None

    def draw(self, surface, cfg, ctx, label: str, frame_points: int,
             pps: int, dirty: bool = False) -> None:
        if self._font is None:
            return
        fps = (pps / frame_points) if frame_points else 0.0
        budget = cfg.points_for(ctx)
        overridden = ctx is not None and (ctx in cfg.game_pps
                                          or ctx in cfg.game_points)

        # Laid out as measured columns rather than space-padded strings: the
        # system font is proportional, so "%-7d" lines nothing up.
        head = "TUNE  %s%s%s" % (label, "  *" if overridden else "",
                                 "   UNSAVED" if dirty else "")
        foot = "TAB CLOSE   \\ SAVE   BKSP DEFAULTS"
        cols = [
            ("PPS", "%d" % pps, "- / =", VALUE),
            ("POINTS", "%d" % budget, "[ / ]", VALUE),
            ("FRAME", "%d PTS" % frame_points, "%d FPS" % round(fps),
             BAD if fps < FLICKER_FPS * 0.75 else
             WARN if fps < FLICKER_FPS else VALUE),
        ]

        f = self._font
        gap = f.size(" " * 2)[0]
        w1 = max(f.size(c[0])[0] for c in cols) + gap
        w2 = max(f.size(c[1])[0] for c in cols) + gap
        line = f.get_linesize()
        pad = 8
        width = pad * 2 + max(f.size(head)[0], f.size(foot)[0],
                              w1 + w2 + max(f.size(c[2])[0] for c in cols))
        height = pad * 2 + line * (len(cols) + 2)
        x, y = 8, surface.get_height() - height - 8

        box = pygame.Surface((width, height))
        box.set_alpha(190)
        box.fill((0, 0, 0))
        surface.blit(box, (x, y))

        cy = y + pad
        surface.blit(f.render(head, True, WARN if dirty else TITLE), (x + pad, cy))
        cy += line
        for c1, c2, c3, colour in cols:
            surface.blit(f.render(c1, True, colour), (x + pad, cy))
            surface.blit(f.render(c2, True, colour), (x + pad + w1, cy))
            surface.blit(f.render(c3, True, colour), (x + pad + w1 + w2, cy))
            cy += line
        surface.blit(f.render(foot, True, TITLE), (x + pad, cy))
