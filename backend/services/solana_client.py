import asyncio
import base64
import json
import logging
import time
from typing import Optional, Dict, Any, List
import aiohttp
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned

logger = logging.getLogger("MaguraBot.SolanaClient")

DEFAULT_BACKUP_RPCS = [
    "https://solana-mainnet.rpc.extrnode.com",
    "https://rpc.ankr.com/solana",
]


class SolanaClient:
    def __init__(
        self,
        rpc_url: str = "https://api.mainnet-beta.solana.com",
        backup_urls: Optional[List[str]] = None,
    ):
        self.rpc_url = rpc_url
        self.backup_urls = backup_urls if backup_urls is not None else list(DEFAULT_BACKUP_RPCS)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def rpc_call(self, method: str, params: list) -> Dict[str, Any]:
        """Send JSON-RPC call with automatic multi-node failover."""
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        
        endpoints = [self.rpc_url] + [u for u in self.backup_urls if u != self.rpc_url]
        last_err = None

        for url in endpoints:
            try:
                async with session.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "error" not in data:
                            return data.get("result", {})
                        last_err = data["error"]
                    else:
                        last_err = f"HTTP {resp.status}"
            except Exception as e:
                last_err = str(e)
                continue

        logger.debug(f"RPC call {method} failed across all nodes: {last_err}")
        return {"error": last_err or "All endpoints failed"}

    async def get_balance(self, pubkey_str: str) -> float:
        """Return account balance in SOL."""
        res = await self.rpc_call("getBalance", [pubkey_str, {"commitment": "confirmed"}])
        if isinstance(res, dict) and "value" in res:
            lamports = res["value"]
            return lamports / 1_000_000_000
        return 0.0

    async def get_latency_ms(self) -> float:
        """Measure roundtrip RPC ping latency in milliseconds."""
        t0 = time.time()
        try:
            res = await self.rpc_call("getHealth", [])
            latency = (time.time() - t0) * 1000.0
            return round(latency, 1)
        except Exception:
            return -1.0

    async def get_latest_blockhash(self) -> Optional[str]:
        """Fetch latest blockhash for transaction construction."""
        res = await self.rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}])
        if isinstance(res, dict) and "value" in res:
            return res["value"].get("blockhash")
        return None

    async def send_raw_transaction(self, raw_tx_b64: str, skip_preflight: bool = True) -> Optional[str]:
        """Broadcast raw base64 transaction to Solana using multi-node TPU racing."""
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                raw_tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": skip_preflight,
                    "preflightCommitment": "processed",
                },
            ],
        }

        endpoints = [self.rpc_url] + [u for u in self.backup_urls if u != self.rpc_url]

        async def _broadcast_to(url: str) -> Optional[str]:
            try:
                async with session.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data.get("result")
                        if isinstance(result, str):
                            return result
            except Exception:
                pass
            return None

        # Race across all endpoints simultaneously for fastest block propagation
        tasks = [asyncio.create_task(_broadcast_to(u)) for u in endpoints]
        for completed in asyncio.as_completed(tasks):
            sig = await completed
            if sig:
                return sig
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

    @staticmethod
    def sign_transaction(raw_tx_b64: str, keypair: Keypair) -> str:
        """
        Sign a base64 serialized VersionedTransaction with keypair and return the signed base64 string.
        """
        raw_bytes = base64.b64decode(raw_tx_b64)
        tx = VersionedTransaction.from_bytes(raw_bytes)
        message_bytes = to_bytes_versioned(tx.message)
        signature = keypair.sign_message(message_bytes)
        signed_tx = VersionedTransaction.populate(tx.message, [signature])
        return base64.b64encode(bytes(signed_tx)).decode("utf-8")

    @staticmethod
    def generate_keypair() -> tuple[str, str]:
        """Generate a fresh Solana keypair, returning (public_key, private_key_base58)."""
        kp = Keypair()
        return str(kp.pubkey()), str(kp)

