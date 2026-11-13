import asyncio
import logging
import random
from typing import List, Optional, Dict, Any
import aiohttp

logger = logging.getLogger("MaguraBot.JitoService")

# Official Jito Tip Accounts on Solana Mainnet
JITO_TIP_ACCOUNTS = [
    "Cw8CFyM9FkoMi7K7Crnq6HNQqf4uEMzpKw6QNghXLvN",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    "HFqU5x63VTxvQssQn138i2NN7MQfuebUJfTXUb4fpUjp",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
]

# Official Jito Block Engine Endpoints
JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles",
]

JITO_TIP_FLOOR_API = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"


class JitoService:
    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or JITO_ENDPOINTS[0]
        self._session: Optional[aiohttp.ClientSession] = None
        self._cached_tip_floor: Dict[str, float] = {
            "p25": 0.0001,
            "p50": 0.0005,
            "p75": 0.0010,
            "p95": 0.0025,
            "p99": 0.0050,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=50,
                keepalive_timeout=120,
                ttl_dns_cache=600,
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=4),
                headers={"User-Agent": "MaguraBot/1.0"}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def get_random_tip_account(self) -> str:
        """Select a random Jito tip account to distribute network tips."""
        return random.choice(JITO_TIP_ACCOUNTS)

    async def fetch_tip_floor(self) -> Dict[str, float]:
        """
        Fetch real-time Jito bundle tip percentiles (25th, 50th, 75th, 95th, 99th) in SOL.
        """
        session = await self._get_session()
        try:
            async with session.get(JITO_TIP_FLOOR_API) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        row = data[0]
                        self._cached_tip_floor = {
                            "p25": float(row.get("landed_tips_25th_percentile") or 0.0001),
                            "p50": float(row.get("landed_tips_50th_percentile") or 0.0005),
                            "p75": float(row.get("landed_tips_75th_percentile") or 0.0010),
                            "p95": float(row.get("landed_tips_95th_percentile") or 0.0025),
                            "p99": float(row.get("landed_tips_99th_percentile") or 0.0050),
                        }
        except Exception as e:
            logger.debug(f"Failed to fetch live Jito tip floor: {e}")
        return self._cached_tip_floor

    def calculate_dynamic_tip(
        self,
        base_tip_sol: float,
        safety_score: int,
        enable_dynamic: bool = True,
        max_tip_sol: float = 0.01
    ) -> float:
        """
        Calculates dynamic Jito tip escalator based on token safety tier and mempool urgency.
        - Score >= 90: High-confidence gem. Escalate by 1.8x to guarantee first-block bundle execution.
        - Score >= 75: Medium gem. Escalate by 1.3x.
        - Score < 75: Base tip.
        Capped strictly at max_tip_sol.
        """
        if not enable_dynamic:
            return min(base_tip_sol, max_tip_sol)

        multiplier = 1.0
        if safety_score >= 90:
            multiplier = 1.8
        elif safety_score >= 75:
            multiplier = 1.3
        elif safety_score < 50:
            multiplier = 0.8  # Reduce tip for low confidence tokens

        computed_tip = base_tip_sol * multiplier
        final_tip = max(0.0001, min(computed_tip, max_tip_sol))
        logger.info(
            f"⚡ Jito Tip Escalator: Base {base_tip_sol:.5f} SOL -> Dynamic {final_tip:.5f} SOL "
            f"(Score: {safety_score}/100, Multiplier: {multiplier}x)"
        )
        return round(final_tip, 6)

    async def send_bundle(self, transactions_b64: List[str]) -> Optional[str]:
        """
        Submit a transaction bundle directly to Jito Block Engines with multi-region racing.
        Bypasses the public mempool to prevent frontrunning/sandwich attacks.
        """
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [
                transactions_b64,
                {"encoding": "base64"}
            ]
        }

        # Race across all global Jito Block Engines concurrently
        endpoints = [self.endpoint] + [ep for ep in JITO_ENDPOINTS if ep != self.endpoint]

        async def _submit(ep: str) -> Optional[str]:
            try:
                async with session.post(
                    ep,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "error" not in data:
                            bundle_id = data.get("result")
                            if bundle_id:
                                return bundle_id
            except Exception:
                pass
            return None

        tasks = [asyncio.create_task(_submit(ep)) for ep in endpoints[:4]]
        for completed in asyncio.as_completed(tasks):
            bundle_id = await completed
            if bundle_id:
                logger.info(f"⚡ Jito bundle landed on fastest Block Engine. Bundle ID: {bundle_id}")
                return bundle_id

        logger.warning("Failed to submit bundle across all Jito Block Engines")
        return None
