import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("MaguraBot.PumpCurve")

# Pump.fun constant parameters
INITIAL_VIRTUAL_SOL_RESERVES = 30.0  # 30 SOL
INITIAL_VIRTUAL_TOKEN_RESERVES = 1_073_000_000.0  # 1.073 Billion tokens
GRADUATION_TARGET_SOL = 85.0  # Target real SOL to trigger Raydium migration


class PumpCurveCalculator:
    """
    Mathematical model for Pump.fun constant product bonding curve:
    k = virtual_sol * virtual_tokens
    """

    @staticmethod
    def calculate_bonding_progress(real_sol_deposited: float) -> Dict[str, Any]:
        """
        Calculates exact bonding curve progress percentage and Raydium graduation probability.
        """
        clamped_sol = max(0.0, real_sol_deposited)
        progress_pct = min(100.0, (clamped_sol / GRADUATION_TARGET_SOL) * 100.0)
        
        virtual_sol = INITIAL_VIRTUAL_SOL_RESERVES + clamped_sol
        k = INITIAL_VIRTUAL_SOL_RESERVES * INITIAL_VIRTUAL_TOKEN_RESERVES
        virtual_tokens = k / virtual_sol if virtual_sol > 0 else INITIAL_VIRTUAL_TOKEN_RESERVES
        tokens_remaining = max(0.0, virtual_tokens - (INITIAL_VIRTUAL_TOKEN_RESERVES - 800_000_000.0))
        
        # Spot price in SOL per token
        spot_price_sol = (virtual_sol / virtual_tokens) if virtual_tokens > 0 else 0.0

        is_graduating = progress_pct >= 95.0
        is_migrated = progress_pct >= 100.0

        return {
            "real_sol_deposited": round(clamped_sol, 4),
            "graduation_target_sol": GRADUATION_TARGET_SOL,
            "progress_pct": round(progress_pct, 2),
            "spot_price_sol": spot_price_sol,
            "tokens_remaining": round(tokens_remaining, 0),
            "is_graduating": is_graduating,
            "is_migrated": is_migrated,
            "sol_needed_to_graduate": max(0.0, round(GRADUATION_TARGET_SOL - clamped_sol, 4)),
        }

    @staticmethod
    def estimate_tokens_out(real_sol_deposited: float, buy_sol: float) -> float:
        """
        Estimates the exact amount of tokens received for a given buy amount in SOL.
        Δy = virtual_tokens - (k / (virtual_sol + Δx))
        """
        if buy_sol <= 0:
            return 0.0
        v_sol = INITIAL_VIRTUAL_SOL_RESERVES + real_sol_deposited
        k = INITIAL_VIRTUAL_SOL_RESERVES * INITIAL_VIRTUAL_TOKEN_RESERVES
        v_tokens = k / v_sol
        new_v_sol = v_sol + buy_sol
        new_v_tokens = k / new_v_sol
        tokens_out = max(0.0, v_tokens - new_v_tokens)
        return tokens_out
