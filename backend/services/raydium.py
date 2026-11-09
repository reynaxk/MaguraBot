import asyncio
import json
import logging
from typing import Callable, Optional
import aiohttp
from backend.models.token import TokenData, LaunchpadType

logger = logging.getLogger("MaguraBot.Raydium")

RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
DEXSCREENER_LATEST_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"


class RaydiumListener:
    def __init__(self, on_token_discovered: Callable[[TokenData], None], rpc_ws_url: str):
        self.on_token_discovered = on_token_discovered
        self.rpc_ws_url = rpc_ws_url
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._seen_mints = set()
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Raydium new pool listener started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Raydium new pool listener stopped")

    async def _poll_loop(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        while self._running:
            try:
                # Fetch recently created Solana token pools from DEXScreener
                async with self._session.get(DEXSCREENER_LATEST_PROFILES) as resp:
                    if resp.status == 200:
                        profiles = await resp.json()
                        for p in profiles:
                            if p.get("chainId") != "solana":
                                continue
                            mint = p.get("tokenAddress")
                            if not mint or mint in self._seen_mints:
                                continue
                            self._seen_mints.add(mint)
                            
                            desc = (p.get("description") or "").strip()
                            symbol = p.get("symbol")
                            if not symbol:
                                first_word = desc.split()[0] if desc else ""
                                symbol = first_word[:8].upper() if (first_word and first_word.isalnum()) else f"RAY-{mint[:4].upper()}"

                            token = TokenData(
                                mint=mint,
                                symbol=symbol,
                                name=(desc[:32] if desc else f"Raydium Token {symbol}"),
                                source=LaunchpadType.RAYDIUM,
                                bonding_curve_pct=100.0,
                                is_migrated=True,
                                raw_metadata=p,
                            )
                            self.on_token_discovered(token)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Raydium poll tick error: {e}")
                
            await asyncio.sleep(4)
