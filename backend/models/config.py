from typing import Optional
from pydantic import BaseModel, Field
from backend.models.order import BotMode


class BotConfig(BaseModel):
    # Core Operation
    mode: BotMode = BotMode.PAPER
    is_running: bool = True
    auto_snipe: bool = True
    max_daily_loss_sol: float = 1.0
    
    # RPC & Network
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_ws_url: str = "wss://api.mainnet-beta.solana.com"
    solana_private_key: Optional[str] = None
    
    # Trade Execution
    buy_amount_sol: float = Field(default=0.05, gt=0)
    max_positions: int = Field(default=5, ge=1, le=20)
    slippage_bps: int = Field(default=500, ge=50, le=5000)
    priority_fee_microlamports: int = Field(default=100000, ge=0)
    use_jito: bool = False
    jito_tip_sol: float = 0.001
    
    # Profit & Risk Targets
    take_profit_pct: float = Field(default=50.0, ge=1.0)
    stop_loss_pct: float = Field(default=20.0, ge=1.0, le=99.0)
    trailing_stop_pct: float = Field(default=15.0, ge=1.0)
    max_hold_time_minutes: int = Field(default=60, ge=1)
    
    # Anti-Rug Filters
    min_liquidity_usd: float = 1000.0
    max_dev_buy_pct: float = 10.0
    require_mint_revoked: bool = True
    require_freeze_revoked: bool = True
    min_safety_score: int = Field(default=70, ge=0, le=100)
    
    # Launchpad Feeds
    enable_pumpfun: bool = True
    enable_raydium: bool = True
    
    # Simulated initial paper wallet
    paper_wallet_sol: float = 10.0
    
    # Notifications (Optional Webhooks)
    discord_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    
    # Custom Blacklists
    blacklist_keywords: list[str] = Field(default_factory=lambda: ["scam", "rug", "test", "fake", "honey"])
    blacklist_creators: list[str] = Field(default_factory=list)


