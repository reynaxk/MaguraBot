from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import time


class LaunchpadType(str, Enum):
    PUMPFUN = "pumpfun"
    RAYDIUM = "raydium"
    JUPITER = "jupiter"


class TokenSafetyScore(BaseModel):
    score: int = Field(default=100, ge=0, le=100)
    is_safe: bool = True
    mint_authority_revoked: bool = True
    freeze_authority_revoked: bool = True
    dev_holding_pct: float = 0.0
    top10_holding_pct: float = 0.0
    reasons: List[str] = Field(default_factory=list)


class TokenData(BaseModel):
    mint: str
    symbol: str
    name: str
    source: LaunchpadType
    creator: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    
    # Financial metrics
    price_sol: float = 0.0
    price_usd: float = 0.0
    market_cap_sol: Optional[float] = None
    market_cap_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    
    # Bonding curve metrics (Pump.fun)
    v_sol_in_bonding_curve: Optional[float] = None
    bonding_curve_pct: float = 0.0
    is_migrated: bool = False
    
    # Risk and safety analysis
    safety: TokenSafetyScore = Field(default_factory=TokenSafetyScore)
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)
