import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional
import csv
import io
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from backend.engine.sniper import SniperEngine
from backend.models.config import BotConfig
from backend.models.events import BotEvent
from backend.models.order import BotMode
from backend.services.candles import CandleService
from backend.services.solana_client import SolanaClient
from backend.engine.backtester import Backtester, BacktestRequest
from backend.services.rpc_pool import RPCPoolService
from backend.services.pump_curve import PumpCurveCalculator
from backend.services.holders import HoldersScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("MaguraBot")

# Initialize global sniper engine and services
engine = SniperEngine()
candle_service = CandleService()
rpc_pool = RPCPoolService([engine.config.solana_rpc_url])
holders_scanner = HoldersScanner(engine.solana_client)
active_websockets: list[WebSocket] = []


def on_bot_event(event: BotEvent):
    """Broadcast sniper events to all connected WebSocket clients."""
    if not active_websockets:
        return
    payload = event.model_dump_json()
    disconnected = []
    for ws in active_websockets:
        try:
            asyncio.create_task(ws.send_text(payload))
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.subscribe(on_bot_event)
    await rpc_pool.start()
    if os.getenv("AUTO_START_BOT", "false").lower() in ("true", "1"):
        await engine.start()
    yield
    await engine.stop()
    await rpc_pool.stop()
    await holders_scanner.close()
    await candle_service.close()


app = FastAPI(
    title="MaguraBot API",
    description="High-Speed Solana Memecoin Sniper Engine & Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------
# REST API Endpoints
# ----------------------------------------------------
@app.get("/api/status")
async def get_status():
    metrics = engine.get_metrics()
    pubkey = engine.executor.get_public_key()
    metrics["wallet_address"] = pubkey
    metrics["has_wallet"] = bool(pubkey)
    if pubkey:
        try:
            live_bal = await engine.solana_client.get_balance(pubkey)
            metrics["live_balance_sol"] = round(live_bal, 4)
            metrics["live_balance_usd"] = round(live_bal * engine.sol_price_usd, 2)
        except Exception:
            metrics["live_balance_sol"] = 0.0
            metrics["live_balance_usd"] = 0.0
    return metrics


@app.get("/api/wallet")
async def get_wallet():
    pubkey = engine.executor.get_public_key()
    if not pubkey:
        return {
            "configured": False,
            "address": None,
            "balance_sol": 0.0,
            "balance_usd": 0.0,
            "mode": engine.config.mode.value
        }
    try:
        balance = await engine.solana_client.get_balance(pubkey)
    except Exception:
        balance = 0.0
    return {
        "configured": True,
        "address": pubkey,
        "balance_sol": round(balance, 4),
        "balance_usd": round(balance * engine.sol_price_usd, 2),
        "mode": engine.config.mode.value
    }


def _mask_config(cfg: dict) -> dict:
    masked = dict(cfg)
    pk = masked.get("solana_private_key")
    if pk:
        masked["solana_private_key"] = f"***{pk[-6:]}" if len(pk) > 10 else "***"
        masked["has_private_key"] = True
    else:
        masked["has_private_key"] = False
    return masked


@app.get("/api/config")
async def get_config():
    return _mask_config(engine.config.model_dump())


class ConfigUpdateRequest(BaseModel):
    mode: Optional[BotMode] = None
    auto_snipe: Optional[bool] = None
    buy_amount_sol: Optional[float] = None
    max_positions: Optional[int] = None
    slippage_bps: Optional[int] = None
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    enable_scale_out: Optional[bool] = None
    min_safety_score: Optional[int] = None
    max_daily_loss_sol: Optional[float] = None
    use_jito: Optional[bool] = None
    jito_tip_sol: Optional[float] = None
    enable_dynamic_jito_tip: Optional[bool] = None
    max_jito_tip_sol: Optional[float] = None
    priority_fee_level: Optional[str] = None
    priority_fee_microlamports: Optional[int] = None
    solana_rpc_url: Optional[str] = None
    solana_private_key: Optional[str] = None
    enable_pumpfun: Optional[bool] = None
    enable_raydium: Optional[bool] = None
    enable_meteora: Optional[bool] = None
    enable_breakeven_lock: Optional[bool] = None
    breakeven_trigger_pct: Optional[float] = None
    discord_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    blacklist_keywords: Optional[list[str]] = None
    blacklist_creators: Optional[list[str]] = None


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    updates = req.model_dump(exclude_unset=True)
    if "solana_private_key" in updates and updates["solana_private_key"]:
        kp = engine.solana_client.load_keypair(updates["solana_private_key"])
        if not kp:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid Solana private key. Must be valid base58 string or byte array."})
    for k, v in updates.items():
        setattr(engine.config, k, v)
    if "priority_fee_level" in updates:
        level = updates["priority_fee_level"].lower()
        if level == "turbo":
            engine.config.priority_fee_microlamports = 1000000
        elif level == "fast":
            engine.config.priority_fee_microlamports = 250000
        else:
            engine.config.priority_fee_microlamports = 50000
    engine.executor.config = engine.config
    engine.tracker.config = engine.config
    if "solana_rpc_url" in updates:
        engine.solana_client.rpc_url = updates["solana_rpc_url"]
    if "discord_webhook_url" in updates:
        engine.alerts.discord_webhook_url = updates["discord_webhook_url"]
    if "telegram_bot_token" in updates:
        engine.alerts.telegram_bot_token = updates["telegram_bot_token"]
    if "telegram_chat_id" in updates:
        engine.alerts.telegram_chat_id = updates["telegram_chat_id"]
    return {"status": "success", "config": _mask_config(engine.config.model_dump())}


@app.post("/api/bot/start")
async def start_bot():
    await engine.start()
    return {"status": "started", "running": engine.is_running}


@app.post("/api/bot/stop")
async def stop_bot():
    await engine.stop()
    return {"status": "stopped", "running": engine.is_running}


@app.get("/api/positions")
async def get_positions():
    open_positions = [p.model_dump() for p in engine.positions.values() if p.status == "open"]
    return open_positions


@app.get("/api/trades")
async def get_trades():
    trades = [p.model_dump() for p in engine.closed_trades]
    return trades


@app.get("/api/trades/export")
async def export_trades_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Symbol", "Mint", "Status", "Invested_SOL",
        "Tokens_Held", "Entry_Price_SOL", "Exit_Price_SOL",
        "Realized_PnL_SOL", "PnL_Pct", "Opened_At", "Closed_At"
    ])
    for p in engine.closed_trades:
        writer.writerow([
            p.id,
            p.symbol,
            p.token_mint,
            p.status.value if hasattr(p.status, "value") else str(p.status),
            round(p.sol_invested, 5),
            p.tokens_held,
            f"{p.entry_price_sol:.9f}",
            f"{p.current_price_sol:.9f}",
            round(p.pnl_sol, 5),
            round(p.pnl_pct, 2),
            p.opened_at,
            p.closed_at or ""
        ])
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=magurabot_trades.csv"}
    )


