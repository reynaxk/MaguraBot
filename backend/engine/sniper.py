import asyncio
import logging
import os
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
    OrderStatus,
)
from backend.models.token import TokenData
from backend.services.solana_client import SolanaClient
from backend.services.security import SecurityService
from backend.services.jupiter import JupiterService
from backend.services.alerts import AlertService
from backend.services.jito import JitoService
from backend.services.telegram_bot import TelegramBotController
from backend.db.storage import StorageService
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
        self.jito = JitoService()
        self.storage = StorageService()
        self.alerts = AlertService(
            telegram_bot_token=self.config.telegram_bot_token,
            telegram_chat_id=self.config.telegram_chat_id,
            discord_webhook_url=self.config.discord_webhook_url,
        )
        self.tg_controller = TelegramBotController(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
            engine=self,
        )
        self.executor = TradeExecutor(self.config, self.solana_client, self.jupiter, self.jito)
        
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
        
        # Lifetime metrics & concurrency controls
        self.lifetime_trades_count: int = 0
        self.lifetime_pnl_sol: float = 0.0
        self.lifetime_wins: int = 0
        self._snipe_lock = asyncio.Lock()
        self._in_flight_snipes: int = 0
        self.sol_price_usd: float = float(os.getenv("SOL_PRICE_USD", "81.05"))
        
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
        
        # Load historical lifetime metrics from SQLite
        try:
            stats = await self.storage.get_trade_stats()
            self.lifetime_trades_count = stats.get("total_trades", 0)
            self.lifetime_pnl_sol = stats.get("total_pnl_sol", 0.0)
            self.lifetime_wins = stats.get("wins", 0)
            logger.info(f"Loaded lifetime history: {self.lifetime_trades_count} trades, {self.lifetime_pnl_sol:+.4f} SOL PnL")
        except Exception as e:
            logger.debug(f"Could not load initial trade stats: {e}")
        
        await self.tracker.start()
        await self.discovery.start()
        await self.tg_controller.start()
        
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
        await self.tg_controller.stop()
        await self.solana_client.close()
        await self.security.close()
        await self.jupiter.close()
        await self.jito.close()
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
        # 0. Check Auto-Snipe toggle
        if not self.config.auto_snipe:
            logger.debug(f"Auto-snipe is paused. {token.symbol} will only be monitored on radar.")
            return

        # 1. Atomic reservation for position limits to prevent race condition slot overflows
        async with self._snipe_lock:
            open_count = len([p for p in self.positions.values() if p.status == PositionStatus.OPEN])
            if open_count + self._in_flight_snipes >= self.config.max_positions:
                logger.debug(
                    f"Max concurrent positions reached ({open_count} open + {self._in_flight_snipes} pending >= {self.config.max_positions}). "
                    f"Skipping {token.symbol}."
                )
                return

            can_trade, reason = self.trade_agent.can_snipe(token, open_count + self._in_flight_snipes)
            if not can_trade:
                logger.debug(f"Skipping {token.symbol}: {reason}")
                return

            self._in_flight_snipes += 1

        try:
            # 2. Check daily loss circuit breaker against lifetime session PnL
            if self.lifetime_pnl_sol < -abs(self.config.max_daily_loss_sol):
                logger.warning(
                    f"Circuit Breaker Triggered: Total realized loss ({self.lifetime_pnl_sol:.4f} SOL) "
                    f"exceeds max daily loss ({self.config.max_daily_loss_sol} SOL). Auto-sniping suspended."
                )
                return

            # 3. Multi-factor risk screening
            passed, risk_reason = await self.risk_agent.evaluate(token)
            if not passed:
                logger.info(f"Rug Filter [{token.symbol}]: {risk_reason}")
                return

            # 4. Trigger Snipe!
            self.trade_agent.record_snipe(token.mint)
            self.broadcast(
                EventType.SNIPE_TRIGGERED,
                {"mint": token.mint, "symbol": token.symbol, "amount_sol": self.config.buy_amount_sol},
                f"Sniping {token.symbol} for {self.config.buy_amount_sol} SOL",
            )
            await self._execute_snipe(token, self.config.buy_amount_sol)
        finally:
            async with self._snipe_lock:
                self._in_flight_snipes = max(0, self._in_flight_snipes - 1)

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
        asyncio.create_task(self.storage.save_order(order.model_dump()))
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

        # Handle Scale-Out Partial Take Profit (Sell 50% to lock profit, leave moonbag)
        if reason == ExitReason.PARTIAL_TAKE_PROFIT:
            tokens_to_sell = pos.tokens_held * 0.5
            order, err = await self.executor.execute_sell(
                token_mint=pos.token_mint,
                symbol=pos.symbol,
                amount_tokens=tokens_to_sell,
                current_price_sol=pos.current_price_sol,
            )
            if order and order.status == OrderStatus.FILLED:
                pos.tokens_held -= tokens_to_sell
                sol_earned = order.amount_sol
                cost_sold = pos.sol_invested * 0.5
                realized_gain = sol_earned - cost_sold
                pos.realized_pnl_sol += realized_gain
                pos.sol_invested -= cost_sold
                pos.take_profit_price = pos.take_profit_price * 1.5
                pos.trailing_stop_active = True

                self.broadcast(
                    EventType.POSITION_UPDATED,
                    pos.model_dump(),
                    f"Scale-out TP1 on {pos.symbol}: secured 50% profit (+{realized_gain:.4f} SOL). Moonbag running!",
                )
                asyncio.create_task(
                    self.alerts.send_exit_alert(
                        pos.symbol, pos.pnl_pct, realized_gain, "Scale-Out (50%)", self.config.mode.value
                    )
                )
                return True
            return False

        # Full Close: Mark closed and remove from active tracker immediately to prevent race conditions
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

        # Update final PnL: include both final leg and any prior scale-out gains
        current_value_sol = pos.tokens_held * pos.current_price_sol
        final_leg_pnl = current_value_sol - pos.sol_invested
        total_trade_pnl = final_leg_pnl + getattr(pos, "realized_pnl_sol", 0.0)
        pos.pnl_sol = total_trade_pnl
        total_cost = pos.sol_invested + (pos.sol_invested if pos.scale_out_triggered else 0.0)
        pos.pnl_pct = (total_trade_pnl / total_cost) * 100.0 if total_cost > 0 else 0.0
        pos.pnl_usd = pos.pnl_sol * self.sol_price_usd

        self.closed_trades.insert(0, pos)
        if len(self.closed_trades) > 2000:
            self.closed_trades.pop()

        self.lifetime_trades_count += 1
        self.lifetime_pnl_sol += total_trade_pnl
        if total_trade_pnl > 0:
            self.lifetime_wins += 1

        asyncio.create_task(self.storage.save_trade(pos.model_dump()))

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
        total_trades = max(self.lifetime_trades_count, len(self.closed_trades))
        total_pnl_sol = self.lifetime_pnl_sol if self.lifetime_trades_count > 0 else sum(p.pnl_sol for p in self.closed_trades)
        wins_count = self.lifetime_wins if self.lifetime_trades_count > 0 else len([p for p in self.closed_trades if p.pnl_sol > 0])
        win_rate = (wins_count / total_trades * 100.0) if total_trades > 0 else 0.0

        return {
            "mode": self.config.mode.value,
            "is_running": self.is_running,
            "auto_snipe": self.config.auto_snipe,
            "use_jito": self.config.use_jito,
            "open_positions_count": len(open_pos),
            "max_positions": self.config.max_positions,
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "total_pnl_sol": round(total_pnl_sol, 4),
            "total_pnl_usd": round(total_pnl_sol * self.sol_price_usd, 2),
            "paper_balance_sol": round(self.executor.paper_balance_sol, 4),
            "sol_price_usd": self.sol_price_usd,
        }
