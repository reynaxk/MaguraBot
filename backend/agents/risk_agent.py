import logging
from typing import Tuple
from backend.models.config import BotConfig
from backend.models.token import TokenData
from backend.services.security import SecurityService

logger = logging.getLogger("MaguraBot.RiskAgent")


class RiskAgent:
    def __init__(self, config: BotConfig, security_service: SecurityService):
        self.config = config
        self.security = security_service

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

        logger.info(f"Token {token.symbol} [{token.mint[:8]}...] PASSED risk evaluation (Score: {safety.score})")
        return True, "Passed all safety checks"

