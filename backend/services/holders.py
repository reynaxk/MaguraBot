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
            
            # Query supply to get exact percentages
            supply_res = await self.solana_client.rpc_call("getTokenSupply", [mint])
            total_supply = 0.0
            if isinstance(supply_res, dict) and "value" in supply_res:
                total_supply = float(supply_res["value"].get("uiAmount") or 0.0)

            total_supply_pct_top10 = 0.0
            top_holders: List[Dict[str, Any]] = []

            for idx, acc in enumerate(accounts[:10]):
                amount = float(acc.get("uiAmount") or 0.0)
                pct = (amount / total_supply * 100.0) if total_supply > 0 else 0.0
                address = acc.get("address", f"Holder-{idx+1}")
                top_holders.append({
                    "rank": idx + 1,
                    "address": address,
                    "amount": amount,
                    "pct": round(pct, 2),
                })
                # Exclude the primary bonding curve / AMM pool if it holds >50% of supply
                if idx > 0 or pct < 50.0:
                    total_supply_pct_top10 += pct

            return {
                "mint": mint,
                "holders_sampled": len(accounts),
                "top_holders": top_holders,
                "top10_holding_pct": round(total_supply_pct_top10, 2),
                "is_whale_concentrated": total_supply_pct_top10 > 30.0 or len(accounts) < 10,
                "status": "success",
            }
        except Exception as e:
            logger.debug(f"Holder analysis failed for {mint}: {e}")
            return {
                "mint": mint,
                "holders_sampled": 0,
                "top_holders": [],
                "top10_holding_pct": 0.0,
                "is_whale_concentrated": False,
                "status": "unavailable",
            }
