import os
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from backend.models.order import BotMode

load_dotenv()


class BotConfig(BaseModel):
    # Core Operation
    mode: BotMode = Field(
        default_factory=lambda: BotMode(os.getenv("BOT_MODE", "paper").lower())
    )
    is_running: bool = True
    auto_snipe: bool = True
    max_daily_loss_sol: float = 1.0
    
    # RPC & Network
    solana_rpc_url: str = Field(
        default_factory=lambda: os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    )
    solana_ws_url: str = Field(
        default_factory=lambda: os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com")
    )
    solana_private_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("SOLANA_PRIVATE_KEY") or None
    )
    
    # Trade Execution
    buy_amount_sol: float = Field(
        default_factory=lambda: float(os.getenv("DEFAULT_BUY_AMOUNT_SOL") or os.getenv("BUY_AMOUNT_SOL") or 0.05)
    )
    max_positions: int = Field(
        default_factory=lambda: int(os.getenv("MAX_POSITIONS") or 10)
    )
    slippage_bps: int = Field(default=500, ge=50, le=5000)
    priority_fee_microlamports: int = Field(default=250000, ge=0)
    priority_fee_level: str = "fast"
    use_jito: bool = Field(
        default_factory=lambda: os.getenv("USE_JITO", "true").lower() in ("true", "1")
    )
    jito_tip_sol: float = 0.001
    enable_dynamic_jito_tip: bool = True
    max_jito_tip_sol: float = 0.01
    
    # Profit & Risk Targets
    take_profit_pct: float = Field(default=50.0, ge=1.0)
    stop_loss_pct: float = Field(default=20.0, ge=1.0, le=99.0)
    trailing_stop_pct: float = Field(default=15.0, ge=1.0)
    enable_scale_out: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_SCALE_OUT", "false").lower() in ("true", "1")
    )
    enable_breakeven_lock: bool = True
    breakeven_trigger_pct: float = Field(default=20.0, ge=5.0)
    max_hold_time_minutes: int = Field(default=60, ge=1)
    
    # Anti-Rug Filters
    min_liquidity_usd: float = 1000.0
    max_dev_buy_pct: float = 10.0
    require_mint_revoked: bool = True
    require_freeze_revoked: bool = True
    min_safety_score: int = Field(
        default_factory=lambda: int(os.getenv("MIN_SAFETY_SCORE") or 75)
    )
    min_holders: int = Field(
        default_factory=lambda: int(os.getenv("MIN_HOLDERS") or 10)
    )
    max_top10_holding_pct: float = Field(
        default_factory=lambda: float(os.getenv("MAX_TOP10_HOLDING_PCT") or 30.0)
    )
    require_socials: bool = Field(
        default_factory=lambda: os.getenv("REQUIRE_SOCIALS", "true").lower() in ("true", "1")
    )
    
    # Launchpad Feeds
    enable_pumpfun: bool = True
    enable_raydium: bool = True
    enable_meteora: bool = True
    
    # Simulated initial paper wallet
    paper_wallet_sol: float = 10.0
    
    # Notifications (Optional Webhooks)
    discord_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    
    # Custom Blacklists
    blacklist_keywords: list[str] = Field(default_factory=lambda: ["scam", "rug", "test", "fake", "honey"])
    blacklist_creators: list[str] = Field(default_factory=list)


