import logging
import time
from typing import Dict, Tuple
from backend.models.config import BotConfig
from backend.models.token import TokenData

logger = logging.getLogger("MaguraBot.TradeAgent")


class TradeAgent:
    def __init__(self, config: BotConfig):
        self.config = config
        self._last_snipe_time = 0.0
        self._sniped_mints: Dict[str, float] = {}

    def can_snipe(self, token: TokenData, current_position_count: int) -> Tuple[bool, str]:
        """Check if trading conditions, concurrent position limits, and cooldowns allow a snipe."""
        if not self.config.is_running:
            return False, "Bot is currently paused"

        # Check maximum concurrent open positions
        if current_position_count >= self.config.max_positions:
            return False, f"Max open positions reached ({current_position_count}/{self.config.max_positions})"

        # Avoid double-sniping the same token
        if token.mint in self._sniped_mints:
            return False, f"Already sniped {token.symbol} previously"

        # Cooldown between snipes (minimum 1.5 seconds)
        now = time.time()
        if now - self._last_snipe_time < 1.5:
            return False, "Sniper cooldown active (rate limit protection)"

        return True, "Ready to snipe"

    def record_snipe(self, mint: str):
        self._last_snipe_time = time.time()
        self._sniped_mints[mint] = self._last_snipe_time
