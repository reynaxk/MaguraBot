import asyncio
import logging
from typing import Callable, Optional
from backend.models.config import BotConfig
from backend.models.token import TokenData
from backend.services.pumpportal import PumpPortalClient
from backend.services.raydium import RaydiumListener

logger = logging.getLogger("MaguraBot.DiscoveryAgent")


class DiscoveryAgent:
    def __init__(self, config: BotConfig, on_token_discovered: Callable[[TokenData], None]):
        self.config = config
        self.on_token_discovered = on_token_discovered
        self.pump_client: Optional[PumpPortalClient] = None
        self.raydium_listener: Optional[RaydiumListener] = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True

        if self.config.enable_pumpfun:
            self.pump_client = PumpPortalClient(self._handle_discovered_token)
            await self.pump_client.start()

        if self.config.enable_raydium:
            self.raydium_listener = RaydiumListener(
                self._handle_discovered_token,
                self.config.solana_ws_url,
            )
            await self.raydium_listener.start()

        logger.info("DiscoveryAgent initialized across Pump.fun and Raydium")

    async def stop(self):
        self._running = False
        if self.pump_client:
            await self.pump_client.stop()
        if self.raydium_listener:
            await self.raydium_listener.stop()
        logger.info("DiscoveryAgent stopped")

    def _handle_discovered_token(self, token: TokenData):
        if not self._running or not self.config.is_running:
            return
        self.on_token_discovered(token)
