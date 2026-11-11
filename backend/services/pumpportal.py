import asyncio
import json
import logging
from typing import Callable, Optional
import websockets
from backend.models.token import TokenData, LaunchpadType

logger = logging.getLogger("MaguraBot.PumpPortal")

WS_URL = "wss://pumpportal.fun/api/data"
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

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("PumpPortal WebSocket client started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PumpPortal WebSocket client stopped")

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
                            
                            # Handle newly minted tokens
                            if tx_type == "create" and data.get("mint"):
                                v_sol = float(data.get("vSolInBondingCurve", START_VSOL))
                                bonding_pct = calculate_bonding_pct(v_sol)
                                market_cap_sol = float(data.get("marketCapSol", 0)) if data.get("marketCapSol") else None

                                token = TokenData(
                                    mint=data["mint"],
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

                            elif tx_type == "migration" and data.get("mint"):
                                token = TokenData(
                                    mint=data["mint"],
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
                logger.warning(f"PumpPortal connection dropped: {conn_err}. Reconnecting in {delay}s...")
                await asyncio.sleep(delay)
