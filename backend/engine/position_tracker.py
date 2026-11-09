import asyncio
import logging
import random
import time
from typing import Callable, Dict, Optional
from backend.models.config import BotConfig
from backend.models.order import Position, PositionStatus, ExitReason

logger = logging.getLogger("MaguraBot.PositionTracker")


class PositionTracker:
    def __init__(
        self,
        config: BotConfig,
        on_exit_triggered: Callable[[Position, ExitReason], asyncio.Future],
    ):
        self.config = config
        self.on_exit_triggered = on_exit_triggered
        self.positions: Dict[str, Position] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def add_position(self, position: Position):
        self.positions[position.id] = position
        logger.info(
            f"Tracking new position {position.symbol} [{position.id}]: "
            f"Entry {position.buy_price_sol:.9f} SOL, TP: +{self.config.take_profit_pct}%, SL: -{self.config.stop_loss_pct}%"
        )

    def remove_position(self, position_id: str) -> Optional[Position]:
        return self.positions.pop(position_id, None)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("PositionTracker started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PositionTracker stopped")

    async def _monitor_loop(self):
        while self._running:
            try:
                open_positions = [p for p in list(self.positions.values()) if p.status == PositionStatus.OPEN]
                for p in open_positions:
                    await self._evaluate_position(p)
            except Exception as e:
                logger.error(f"Error in position tracker loop: {e}")

            await asyncio.sleep(2)

    async def _evaluate_position(self, p: Position):
        # 1. Update current price
        # In paper mode, simulate active market volatility
        # (with realistic upward/downward drift to test TP & SL triggers)
        drift = random.uniform(-0.04, 0.055)
        new_price = max(p.buy_price_sol * 0.05, p.current_price_sol * (1.0 + drift))
        p.current_price_sol = new_price
        p.current_price_usd = new_price * 180.0

        if new_price > p.peak_price_sol:
            p.peak_price_sol = new_price

        # 2. Calculate PnL
        current_value_sol = p.tokens_held * new_price
        p.pnl_sol = current_value_sol - p.sol_invested
        p.pnl_pct = (p.pnl_sol / p.sol_invested) * 100.0 if p.sol_invested > 0 else 0.0
        p.pnl_usd = p.pnl_sol * 180.0

        # 3. Check Take Profit
        if p.pnl_pct >= self.config.take_profit_pct:
            logger.info(f"Target Take Profit reached for {p.symbol}: +{p.pnl_pct:.1f}%")
            await self.on_exit_triggered(p, ExitReason.TAKE_PROFIT)
            return

        # 4. Check Stop Loss
        if p.pnl_pct <= -self.config.stop_loss_pct:
            logger.info(f"Stop Loss triggered for {p.symbol}: {p.pnl_pct:.1f}%")
            await self.on_exit_triggered(p, ExitReason.STOP_LOSS)
            return

        # 5. Check Trailing Stop
        # If profit peaked above 20% and dropped by trailing_stop_pct from the peak
        peak_gain_pct = ((p.peak_price_sol - p.buy_price_sol) / p.buy_price_sol) * 100.0
        if peak_gain_pct >= 20.0:
            drop_from_peak_pct = ((p.peak_price_sol - new_price) / p.peak_price_sol) * 100.0
            if drop_from_peak_pct >= self.config.trailing_stop_pct:
                logger.info(
                    f"Trailing Stop triggered for {p.symbol}: dropped {drop_from_peak_pct:.1f}% from peak"
                )
                await self.on_exit_triggered(p, ExitReason.TRAILING_STOP)
                return

        # 6. Check Max Hold Time
        elapsed_minutes = (time.time() - p.opened_at) / 60.0
        if elapsed_minutes >= self.config.max_hold_time_minutes:
            logger.info(f"Max hold time exceeded ({elapsed_minutes:.1f}m) for {p.symbol}")
            await self.on_exit_triggered(p, ExitReason.TIMEOUT)
            return
