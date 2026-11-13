import asyncio
import logging
import os
import random
import time
from typing import Callable, Dict, Optional
import aiohttp
from backend.models.config import BotConfig
from backend.models.order import BotMode, Position, PositionStatus, ExitReason
from backend.services.jupiter import WSOL_MINT, JupiterService

logger = logging.getLogger("MaguraBot.PositionTracker")


class PositionTracker:
    def __init__(
        self,
        config: BotConfig,
        on_exit_triggered: Callable[[Position, ExitReason], asyncio.Future],
        jupiter: Optional[JupiterService] = None,
    ):
        self.config = config
        self.on_exit_triggered = on_exit_triggered
        self.jupiter = jupiter
        self.positions: Dict[str, Position] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    def add_position(self, position: Position):
        if not position.initial_tokens_held:
            position.initial_tokens_held = position.tokens_held
        self.positions[position.id] = position
        logger.info(
            f"Tracking new position {position.symbol} [{position.id}]: "
            f"Entry {position.buy_price_sol:.9f} SOL, TP: +{self.config.take_profit_pct}%, SL: -{self.config.stop_loss_pct}%"
        )

    def remove_position(self, position_id: str) -> Optional[Position]:
        return self.positions.pop(position_id, None)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4))
        return self._session

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
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("PositionTracker stopped")

    async def _fetch_live_price(self, p: Position) -> Optional[float]:
        """Fetch true live market price via DexScreener API or Jupiter Quote."""
        # 1. Query DexScreener token pairs
        try:
            session = await self._get_session()
            url = f"https://api.dexscreener.com/latest/dex/tokens/{p.token_mint}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs") or []
                    if pairs:
                        price_native = pairs[0].get("priceNative")
                        if price_native:
                            val = float(price_native)
                            if val > 0:
                                return val
        except Exception as e:
            logger.debug(f"DexScreener price error for {p.symbol}: {e}")

        # 2. Query Jupiter Swap quote
        if self.jupiter and p.tokens_held > 0:
            try:
                quote = await self.jupiter.get_quote(
                    input_mint=p.token_mint,
                    output_mint=WSOL_MINT,
                    amount_lamports=int(p.tokens_held),
                    slippage_bps=self.config.slippage_bps,
                )
                if quote and "outAmount" in quote:
                    out_sol = float(quote["outAmount"]) / 1_000_000_000.0
                    if out_sol > 0 and p.tokens_held > 0:
                        return out_sol / p.tokens_held
            except Exception as e:
                logger.debug(f"Jupiter quote error for {p.symbol}: {e}")

        return None

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
        sol_price_usd = float(os.getenv("SOL_PRICE_USD", "81.05"))

        if self.config.mode == BotMode.LIVE:
            live_price = await self._fetch_live_price(p)
            if live_price is not None and live_price > 0:
                new_price = live_price
            else:
                # Do NOT fake any upward or downward movement when live quote is temporarily missing
                new_price = p.current_price_sol
        else:
            # Paper mode: simulate active market volatility
            drift = random.uniform(-0.04, 0.055)
            new_price = max(p.buy_price_sol * 0.05, p.current_price_sol * (1.0 + drift))

        p.current_price_sol = new_price
        p.current_price_usd = new_price * sol_price_usd

        if new_price > p.peak_price_sol:
            p.peak_price_sol = new_price

        # 2. Calculate PnL
        current_value_sol = p.tokens_held * new_price
        p.pnl_sol = current_value_sol - p.sol_invested
        p.pnl_pct = (p.pnl_sol / p.sol_invested) * 100.0 if p.sol_invested > 0 else 0.0
        p.pnl_usd = p.pnl_sol * sol_price_usd

        # 3. Check Breakeven Stop-Loss Lock (+20% gain locks SL at entry to guarantee risk-free trade)
        if getattr(self.config, "enable_breakeven_lock", True) and not p.breakeven_locked:
            trigger_pct = getattr(self.config, "breakeven_trigger_pct", 20.0)
            if p.pnl_pct >= trigger_pct:
                p.breakeven_locked = True
                p.stop_loss_price = max(p.stop_loss_price, p.buy_price_sol * 1.005)  # 0.5% above entry for fee buffer
                logger.info(f"🛡️ Breakeven Stop-Loss LOCKED for {p.symbol}: price reached +{p.pnl_pct:.1f}%. Risk-free trade secured.")

        # 4. Check Take Profit / Scale Out
        if p.pnl_pct >= self.config.take_profit_pct:
            if self.config.enable_scale_out and not p.scale_out_triggered:
                p.scale_out_triggered = True
                logger.info(f"Scale-out TP1 triggered for {p.symbol}: +{p.pnl_pct:.1f}%. Selling 50% moonbag setup...")
                await self.on_exit_triggered(p, ExitReason.PARTIAL_TAKE_PROFIT)
                return
            else:
                logger.info(f"Target Take Profit reached for {p.symbol}: +{p.pnl_pct:.1f}%")
                await self.on_exit_triggered(p, ExitReason.TAKE_PROFIT)
                return

        # 5. Check Stop Loss (Standard SL or Breakeven Lock)
        if new_price <= p.stop_loss_price or p.pnl_pct <= -self.config.stop_loss_pct:
            reason_desc = "Breakeven Stop Loss" if p.breakeven_locked else "Stop Loss"
            logger.info(f"{reason_desc} triggered for {p.symbol}: {p.pnl_pct:.1f}%")
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
