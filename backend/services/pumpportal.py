import asyncio
import json
import logging
from typing import Callable, Optional, Set
import aiohttp
import websockets
from backend.models.token import TokenData, LaunchpadType

logger = logging.getLogger("MaguraBot.PumpPortal")

WS_URL = "wss://pumpportal.fun/api/data"
GECKOTERMINAL_NEW_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1"
START_VSOL = 30.0
END_VSOL = 115.0


def calculate_bonding_pct(v_sol: float) -> float:
    """Pump.fun curve starts at ~30 vSOL and completes at ~115 vSOL."""
    return max(0.0, min(100.0, ((v_sol - START_VSOL) / (END_VSOL - START_VSOL)) * 100.0))


class PumpPortalClient:
    def __init__(self, on_token_discovered: Callable[[TokenData], None]):
        self.on_token_discovered = on_token_discovered
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._seen_mints: Set[str] = set()

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        self._poll_task = asyncio.create_task(self._fallback_poll_loop())
        logger.info("PumpPortal discovery client started (WebSocket + GeckoTerminal stream)")

    async def stop(self):
        self._running = False
        for task in (self._task, self._poll_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("PumpPortal discovery client stopped")

    async def _listen_loop(self):
        while self._running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    # Subscribe to new token creations & migrations
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    logger.info("Connected to PumpPortal WebSocket feed")

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            tx_type = data.get("txType")
                            mint = data.get("mint")
                            if not mint or mint in self._seen_mints:
                                continue
                            self._seen_mints.add(mint)
                            
                            # Handle newly minted tokens
                            if tx_type == "create":
                                v_sol = float(data.get("vSolInBondingCurve", START_VSOL))
                                bonding_pct = calculate_bonding_pct(v_sol)
                                market_cap_sol = float(data.get("marketCapSol", 0)) if data.get("marketCapSol") else None

                                token = TokenData(
                                    mint=mint,
                                    symbol=data.get("symbol", "UNKNOWN"),
                                    name=data.get("name", "Unknown Token"),
                                    source=LaunchpadType.PUMPFUN,
                                    creator=data.get("traderPublicKey"),
                                    market_cap_sol=market_cap_sol,
                                    v_sol_in_bonding_curve=v_sol,
                                    bonding_curve_pct=bonding_pct,
                                    is_migrated=False,
                                    raw_metadata=data,
                                )
                                self.on_token_discovered(token)

                            elif tx_type == "migration":
                                token = TokenData(
                                    mint=mint,
                                    symbol=data.get("symbol", "MIGRATED"),
                                    name=data.get("name", "Migrated Raydium Pool"),
                                    source=LaunchpadType.PUMPFUN,
                                    is_migrated=True,
                                    bonding_curve_pct=100.0,
                                    raw_metadata=data,
                                )
                                self.on_token_discovered(token)

                        except Exception as parse_err:
                            logger.debug(f"Error parsing PumpPortal message: {parse_err}")

            except asyncio.CancelledError:
                break
            except Exception as conn_err:
                delay = 30 if "403" in str(conn_err) else 5
                logger.warning(f"PumpPortal WebSocket dropped: {conn_err}. Reconnecting in {delay}s...")
                await asyncio.sleep(delay)

    async def _fallback_poll_loop(self):
        """High-frequency poller for new Pump.fun and PumpSwap pools via GeckoTerminal API."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MaguraBot/1.0"}
        ) as session:
            while self._running:
                try:
                    async with session.get(GECKOTERMINAL_NEW_POOLS_URL) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pools = data.get("data", [])
                            for p in pools:
                                relationships = p.get("relationships", {})
                                base_token = relationships.get("base_token", {}).get("data", {})
                                base_id = base_token.get("id", "")
                                mint = base_id.replace("solana_", "").strip()
                                if not mint or mint in self._seen_mints:
                                    continue

                                dex = relationships.get("dex", {}).get("data", {}).get("id", "").lower()
                                is_pump = "pump" in dex or mint.endswith("pump")
                                if not is_pump:
                                    continue

                                self._seen_mints.add(mint)
                                attrs = p.get("attributes", {})
                                name_raw = attrs.get("name", "")
                                symbol = attrs.get("symbol") or (name_raw.split("/")[0].strip() if "/" in name_raw else name_raw[:8])
                                if not symbol:
                                    symbol = "PUMP"

                                price_sol = float(attrs.get("base_token_price_native_currency") or 0.0)
                                price_usd = float(attrs.get("base_token_price_usd") or 0.0)
                                fdv_usd = float(attrs.get("fdv_usd") or 0.0) if attrs.get("fdv_usd") else None
                                reserve_usd = float(attrs.get("reserve_in_usd") or 0.0) if attrs.get("reserve_in_usd") else None

                                # Bonding curve estimate (starts ~$4k, migrates ~$69k)
                                bonding_pct = min(100.0, max(5.0, (fdv_usd / 69000.0 * 100.0))) if fdv_usd else 15.0

                                token = TokenData(
                                    mint=mint,
                                    symbol=symbol.upper(),
                                    name=name_raw or f"Pump.fun Token {symbol}",
                                    source=LaunchpadType.PUMPFUN,
                                    price_sol=price_sol,
                                    price_usd=price_usd,
                                    market_cap_usd=fdv_usd,
                                    liquidity_usd=reserve_usd,
                                    bonding_curve_pct=bonding_pct,
                                    is_migrated="pumpswap" in dex,
                                    raw_metadata=p,
                                )
                                self.on_token_discovered(token)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Pump.fun fallback poll error: {e}")

                await asyncio.sleep(2.5)
