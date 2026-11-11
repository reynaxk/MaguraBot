import math
import random
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    initial_balance_sol: float = Field(default=10.0, gt=0)
    buy_amount_sol: float = Field(default=0.1, gt=0)
    take_profit_pct: float = Field(default=50.0, ge=1.0)
    stop_loss_pct: float = Field(default=20.0, ge=1.0)
    trailing_stop_pct: float = Field(default=15.0, ge=1.0)
    enable_scale_out: bool = True
    slippage_pct: float = 1.5
    simulated_token_count: int = Field(default=60, ge=10, le=200)


class BacktestResult(BaseModel):
    initial_balance_sol: float
    final_balance_sol: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    net_pnl_sol: float
    net_pnl_usd: float
    max_drawdown_pct: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    equity_curve: List[Dict[str, Any]]
    trades: List[Dict[str, Any]]


class Backtester:
    """Strategy Simulator & Backtesting Engine for Memecoin Sniping."""

    @staticmethod
    def run_simulation(req: BacktestRequest) -> BacktestResult:
        balance = req.initial_balance_sol
        peak_balance = balance
        max_drawdown_sol = 0.0

        trades = []
        equity_curve = []
        now = time.time() - (req.simulated_token_count * 300)

        wins = 0
        losses = 0
        gross_profit = 0.0
        gross_loss = 0.0
        win_pcts = []
        loss_pcts = []

        token_names = [
            "PEPE2", "SOLCAT", "DOGEX", "PUMPZ", "MOON", "RUGGY", "BONKLE",
            "SHIBAX", "FROG", "NEO", "SAMO2", "CHAD", "WOJAK", "BULL", "BEAR"
        ]

        for i in range(req.simulated_token_count):
            if balance < req.buy_amount_sol:
                break  # Insufficient funds to continue

            trade_time = now + (i * 300)
            symbol = f"${random.choice(token_names)}{random.randint(10, 99)}"
            entry_price = random.uniform(0.00000002, 0.0000001)
            effective_entry = entry_price * (1.0 + (req.slippage_pct / 100.0))
            tokens = req.buy_amount_sol / effective_entry

            # Scenario generation: 30% Moonshots, 40% quick bleed/dump, 30% normal bounce
            scenario_roll = random.random()
            if scenario_roll < 0.28:
                # Moonshot / Big runner (+60% to +350%)
                peak_multiplier = random.uniform(1.6, 4.5)
            elif scenario_roll < 0.65:
                # Rug / Dump (-15% to -80%)
                peak_multiplier = random.uniform(0.4, 1.1)
            else:
                # Moderate pump (+15% to +55%)
                peak_multiplier = random.uniform(1.15, 1.55)

            peak_price = entry_price * peak_multiplier
            peak_gain_pct = ((peak_price - entry_price) / entry_price) * 100.0

            exit_reason = "stop_loss"
            exit_price = entry_price * (1.0 - (req.stop_loss_pct / 100.0))

            if peak_gain_pct >= req.take_profit_pct:
                if req.enable_scale_out:
                    # Scale out: 50% sold at TP, 50% trailing
                    exit_reason = "scale_out_tp"
                    tp_price = entry_price * (1.0 + (req.take_profit_pct / 100.0))
                    moonbag_exit = peak_price * (1.0 - (req.trailing_stop_pct / 100.0))
                    exit_price = (tp_price * 0.5) + (moonbag_exit * 0.5)
                else:
                    exit_reason = "take_profit"
                    exit_price = entry_price * (1.0 + (req.take_profit_pct / 100.0))
            elif peak_gain_pct >= 20.0:
                # Hit trailing stop before reaching TP
                exit_reason = "trailing_stop"
                exit_price = peak_price * (1.0 - (req.trailing_stop_pct / 100.0))

            effective_exit = exit_price * (1.0 - (req.slippage_pct / 100.0))
            pnl_sol = (tokens * effective_exit) - req.buy_amount_sol
            pnl_pct = (pnl_sol / req.buy_amount_sol) * 100.0

            balance += pnl_sol
            if balance > peak_balance:
                peak_balance = balance
            dd = peak_balance - balance
            if dd > max_drawdown_sol:
                max_drawdown_sol = dd

            if pnl_sol > 0:
                wins += 1
                gross_profit += pnl_sol
                win_pcts.append(pnl_pct)
            else:
                losses += 1
                gross_loss += abs(pnl_sol)
                loss_pcts.append(pnl_pct)

            trades.append({
                "trade_id": i + 1,
                "symbol": symbol,
                "timestamp": trade_time,
                "invested_sol": req.buy_amount_sol,
                "exit_reason": exit_reason,
                "pnl_sol": round(pnl_sol, 4),
                "pnl_pct": round(pnl_pct, 1),
                "running_balance_sol": round(balance, 4),
            })

            equity_curve.append({
                "timestamp": trade_time,
                "balance_sol": round(balance, 4),
                "pnl_sol": round(pnl_sol, 4),
            })

        total_trades = wins + losses
        win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        max_dd_pct = (max_drawdown_sol / peak_balance * 100.0) if peak_balance > 0 else 0.0

        return BacktestResult(
            initial_balance_sol=req.initial_balance_sol,
            final_balance_sol=round(balance, 4),
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            win_rate_pct=round(win_rate, 1),
            net_pnl_sol=round(balance - req.initial_balance_sol, 4),
            net_pnl_usd=round((balance - req.initial_balance_sol) * 180.0, 2),
            max_drawdown_pct=round(max_dd_pct, 1),
            profit_factor=round(profit_factor, 2),
            avg_win_pct=round(sum(win_pcts) / len(win_pcts), 1) if win_pcts else 0.0,
            avg_loss_pct=round(sum(loss_pcts) / len(loss_pcts), 1) if loss_pcts else 0.0,
            equity_curve=equity_curve,
            trades=trades,
        )
