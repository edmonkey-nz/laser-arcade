"""Joystick / game controller input, translated into pygame key codes.

The shell already speaks keyboard, and `keymap` already lets the config screen
rebind actions to keys -- so the cheapest way to support a pad is to make it
look like a keyboard. This module polls every connected joystick and reports
which *keys* are currently held, plus the edges (newly pressed) for menus.

Each frame the held set is rebuilt from scratch as the union of buttons, hat
and axes, then diffed against the previous frame. Rebuilding rather than
mutating matters: several sources map onto the same key (a pad may report its
D-pad as a hat *and* as buttons), and with incremental updates whichever source
ran last would clobber the others.

Cheap pads chatter -- a held hat direction can drop to centre for a frame or
two -- so releases are held back briefly (`STICKY_MS`) before they count.
"""
from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

import pygame

# Button number -> key. Pads disagree wildly about numbering, so this is only a
# starting point; run `python controller_test.py` to read off your own. Numbers
# that don't exist on the attached pad are simply ignored.
# Several numbers map to the same action on purpose, so one map covers pads
# that number their buttons differently -- a generic USB pad and a DualShock 4
# can both be plugged in at once and both work. Extra numbers that don't exist
# on the attached pad are simply never pressed.
DEFAULT_BUTTON_MAP: Dict[int, int] = {
    0: pygame.K_SPACE,      # fire -- also "start" (space launches from the menu)
    1: pygame.K_LSHIFT,     # alternate / hyperspace
    6: pygame.K_ESCAPE,     # back: game -> menu, config -> save & back
    9: pygame.K_ESCAPE,     # ...and DualShock 4 "Options"
    8: pygame.K_r,          # retry / reset  (DualShock 4 "Share")
    10: pygame.K_r,         # ...and the generic pad's equivalent
    12: pygame.K_UP,        # D-pad, only on pads that report it as buttons
    13: pygame.K_DOWN,      # (ignored when the pad has a real hat -- see
    14: pygame.K_LEFT,      #  DPAD_BUTTONS below, where 12 would otherwise
    15: pygame.K_RIGHT,     #  collide with a DualShock 4's right-stick click)
}

# Hat (D-pad) direction -> key. Diagonals are handled by splitting the axes.
HAT_X = {-1: pygame.K_LEFT, 1: pygame.K_RIGHT}
HAT_Y = {-1: pygame.K_DOWN, 1: pygame.K_UP}

# The D-pad entries in the button map above are a fallback for pads that report
# their D-pad as ordinary buttons. On a pad that has a real hat those numbers
# mean something else entirely -- on a DualShock 4, button 12 is the right
# stick click -- so they are ignored whenever a hat is present.
DPAD_BUTTONS = frozenset((12, 13, 14, 15))

# Which axes make up each stick. Pads disagree, and getting it wrong is not
# subtle: an analogue trigger rests at -1.0, so mistaking one for a stick axis
# reads as a direction held down forever. Rather than hard-code it, the resting
# position of every axis is sampled when a pad connects -- anything already
# pinned to an extreme is a trigger, and the first two axes that aren't are the
# right stick. That lands on (2, 3) for a generic pad and (3, 4) for a
# DualShock 4, both correct.
#
# Set AUTODETECT_STICKS = False to force the fallbacks below instead.
AUTODETECT_STICKS = True
LEFT_STICK_AXES = (0, 1)
RIGHT_STICK_AXES = (2, 3)
# Resting deflection past which an axis is taken to be a trigger, not a stick.
TRIGGER_REST = -0.9

# Sticks are reported to games as vectors *and* synthesised into keys. The two
# sticks deliberately use different keys so a single pad can drive both Pong
# paddles: left stick -> WASD (= p1), right stick -> arrows (= p2). Every
# single-player game binds both sets, so either stick steers there.
LEFT_STICK_KEYS = (pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s)
RIGHT_STICK_KEYS = (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN)

# Past this deflection a stick counts as a key press.
AXIS_DEADZONE = 0.5
# Below this, a stick reads as centred (drift on cheap pads never reaches 0).
AXIS_NOISE = 0.15

