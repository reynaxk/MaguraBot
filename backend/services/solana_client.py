import json
import logging
from typing import Optional, Dict, Any
import aiohttp
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey

logger = logging.getLogger("MaguraBot.SolanaClient")


class SolanaClient:
    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.rpc_url = rpc_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def rpc_call(self, method: str, params: list) -> Dict[str, Any]:
        """Send an asynchronous JSON-RPC call to the configured Solana node."""
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        try:
            async with session.post(
                self.rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    logger.error(f"RPC error on {method}: {data['error']}")
                    return {"error": data["error"]}
                return data.get("result", {})
        except Exception as e:
            logger.warning(f"RPC request failed ({method}): {e}")
            return {"error": str(e)}

    async def get_balance(self, pubkey_str: str) -> float:
        """Return account balance in SOL."""
        res = await self.rpc_call("getBalance", [pubkey_str, {"commitment": "confirmed"}])
        if isinstance(res, dict) and "value" in res:
            lamports = res["value"]
            return lamports / 1_000_000_000
        return 0.0

    async def get_latest_blockhash(self) -> Optional[str]:
        """Fetch latest blockhash for transaction construction."""
        res = await self.rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}])
        if isinstance(res, dict) and "value" in res:
            return res["value"].get("blockhash")
        return None

    async def send_raw_transaction(self, raw_tx_b64: str, skip_preflight: bool = True) -> Optional[str]:
        """Broadcast raw base64 transaction to Solana."""
        res = await self.rpc_call(
            "sendTransaction",
            [
                raw_tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": skip_preflight,
                    "preflightCommitment": "processed",
                },
            ],
        )
        if isinstance(res, str):
            return res
        return None

    @staticmethod
    def load_keypair(key_str: Optional[str]) -> Optional[Keypair]:
        """Parse keypair from Base58 string or JSON byte array."""
        if not key_str:
            return None
        key_str = key_str.strip()
        try:
            if key_str.startswith("[") and key_str.endswith("]"):
                data = bytes(json.loads(key_str))
                return Keypair.from_bytes(data)
            else:
                return Keypair.from_base58_string(key_str)
        except Exception as e:
            logger.error(f"Failed to parse keypair: {e}")
            return None
