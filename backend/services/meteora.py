import asyncio
import logging
from typing import Callable, Optional
import aiohttp
from backend.models.token import TokenData, LaunchpadType

logger = logging.getLogger("MaguraBot.Meteora")

METEORA_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q=meteora"


class MeteoraListener:
    """
    Listens for active and newly created DLMM / Dynamic pools on Meteora DEX (Solana).
    """

    def __init__(self, on_token_discovered: Callable[[TokenData], None]):
        self.on_token_discovered = on_token_discovered
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._seen_mints = set()
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Meteora DLMM pool listener started")

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
        logger.info("Meteora DLMM pool listener stopped")

    async def _poll_loop(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            headers={"User-Agent": "MaguraBot/1.0"},
        )
        while self._running:
            try:
                async with self._session.get(METEORA_SEARCH_URL) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs") or []
                        for pair in pairs:
                            if pair.get("chainId") != "solana":
                                continue
                            dex_id = (pair.get("dexId") or "").lower()
                            if "meteora" not in dex_id:
                                continue

                            base_token = pair.get("baseToken") or {}
                            mint = base_token.get("address")
                            if not mint or mint in self._seen_mints:
                                continue
                            self._seen_mints.add(mint)

                            symbol = base_token.get("symbol") or f"MET-{mint[:4].upper()}"
                            name = base_token.get("name") or f"Meteora Token {symbol}"
                            price_sol = 0.0
                            price_usd = 0.0
                            try:
                                price_sol = float(pair.get("priceNative") or 0.0)
                                price_usd = float(pair.get("priceUsd") or 0.0)
                            except (ValueError, TypeError):
                                pass

                            liquidity_usd = None
                            liq_data = pair.get("liquidity") or {}
                            if "usd" in liq_data:
                                try:
                                    liquidity_usd = float(liq_data["usd"])
                                except (ValueError, TypeError):
                                    pass

                            token = TokenData(
                                mint=mint,
                                symbol=symbol,
                                name=name[:40],
                                source=LaunchpadType.METEORA,
                                price_sol=price_sol,
                                price_usd=price_usd,
                                liquidity_usd=liquidity_usd,
                                bonding_curve_pct=100.0,
                                is_migrated=True,
                                raw_metadata=pair,
                            )
                            logger.info(f"Discovered Meteora DLMM token: {symbol} ({mint[:8]}...) - Liq: ${liquidity_usd or 0:,.0f}")
                            self.on_token_discovered(token)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Meteora poll tick error: {e}")

            await asyncio.sleep(5)