@app.get("/api/analytics")
async def get_analytics():
    trades = engine.closed_trades
    total_trades = max(engine.lifetime_trades_count, len(trades))
    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_realized_pnl_sol": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "equity_curve": [],
        }

    wins = [t for t in trades if t.pnl_sol > 0]
    losses = [t for t in trades if t.pnl_sol <= 0]
    winning_trades = engine.lifetime_wins if engine.lifetime_trades_count >= len(trades) else len(wins)
    losing_trades = max(0, total_trades - winning_trades)
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
    total_pnl = engine.lifetime_pnl_sol if engine.lifetime_trades_count >= len(trades) else sum(t.pnl_sol for t in trades)
    avg_win = (sum(t.pnl_pct for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(t.pnl_pct for t in losses) / len(losses)) if losses else 0.0
    gross_profits = sum(t.pnl_sol for t in wins)
    gross_losses = abs(sum(t.pnl_sol for t in losses))
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (99.0 if gross_profits > 0 else 0.0)

    equity_curve = []
    cumulative = 0.0
    for t in sorted(trades, key=lambda x: x.closed_at or 0):
        cumulative += t.pnl_sol
        equity_curve.append({
            "timestamp": t.closed_at or t.opened_at,
            "symbol": t.symbol,
            "pnl_sol": round(t.pnl_sol, 5),
            "cumulative_pnl_sol": round(cumulative, 5),
        })

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 1),
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "total_realized_pnl_sol": round(total_pnl, 5),
        "avg_win_pct": round(avg_win, 1),
        "avg_loss_pct": round(avg_loss, 1),
        "profit_factor": round(profit_factor, 2),
        "equity_curve": equity_curve,
    }


@app.get("/api/wallet/info")
async def get_wallet_info():
    pubkey = engine.executor.get_public_key()
    mode = engine.config.mode.value
    balance = 0.0
    if mode == "live" and pubkey:
        balance = await engine.solana_client.get_balance(pubkey)
    else:
        balance = engine.executor.paper_balance_sol
    return {
        "mode": mode,
        "public_key": pubkey or "Paper Wallet (Simulation)",
        "balance_sol": round(balance, 4),
        "is_live": mode == "live" and bool(pubkey),
    }


@app.post("/api/wallet/generate")
async def generate_wallet():
    pub, priv = SolanaClient.generate_keypair()
    return {
        "public_key": pub,
        "private_key": priv,
        "note": "Save your private key securely. Never share your secret key with anyone."
    }


