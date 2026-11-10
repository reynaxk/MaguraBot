import logging
import random
from typing import List, Optional
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


class JitoService:
    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or JITO_ENDPOINTS[0]
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def get_random_tip_account(self) -> str:
        """Select a random Jito tip account to distribute network tips."""
        return random.choice(JITO_TIP_ACCOUNTS)

    async def send_bundle(self, transactions_b64: List[str]) -> Optional[str]:
        """
        Submit a transaction bundle directly to Jito Block Engine.
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

        try:
            async with session.post(
                self.endpoint,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    logger.warning(f"Jito bundle submission error: {data['error']}")
                    return None
                bundle_id = data.get("result")
                logger.info(f"Jito bundle submitted successfully. Bundle ID: {bundle_id}")
                return bundle_id
        except Exception as e:
            logger.warning(f"Failed to submit bundle to Jito: {e}")
            return None
