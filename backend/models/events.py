from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import time


class EventType(str, Enum):
    TOKEN_DISCOVERED = "token_discovered"
    SNIPE_TRIGGERED = "snipe_triggered"
    ORDER_EXECUTED = "order_executed"
    POSITION_UPDATED = "position_updated"
    POSITION_CLOSED = "position_closed"
    BOT_STATE_CHANGED = "bot_state_changed"
    ERROR = "error"
    LOG = "log"


class BotEvent(BaseModel):
    event: EventType
    timestamp: float = Field(default_factory=time.time)
    data: Dict[str, Any]
    message: Optional[str] = None