# A release must persist this long before it counts, to ride out pad chatter.
STICKY_MS = 60
# Auto-repeat for the edge set, so holding a direction walks a menu the way a
# held keyboard key does. Games read the held set and are unaffected. Only the
# directions repeat -- a repeating fire button would re-launch from the menu,
# and a repeating Escape would walk straight out of the app.
REPEAT_KEYS = frozenset((pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT))
REPEAT_DELAY_MS = 300
REPEAT_RATE_MS = 120


class JoystickManager:
    """Polls connected pads and reports keyboard-equivalent input."""

    def __init__(self, button_map: Optional[Dict[int, int]] = None,
                 debug: bool = False):
        self.joysticks: Dict[int, pygame.joystick.Joystick] = {}
        self.layouts: Dict[int, tuple] = {}   # instance id -> (left, right) axes
        self.button_map = dict(button_map or DEFAULT_BUTTON_MAP)
        self.debug = debug
        self.held: Set[int] = set()          # keys currently held, after sticky
        # Stick deflection as (x, y) in [-1, 1], y positive *up* to match world
        # space. (0.0, 0.0) when centred or absent.
        self.left_stick: Tuple[float, float] = (0.0, 0.0)
        self.right_stick: Tuple[float, float] = (0.0, 0.0)
        self._pending_release: Dict[int, int] = {}   # key -> ms first seen down
        self._repeat_at: Dict[int, int] = {}         # key -> ms of next repeat
        self._initialized = False

    def init(self) -> None:
        """Initialise joystick support (call after pygame.init())."""
        if self._initialized:
            return
        pygame.joystick.init()
        self._initialized = True

    # -- polling ------------------------------------------------------------
    def update(self) -> Tuple[Set[int], Set[int]]:
        """Poll every pad; return (pressed, released) as pygame key codes.

        `pressed` carries edges *and* auto-repeats; `held` (the attribute) is
        the authoritative set of keys the pad is holding down right now.
        """
        if not self._initialized:
            self.init()
        self._detect_joysticks()

        now = pygame.time.get_ticks()
        raw = self._raw_keys()

        # A key that vanished from `raw` only really releases once it has been
        # absent for STICKY_MS -- chatter reappears well inside that window.
        for key in self.held - raw:
            self._pending_release.setdefault(key, now)
        for key in raw:
            self._pending_release.pop(key, None)
        settled = {k for k, since in self._pending_release.items()
                   if now - since >= STICKY_MS}
        for key in settled:
            del self._pending_release[key]

        new_held = (self.held | raw) - settled
        pressed = new_held - self.held
        released = self.held - new_held
        self.held = new_held

        # auto-repeat, for menu navigation
        for key in pressed & REPEAT_KEYS:
            self._repeat_at[key] = now + REPEAT_DELAY_MS
        for key in released:
            self._repeat_at.pop(key, None)
        for key, due in self._repeat_at.items():
            if now >= due:
                pressed.add(key)
                self._repeat_at[key] = now + REPEAT_RATE_MS

        if self.debug and (pressed or released):
            names = lambda ks: ",".join(sorted(pygame.key.name(k) for k in ks))
            print("[joy] +%s -%s" % (names(pressed) or "-", names(released) or "-"))
        return pressed, released

    def _raw_keys(self) -> Set[int]:
        """The keys every source says are down this instant, unioned.

        Also refreshes `left_stick` / `right_stick` as a side effect, since it
        is the same poll.
        """
        keys: Set[int] = set()
        left = right = (0.0, 0.0)
        for iid, joystick in list(self.joysticks.items()):
            try:
                has_hat = joystick.get_numhats() > 0
                for n in range(joystick.get_numbuttons()):
                    if has_hat and n in DPAD_BUTTONS:
                        continue
                    if joystick.get_button(n) and n in self.button_map:
                        keys.add(self.button_map[n])

                for h in range(joystick.get_numhats()):
                    hx, hy = joystick.get_hat(h)
                    if hx in HAT_X:
                        keys.add(HAT_X[hx])
                    if hy in HAT_Y:
                        keys.add(HAT_Y[hy])

                layout = self.layouts.get(iid, (LEFT_STICK_AXES, RIGHT_STICK_AXES))
                for axes, stick_keys, is_left in (
                        (layout[0], LEFT_STICK_KEYS, True),
                        (layout[1], RIGHT_STICK_KEYS, False)):
                    v = self._read_stick(joystick, axes)
                    if v is None:
                        continue
                    if is_left:
                        left = v
                    else:
                        right = v
                    k_left, k_right, k_up, k_down = stick_keys
                    x, y = v
                    if x < -AXIS_DEADZONE:
                        keys.add(k_left)
                    elif x > AXIS_DEADZONE:
                        keys.add(k_right)
                    if y > AXIS_DEADZONE:
                        keys.add(k_up)
                    elif y < -AXIS_DEADZONE:
                        keys.add(k_down)
            except pygame.error:
                # A Bluetooth pad can vanish mid-frame. Drop it and carry on
                # rather than taking the arcade down with it.
                self._drop(iid)

        self.left_stick, self.right_stick = left, right
        return keys

    @staticmethod
    def _read_stick(joystick, axes) -> Optional[Tuple[float, float]]:
        """Deflection of one stick as (x, y), y positive up, or None if this pad
        has no such axes. Values inside the noise floor read as centred."""
        if axes is None:
            return None
        ax, ay = axes
        if joystick.get_numaxes() <= max(ax, ay):
            return None
        x, y = joystick.get_axis(ax), -joystick.get_axis(ay)   # SDL y is down
        return (0.0 if abs(x) < AXIS_NOISE else x,
                0.0 if abs(y) < AXIS_NOISE else y)

    def _detect_joysticks(self) -> None:
        """Pick up newly connected pads and forget disconnected ones.

        Keyed by SDL instance id, not device index: indices are renumbered when
        a device goes away, so an index-keyed cache silently aliases one pad
        onto another after a Bluetooth drop.
        """
        seen = set()
        for i in range(pygame.joystick.get_count()):
            try:
                joy = pygame.joystick.Joystick(i)
                joy.init()
                iid = joy.get_instance_id()
                seen.add(iid)
                if iid in self.joysticks:
                    continue
                self.joysticks[iid] = joy
                self.layouts[iid] = self._axis_layout(joy)
                print("[joystick] connected: %s (%d buttons, %d hats, %d axes;"
                      " left stick %s, right stick %s)"
                      % (joy.get_name(), joy.get_numbuttons(),
                         joy.get_numhats(), joy.get_numaxes(),
                         self.layouts[iid][0], self.layouts[iid][1]))
            except pygame.error:
                continue
        for iid in [i for i in self.joysticks if i not in seen]:
            self._drop(iid)

    def _drop(self, iid) -> None:
        joy = self.joysticks.pop(iid, None)
        self.layouts.pop(iid, None)
        name = "?"
        try:
            name = joy.get_name() if joy is not None else "?"
        except pygame.error:
            pass
        print("[joystick] disconnected: %s" % name)

    @staticmethod
    def _axis_layout(joystick):
        """(left_axes, right_axes) for this pad, from where its axes rest.

        A stick sits near 0 when untouched; an analogue trigger sits at -1. So
        anything already pinned low is a trigger, and the first two survivors
        past the left stick are the right stick.
        """
        if not AUTODETECT_STICKS:
            return (LEFT_STICK_AXES, RIGHT_STICK_AXES)
        try:
            n = joystick.get_numaxes()
            if n < 2:
                return (None, None)
            spare = [i for i in range(2, n)
                     if joystick.get_axis(i) > TRIGGER_REST]
            right = (spare[0], spare[1]) if len(spare) >= 2 else None
            return ((0, 1), right)
        except pygame.error:
            return (LEFT_STICK_AXES, RIGHT_STICK_AXES)

    def close(self) -> None:
        self.joysticks.clear()
        self.held.clear()
        self._pending_release.clear()
        self._repeat_at.clear()
