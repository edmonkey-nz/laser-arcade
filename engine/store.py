"""Persist the player's config (per-game PPS, key bindings, pincushion) to a
small JSON file in the user's home, and apply it to / read it from Settings.
High scores live beside it in their own file, keyed by game.
"""
from __future__ import annotations

import json
import os
from typing import Dict

from .keymap import KeyMap

CONFIG_DIR = os.path.expanduser("~/.laser-arcade")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
HIGHSCORE_PATH = os.path.join(CONFIG_DIR, "highscores.json")


def load() -> Dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save(data: Dict) -> bool:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


# Two things are deliberately absent from everything below, and must stay
# absent:
#
#   max_brightness -- the ceiling is NOT persisted. Every launch starts at 5%
#       bring-up power and the operator has to raise it on purpose. Upstream
#       laser-laser-laser does persist it (see SAFETY.md "Known gaps": raise it
#       once and it stays raised). An arcade cabinet gets power-cycled by
#       people who are not the operator, so this repo is deliberately stricter.
#
#   armed -- there is no auto-arm flag and never should be. The ARM gate is the
#       per-session guarantee; a remembered "armed" would destroy it.

def apply_to(cfg, data: Dict) -> None:
    """Load persisted values onto a Settings instance."""
    cfg.game_pps = {str(k): int(v) for k, v in data.get("pps", {}).items()}
    cfg.keystone_h = float(data.get("keystone_h", 0.0))
    cfg.keystone_v = float(data.get("keystone_v", 0.0))
    cfg.config_laser_output = bool(data.get("config_laser_output", False))
    cfg.keymap = KeyMap(data.get("keys"))
    # Which projector this cabinet has is a property of the cabinet, so it is
    # worth remembering. Arm state and the ceiling are not (see above).
    kind = str(data.get("output_kind", cfg.output_kind))
    if kind in ("none", "helios", "lasercube"):
        cfg.output_kind = kind


def from_settings(cfg) -> Dict:
    """Build the JSON-serialisable dict from a Settings instance."""
    return {
        "pps": {k: int(v) for k, v in getattr(cfg, "game_pps", {}).items()},
        "keystone_h": float(getattr(cfg, "keystone_h", 0.0)),
        "keystone_v": float(getattr(cfg, "keystone_v", 0.0)),
        "config_laser_output": bool(getattr(cfg, "config_laser_output", False)),
        "keys": cfg.keymap.to_dict() if getattr(cfg, "keymap", None) else {},
        "output_kind": str(getattr(cfg, "output_kind", "none")),
    }


def save_settings(cfg) -> bool:
    return save(from_settings(cfg))


# -- high scores ------------------------------------------------------------
def load_highscores() -> Dict[str, int]:
    """game key -> best score. Missing or corrupt file just means no scores
    yet; a cabinet should still boot."""
    try:
        with open(HIGHSCORE_PATH) as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items()}
    except Exception:
        return {}


def save_highscores(scores: Dict[str, int]) -> bool:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(HIGHSCORE_PATH, "w") as f:
            json.dump({str(k): int(v) for k, v in sorted(scores.items())},
                      f, indent=2)
        return True
    except Exception:
        return False
