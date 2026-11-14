import logging
from typing import Tuple, Optional
from backend.models.config import BotConfig
from backend.models.token import TokenData
from backend.services.security import SecurityService
from backend.services.holders import HoldersScanner

logger = logging.getLogger("MaguraBot.RiskAgent")


class RiskAgent:
    def __init__(
        self,
        config: BotConfig,
        security_service: SecurityService,
        holders_scanner: Optional[HoldersScanner] = None,
    ):
        self.config = config
        self.security = security_service
        self.holders_scanner = holders_scanner

    async def evaluate(self, token: TokenData) -> Tuple[bool, str]:
        """Perform sub-second multi-factor rug check on incoming token."""
        # 1. Evaluate on-chain and metadata safety if not already evaluated
        if token.safety.score == 100 and not token.safety.reasons and token.safety.freeze_authority_revoked:
            safety = await self.security.evaluate_token(
                mint=token.mint,
                dev_wallet=token.creator,
                raw_metadata=token.raw_metadata,
            )
            token.safety = safety
        else:
            safety = token.safety

        # 2. Check mint authority
        if self.config.require_mint_revoked and not safety.mint_authority_revoked:
            return False, "Rejected: Mint authority active (risk of mint rug)"

        # 3. Check freeze authority
        if self.config.require_freeze_revoked and not safety.freeze_authority_revoked:
            return False, "Rejected: Freeze authority active (risk of honeypot)"

        # 4. Check safety score threshold
        if safety.score < self.config.min_safety_score:
            reasons_str = ", ".join(safety.reasons) if safety.reasons else "Score below threshold"
            return False, f"Rejected: Safety score {safety.score}/{self.config.min_safety_score} ({reasons_str})"

        # 5. Check bonding curve progress
        if token.source == "pumpfun":
            # Avoid bonding curves that have already peaked or are dumping
            if token.bonding_curve_pct > 85.0:
                return False, f"Rejected: Bonding curve too high ({token.bonding_curve_pct:.1f}%), close to migration dump"

        # 6. Check custom blacklist keywords (symbol & name)
        symbol_lower = (token.symbol or "").lower()
        name_lower = (token.name or "").lower()
        for kw in self.config.blacklist_keywords:
            if kw and (kw.lower() in symbol_lower or kw.lower() in name_lower):
                return False, f"Rejected: Matches blacklisted keyword '{kw}'"

        # 7. Check creator blacklist
        if token.creator and token.creator in self.config.blacklist_creators:
            return False, f"Rejected: Creator {token.creator[:8]} is blacklisted"

        # 8. Check social presence (anti-throwaway token)
        if getattr(self.config, "require_socials", True):
            has_socials = False
            raw = token.raw_metadata or {}
            # Check direct social keys from PumpPortal or DexScreener
            if any(raw.get(k) for k in ("twitter", "telegram", "website")):
                has_socials = True
            elif raw.get("links") and len(raw["links"]) > 0:
                has_socials = True
            elif raw.get("info", {}).get("socials") or raw.get("info", {}).get("websites"):
                has_socials = True

            if not has_socials:
                return False, "Rejected: No verified socials (Twitter/Telegram/Website). High throwaway dump risk."

        # 9. Check liquidity parameters if available
        if token.liquidity_usd is not None:
            min_liq = getattr(self.config, "min_liquidity_usd", 0.0)
            if min_liq > 0 and token.liquidity_usd < min_liq:
                return False, f"Rejected: Liquidity ${token.liquidity_usd:,.0f} below min ${min_liq:,.0f}"
            max_liq = getattr(self.config, "max_liquidity_usd", None)
            if max_liq and token.liquidity_usd > max_liq:
                return False, f"Rejected: Liquidity ${token.liquidity_usd:,.0f} exceeds max limit ${max_liq:,.0f}"

        # 10. Check volume parameters if available
        min_vol = getattr(self.config, "min_volume_usd", None)
        if min_vol and token.volume_24h_usd is not None:
            if token.volume_24h_usd < min_vol:
                return False, f"Rejected: 24h Volume ${token.volume_24h_usd:,.0f} below min ${min_vol:,.0f}"

        # 11. Check token timeframe / age
        max_age = getattr(self.config, "max_age_minutes", None)
        if max_age and token.created_at:
            import time
            age_mins = (time.time() - token.created_at) / 60.0
            if age_mins > max_age:
                return False, f"Rejected: Token age {age_mins:.1f}m exceeds timeframe limit ({max_age}m)"

        # 12. Check on-chain holder distribution
        if self.holders_scanner and getattr(self.config, "min_holders", 0) > 0:
            holders_info = await self.holders_scanner.analyze_holders(token.mint)
            if holders_info.get("status") == "success":
                sampled = holders_info.get("holders_sampled", 0)
                if sampled < self.config.min_holders:
                    return False, f"Rejected: Only {sampled} holders detected (minimum {self.config.min_holders} required)"

                top10_pct = holders_info.get("top10_holding_pct", 0.0)
                max_top10 = getattr(self.config, "max_top10_holding_pct", 30.0)
                if top10_pct > max_top10:
                    return False, f"Rejected: Top holders control {top10_pct:.1f}% of supply (exceeds max {max_top10}%)"

        logger.info(f"Token {token.symbol} [{token.mint[:8]}...] PASSED risk evaluation (Score: {safety.score})")
        return True, "Passed all safety checks"

