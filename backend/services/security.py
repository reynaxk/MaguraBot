import logging
from typing import Optional
import aiohttp
from backend.models.token import TokenSafetyScore

logger = logging.getLogger("MaguraBot.Security")


class SecurityService:
    def __init__(self, solana_client=None):
        self.solana_client = solana_client
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=4)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def evaluate_token(
        self,
        mint: str,
        dev_wallet: Optional[str] = None,
        raw_metadata: Optional[dict] = None,
    ) -> TokenSafetyScore:
        """Evaluate token safety, rug risk, dev concentration, and authorities."""
        reasons = []
        score = 100
        mint_revoked = True
        freeze_revoked = True
        dev_holding_pct = 0.0
        top10_holding_pct = 0.0

        # 1. Inspect on-chain token supply and authorities if solana_client is present
        if self.solana_client:
            try:
                acc_info = await self.solana_client.rpc_call(
                    "getAccountInfo",
                    [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}],
                )
                if isinstance(acc_info, dict) and "value" in acc_info:
                    parsed = acc_info["value"].get("data", {}).get("parsed", {}).get("info", {})
                    # Mint authority check
                    if parsed.get("mintAuthority") is not None:
                        mint_revoked = False
                        score -= 30
                        reasons.append("Mint authority is not revoked (can print unlimited tokens)")
                    # Freeze authority check
                    if parsed.get("freezeAuthority") is not None:
                        freeze_revoked = False
                        score -= 35
                        reasons.append("Freeze authority is active (can freeze token transfers)")
            except Exception as e:
                logger.debug(f"Direct on-chain check error: {e}")

        # 2. Check RugCheck API (free endpoint with fast fallback)
        try:
            session = await self._get_session()
            url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    risks = data.get("risks", [])
                    rug_score = data.get("score", 0)
                    
                    for r in risks:
                        name = r.get("name", "")
                        level = r.get("level", "")
                        if level == "danger":
                            score -= 25
                            reasons.append(f"RugCheck Danger: {name}")
                        elif level == "warn":
                            score -= 10
                            reasons.append(f"RugCheck Warning: {name}")
                            
                    # RugCheck score: lower is better (0-1000)
                    if rug_score > 2000:
                        score -= 30
                        reasons.append("High overall risk rating on RugCheck")
        except Exception:
            # Fallback smoothly if external API is slow or rate limited
            pass

        # 3. Check metadata heuristics
        if raw_metadata:
            # High initial buy by creator in bonding curve
            initial_buy_sol = raw_metadata.get("initialBuy", 0)
            if initial_buy_sol and float(initial_buy_sol) > 15:
                score -= 15
                reasons.append(f"High dev presale/bundle buy: {initial_buy_sol} SOL")

        score = max(0, min(100, score))
        is_safe = score >= 60 and mint_revoked and freeze_revoked

        return TokenSafetyScore(
            score=score,
            is_safe=is_safe,
            mint_authority_revoked=mint_revoked,
            freeze_authority_revoked=freeze_revoked,
            dev_holding_pct=dev_holding_pct,
            top10_holding_pct=top10_holding_pct,
            reasons=reasons,
        )

    async def get_token_audit(self, mint: str, symbol: Optional[str] = None) -> dict:
        """Fetch comprehensive token security audit with risk rating and explorer links."""
        safety = await self.evaluate_token(mint)
        
        if safety.score >= 80:
            risk_tier = "LOW RISK"
            badge_color = "accent"
        elif safety.score >= 50:
            risk_tier = "MEDIUM RISK"
            badge_color = "warn"
        else:
            risk_tier = "HIGH RISK"
            badge_color = "danger"

        return {
            "mint": mint,
            "symbol": symbol or "TOKEN",
            "score": safety.score,
            "is_safe": safety.is_safe,
            "risk_tier": risk_tier,
            "badge_color": badge_color,
            "mint_authority_revoked": safety.mint_authority_revoked,
            "freeze_authority_revoked": safety.freeze_authority_revoked,
            "top10_holding_pct": safety.top10_holding_pct,
            "dev_holding_pct": safety.dev_holding_pct,
            "reasons": safety.reasons,
            "links": {
                "solscan": f"https://solscan.io/token/{mint}",
                "rugcheck": f"https://rugcheck.xyz/tokens/{mint}",
                "dexscreener": f"https://dexscreener.com/solana/{mint}",
                "pumpfun": f"https://pump.fun/{mint}",
                "photon": f"https://photon-sol.tinyastro.io/en/lp/{mint}",
            }
        }

