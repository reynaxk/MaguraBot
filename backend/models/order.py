from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import time
import uuid


class BotMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderType(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    PARTIAL_TAKE_PROFIT = "partial_tp"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIMEOUT = "timeout"
    MANUAL = "manual"
    EMERGENCY = "emergency"


class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    token_mint: str
    symbol: str
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    mode: BotMode = BotMode.PAPER
    
    amount_sol: float
    amount_tokens: float = 0.0
    price_sol: float = 0.0
    price_usd: float = 0.0
    slippage_bps: int = 500
    
    tx_hash: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None


class Position(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    token_mint: str
    symbol: str
    name: str
    mode: BotMode = BotMode.PAPER
    status: PositionStatus = PositionStatus.OPEN
    
    # Entry details
    buy_order_id: str
    buy_tx_hash: Optional[str] = None
    buy_price_sol: float
    buy_price_usd: float
    sol_invested: float
    tokens_held: float
    opened_at: float = Field(default_factory=time.time)
    
    # Live tracking details
    current_price_sol: float
    current_price_usd: float
    peak_price_sol: float
    pnl_sol: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    
    # Targets
    take_profit_price: float
    stop_loss_price: float
    trailing_stop_active: bool = False
    scale_out_triggered: bool = False
    initial_tokens_held: float = 0.0
    realized_pnl_sol: float = 0.0
    
    # Exit details
    sell_order_id: Optional[str] = None
    sell_tx_hash: Optional[str] = None
    exit_reason: Optional[ExitReason] = None
    closed_at: Optional[float] = None