@app.get("/api/network/latency")
async def get_network_latency():
    ms = await engine.solana_client.get_latency_ms()
    return {
        "rpc_url": engine.config.solana_rpc_url,
        "latency_ms": ms,
        "healthy": ms > 0 and ms < 2000,
        "mode": engine.config.mode.value,
        "use_jito": engine.config.use_jito,
    }


@app.get("/api/rpc/pool")
async def get_rpc_pool():
    return {
        "primary": rpc_pool.get_primary_endpoint(),
        "endpoints": rpc_pool.get_pool_status(),
    }


@app.get("/api/pump/curve/{mint}")
async def get_pump_curve(mint: str, deposited_sol: float = 0.0):
    if deposited_sol <= 0.0:
        for t in engine.recent_tokens:
            if t.mint == mint and t.v_sol_in_bonding_curve:
                deposited_sol = t.v_sol_in_bonding_curve
                break
    return PumpCurveCalculator.calculate_bonding_progress(deposited_sol)


@app.get("/api/tokens/{mint}/holders")
async def get_token_holders(mint: str):
    return await holders_scanner.analyze_holders(mint)


@app.get("/api/jito/tip-floor")
async def get_jito_tip_floor():
    floors = await engine.jito.fetch_tip_floor()
    return {
        "status": "success",
        "tip_floors_sol": floors,
        "base_tip_sol": engine.config.jito_tip_sol,
        "dynamic_enabled": getattr(engine.config, "enable_dynamic_jito_tip", True),
        "max_tip_sol": getattr(engine.config, "max_jito_tip_sol", 0.01),
    }


@app.get("/api/tokens/recent")
async def get_recent_tokens():
    tokens = [t.model_dump() for t in engine.recent_tokens]
    return tokens


@app.get("/api/tokens/{mint}/audit")
async def get_token_audit(mint: str):
    symbol = "TOKEN"
    for t in engine.recent_tokens:
        if t.mint == mint:
            symbol = t.symbol
            break
    if symbol == "TOKEN":
        for p in engine.positions.values():
            if p.token_mint == mint:
                symbol = p.symbol
                break
    audit = await engine.security.get_token_audit(mint, symbol)
    return audit


@app.get("/api/tokens/{mint}/candles")
async def get_token_candles(mint: str, timeframe: str = "5m", limit: int = 60):
    current_price = 0.0
    for p in engine.positions.values():
        if p.token_mint == mint:
            current_price = p.current_price_sol
            break
    if current_price == 0.0:
        for t in engine.recent_tokens:
            if t.mint == mint and t.price_sol > 0:
                current_price = t.price_sol
                break

    candles = await candle_service.get_candles(
        mint=mint,
        timeframe=timeframe,
        limit=limit,
        current_price_sol=current_price,
    )
    return {
        "mint": mint,
        "timeframe": timeframe,
        "candles": candles,
    }


@app.post("/api/positions/{position_id}/sell")
async def manual_sell_position(position_id: str):
    success = await engine.close_position(position_id)
    if success:
        return {"status": "success", "message": f"Position {position_id} market sold"}
    return JSONResponse(status_code=404, content={"status": "error", "message": "Position not found or not open"})


@app.post("/api/positions/emergency-sell-all")
async def emergency_sell_all():
    await engine.emergency_sell_all()
    return {"status": "success", "message": "Emergency sell triggered for all positions"}


class ManualSnipeRequest(BaseModel):
    mint: str
    symbol: Optional[str] = "MANUAL"
    amount_sol: Optional[float] = None


@app.post("/api/snipe")
async def manual_snipe(req: ManualSnipeRequest):
    asyncio.create_task(engine.manual_snipe(req.mint, req.symbol, req.amount_sol))
    return {"status": "queued", "mint": req.mint}


@app.post("/api/backtest/run")
async def run_backtest(req: BacktestRequest):
    result = await asyncio.to_thread(Backtester.run_simulation, req)
    return result.model_dump()


# ----------------------------------------------------
# Real-time WebSocket
# ----------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    # Send initial snapshot
    init_data = {
        "event": "snapshot",
        "metrics": engine.get_metrics(),
        "positions": [p.model_dump() for p in engine.positions.values() if p.status == "open"],
        "recent_tokens": [t.model_dump() for t in engine.recent_tokens[:20]],
        "trades": [p.model_dump() for p in engine.closed_trades[:20]],
    }
    await websocket.send_text(json.dumps(init_data))
    try:
        while True:
            # Keep socket alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = BASE_DIR / "dashboard" / "index.html"


# ----------------------------------------------------
# Integrated Web UI
# ----------------------------------------------------
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def serve_dashboard():
    if DASHBOARD_FILE.exists():
        return DASHBOARD_FILE.read_text(encoding="utf-8")
    return HTMLResponse(
        content=f"<h1>MaguraBot Dashboard</h1><p>Dashboard HTML not found at: {DASHBOARD_FILE}</p>",
        status_code=500,
    )

