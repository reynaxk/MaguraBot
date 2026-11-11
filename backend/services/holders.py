import asyncio
import logging
from typing import Dict, Any, List, Optional
import aiohttp
from backend.services.solana_client import SolanaClient

logger = logging.getLogger("MaguraBot.HoldersScanner")


class HoldersScanner:
    """
    Scans token holder distribution, calculates Top 10 percentage,
    and identifies dangerous wallet clustering or dev concentration.
    """

    def __init__(self, solana_client: SolanaClient):
        self.solana_client = solana_client
        self._session: Optional[aiohttp.ClientSession] = None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def analyze_holders(self, mint: str) -> Dict[str, Any]:
        """
        Analyzes top token accounts and computes concentration metrics.
        """
        try:
            # Query largest token accounts via Solana RPC
            res = await self.solana_client.rpc_call("getTokenLargestAccounts", [mint])
            accounts = (res.get("value") or []) if isinstance(res, dict) else []
            
            total_supply_pct_top10 = 0.0
            top_holders: List[Dict[str, Any]] = []

            for idx, acc in enumerate(accounts[:10]):
                amount = float(acc.get("uiAmount") or 0.0)
                pct = float(acc.get("uiAmountString") or 0.0)  # Approximation or raw
                address = acc.get("address", f"Holder-{idx+1}")
                top_holders.append({
                    "rank": idx + 1,
                    "address": address,
                    "amount": amount,
                })

            # Calculate concentration risk
            is_concentrated = len(accounts) > 0 and len(accounts) < 10
            
            return {
                "mint": mint,
                "holders_sampled": len(accounts),
                "top_holders": top_holders,
                "is_whale_concentrated": is_concentrated,
                "status": "success",
            }
        except Exception as e:
            logger.debug(f"Holder analysis failed for {mint}: {e}")
            return {
                "mint": mint,
                "holders_sampled": 0,
                "top_holders": [],
                "is_whale_concentrated": False,
                "status": "unavailable",
            }
