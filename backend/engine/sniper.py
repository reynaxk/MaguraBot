import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional
from backend.models.config import BotConfig
from backend.models.events import BotEvent, EventType
from backend.models.order import (
    BotMode,
    Position,
    PositionStatus,
    ExitReason,
    Order,
)
from backend.models.token import TokenData
from backend.services.solana_client import SolanaClient
from backend.services.security import SecurityService
from backend.services.jupiter import JupiterService
from backend.services.alerts import AlertService
from backend.engine.executor import TradeExecutor
from backend.engine.position_tracker import PositionTracker
from backend.agents.discovery_agent import DiscoveryAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.trade_agent import TradeAgent

logger = logging.getLogger("MaguraBot.SniperEngine")


class SniperEngine:
    def __init__(self, config: Optional[BotConfig] = None):
        self.config = config or BotConfig()
        
        # Core services
        self.solana_client = SolanaClient(self.config.solana_rpc_url)
        self.security = SecurityService(self.solana_client)
        self.jupiter = JupiterService()
        self.alerts = AlertService(
            telegram_bot_token=self.config.telegram_bot_token,
            telegram_chat_id=self.config.telegram_chat_id,
            discord_webhook_url=self.config.discord_webhook_url,
        )
        self.executor = TradeExecutor(self.config, self.solana_client, self.jupiter)
        
        # Position Tracker
        self.tracker = PositionTracker(self.config, self._handle_auto_exit)
        
        # Agents
        self.discovery = DiscoveryAgent(self.config, self.handle_token_discovery)
        self.risk_agent = RiskAgent(self.config, self.security)
        self.trade_agent = TradeAgent(self.config)
        
        # State stores
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[Position] = []
        self.recent_tokens: List[TokenData] = []
        self.subscribers: List[Callable[[BotEvent], None]] = []
        
        self.is_running = False

    def subscribe(self, listener: Callable[[BotEvent], None]):
        self.subscribers.append(listener)

    def unsubscribe(self, listener: Callable[[BotEvent], None]):
        if listener in self.subscribers:
            self.subscribers.remove(listener)

    def broadcast(self, event_type: EventType, data: dict, message: Optional[str] = None):
        event = BotEvent(event=event_type, data=data, message=message)
        for sub in list(self.subscribers):
            try:
                sub(event)
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.config.is_running = True
        
        await self.tracker.start()
        await self.discovery.start()
        
        self.broadcast(
            EventType.BOT_STATE_CHANGED,
            {"running": True, "mode": self.config.mode.value},
            "Sniper Bot successfully started",
        )
        logger.info(f"SniperEngine started in {self.config.mode.value.upper()} mode")

    async def stop(self):
        self.is_running = False
        self.config.is_running = False
        
        await self.discovery.stop()
        await self.tracker.stop()
        await self.solana_client.close()
        await self.security.close()
        await self.jupiter.close()
        await self.alerts.close()
        
        self.broadcast(
            EventType.BOT_STATE_CHANGED,
            {"running": False, "mode": self.config.mode.value},
            "Sniper Bot stopped",
        )
        logger.info("SniperEngine stopped")

    def handle_token_discovery(self, token: TokenData):
        """Asynchronously enqueue newly discovered token for evaluation."""
        # Keep rolling buffer of recent tokens
        self.recent_tokens.insert(0, token)
        if len(self.recent_tokens) > 60:
            self.recent_tokens.pop()

        self.broadcast(EventType.TOKEN_DISCOVERED, token.model_dump())
        asyncio.create_task(self._process_token(token))

    async def _process_token(self, token: TokenData):
        # 1. Check trading limits and cooldown
        open_count = len([p for p in self.positions.values() if p.status == PositionStatus.OPEN])
        can_trade, reason = self.trade_agent.can_snipe(token, open_count)
        if not can_trade:
            logger.debug(f"Skipping {token.symbol}: {reason}")
            return

        # 2. Multi-factor risk screening
        passed, risk_reason = await self.risk_agent.evaluate(token)
        if not passed:
            logger.info(f"Rug Filter [{token.symbol}]: {risk_reason}")
            return

        # 3. Trigger Snipe!
        self.trade_agent.record_snipe(token.mint)
        self.broadcast(
            EventType.SNIPE_TRIGGERED,
            {"mint": token.mint, "symbol": token.symbol, "amount_sol": self.config.buy_amount_sol},
            f"Sniping {token.symbol} for {self.config.buy_amount_sol} SOL",
        )
        await self._execute_snipe(token, self.config.buy_amount_sol)

    async def _execute_snipe(self, token: TokenData, amount_sol: float):
        order, err = await self.executor.execute_buy(token, amount_sol)
        if err or not order or order.status != "filled":
            logger.error(f"Snipe failed for {token.symbol}: {err}")
            self.broadcast(EventType.ERROR, {"error": err, "token": token.symbol})
            return

        # Create tracked position
        pos = Position(
            token_mint=token.mint,
            symbol=token.symbol,
            name=token.name,
            mode=self.config.mode,
            status=PositionStatus.OPEN,
            buy_order_id=order.id,
            buy_tx_hash=order.tx_hash,
            buy_price_sol=order.price_sol,
            buy_price_usd=order.price_usd,
            sol_invested=order.amount_sol,
            tokens_held=order.amount_tokens,
            current_price_sol=order.price_sol,
            current_price_usd=order.price_usd,
            peak_price_sol=order.price_sol,
            take_profit_price=order.price_sol * (1.0 + self.config.take_profit_pct / 100.0),
            stop_loss_price=order.price_sol * (1.0 - self.config.stop_loss_pct / 100.0),
        )

        self.positions[pos.id] = pos
        self.tracker.add_position(pos)
        self.broadcast(EventType.ORDER_EXECUTED, order.model_dump())
        self.broadcast(EventType.POSITION_UPDATED, pos.model_dump())
        asyncio.create_task(
            self.alerts.send_snipe_alert(
                pos.symbol, pos.token_mint, pos.sol_invested, self.config.mode.value
            )
        )

    async def _handle_auto_exit(self, position: Position, reason: ExitReason):
        logger.info(f"Auto-exit triggered for {position.symbol}: {reason.value}")
        await self.close_position(position.id, reason)

    async def close_position(self, position_id: str, reason: ExitReason = ExitReason.MANUAL) -> bool:
        pos = self.positions.get(position_id)
        if not pos or pos.status != PositionStatus.OPEN:
            return False

        # Mark closed and remove from active tracker immediately to prevent race conditions
        pos.status = PositionStatus.CLOSED
        pos.exit_reason = reason
        pos.closed_at = time.time()
        self.tracker.remove_position(position_id)

        order, err = await self.executor.execute_sell(
            token_mint=pos.token_mint,
            symbol=pos.symbol,
            amount_tokens=pos.tokens_held,
            current_price_sol=pos.current_price_sol,
        )

        if order:
            pos.sell_order_id = order.id
            pos.sell_tx_hash = order.tx_hash

        # Update final PnL
        current_value_sol = pos.tokens_held * pos.current_price_sol
        pos.pnl_sol = current_value_sol - pos.sol_invested
        pos.pnl_pct = (pos.pnl_sol / pos.sol_invested) * 100.0 if pos.sol_invested > 0 else 0.0
        pos.pnl_usd = pos.pnl_sol * 180.0

        self.closed_trades.insert(0, pos)
        if len(self.closed_trades) > 100:
            self.closed_trades.pop()

        self.broadcast(
            EventType.POSITION_CLOSED,
            pos.model_dump(),
            f"Closed {pos.symbol} ({reason.value}): PnL {pos.pnl_pct:+.1f}% ({pos.pnl_sol:+.4f} SOL)",
        )
        asyncio.create_task(
            self.alerts.send_exit_alert(
                pos.symbol, pos.pnl_pct, pos.pnl_sol, reason.value, self.config.mode.value
            )
        )
        return True

    async def manual_snipe(self, mint: str, symbol: str = "MANUAL", amount_sol: Optional[float] = None):
        amount = amount_sol or self.config.buy_amount_sol
        token = TokenData(
            mint=mint,
            symbol=symbol,
            name=f"Manual Snipe {symbol}",
            source="jupiter",
            price_sol=0.0000001,
        )
        await self._execute_snipe(token, amount)

    async def emergency_sell_all(self):
        open_ids = [p.id for p in self.positions.values() if p.status == PositionStatus.OPEN]
        for pid in open_ids:
            await self.close_position(pid, ExitReason.EMERGENCY)

    def get_metrics(self) -> dict:
        open_pos = [p for p in self.positions.values() if p.status == PositionStatus.OPEN]
        wins = [p for p in self.closed_trades if p.pnl_sol > 0]
        total_pnl_sol = sum(p.pnl_sol for p in self.closed_trades)
        total_pnl_usd = total_pnl_sol * 180.0
        win_rate = (len(wins) / len(self.closed_trades) * 100.0) if self.closed_trades else 0.0

        return {
            "mode": self.config.mode.value,
            "is_running": self.is_running,
            "open_positions_count": len(open_pos),
            "total_trades": len(self.closed_trades),
            "win_rate_pct": round(win_rate, 1),
            "total_pnl_sol": round(total_pnl_sol, 4),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "paper_balance_sol": round(self.executor.paper_balance_sol, 4),
            "sol_price_usd": 180.0,
        }
