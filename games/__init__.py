"""Registry of playable games. Add a game here and it appears in the menu."""
from .asteroids import AsteroidsGame
from .pong import PongGame
from .slipstream import SlipstreamGame
from .missile import MissileGame
from .gyruss import GyrussGame
from .defender import DefenderGame
from .snake import SnakeGame

# Order here is the menu order.
GAMES = [AsteroidsGame, PongGame, SlipstreamGame, MissileGame, GyrussGame, DefenderGame, SnakeGame]

BY_KEY = {
    "asteroids": AsteroidsGame,
    "pong": PongGame,
    "slipstream": SlipstreamGame,
    "missile": MissileGame,
    "gyruss": GyrussGame,
    "defender": DefenderGame,
    "snake": SnakeGame,
}
