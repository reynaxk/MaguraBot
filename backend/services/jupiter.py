import logging
from typing import Optional, Dict, Any
import aiohttp

logger = logging.getLogger("MaguraBot.Jupiter")

# Standard WSOL mint address on Solana
WSOL_MINT = "So11111111111111111111111111111111111111112"


class JupiterService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://api.jup.ag/swap/v1" if api_key else "https://lite-api.jup.ag/swap/v1"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["x-api-key"] = self.api_key
            connector = aiohttp.TCPConnector(
                limit=100,
                keepalive_timeout=120,
                ttl_dns_cache=600,
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int = 500,
    ) -> Optional[Dict[str, Any]]:
        """Fetch best quote from Jupiter v6 API."""
        session = await self._get_session()
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": str(slippage_bps),
            "swapMode": "ExactIn",
        }
        url = f"{self.base_url}/quote"
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                text = await resp.text()
                logger.warning(f"Jupiter quote failed ({resp.status}): {text}")
                return None
        except Exception as e:
            logger.error(f"Jupiter quote request exception: {e}")
            return None

    async def get_swap_transaction(
        self,
        quote_response: Dict[str, Any],
        user_public_key: str,
        priority_fee_lamports: int = 100000,
    ) -> Optional[str]:
        """Request serialized swap transaction from Jupiter."""
        session = await self._get_session()
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": priority_fee_lamports,
        }
        url = f"{self.base_url}/swap"
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("swapTransaction")
                text = await resp.text()
                logger.error(f"Jupiter swap building failed: {text}")
                return None
        except Exception as e:
            logger.error(f"Jupiter swap transaction exception: {e}")
            return None
