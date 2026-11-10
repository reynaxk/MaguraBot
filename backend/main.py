import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.engine.sniper import SniperEngine
from backend.models.config import BotConfig
from backend.models.events import BotEvent
from backend.models.order import BotMode
from backend.services.candles import CandleService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("MaguraBot")

# Initialize global sniper engine and candle service
engine = SniperEngine()
candle_service = CandleService()
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
    await engine.start()
    yield
    await engine.stop()
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
    return metrics


@app.get("/api/config")
async def get_config():
    return engine.config.model_dump()


class ConfigUpdateRequest(BaseModel):
    mode: Optional[BotMode] = None
    buy_amount_sol: Optional[float] = None
    max_positions: Optional[int] = None
    slippage_bps: Optional[int] = None
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    min_safety_score: Optional[int] = None
    enable_pumpfun: Optional[bool] = None
    enable_raydium: Optional[bool] = None


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    updates = req.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(engine.config, k, v)
    engine.executor.config = engine.config
    engine.tracker.config = engine.config
    return {"status": "success", "config": engine.config.model_dump()}


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


@app.get("/api/tokens/recent")
async def get_recent_tokens():
    tokens = [t.model_dump() for t in engine.recent_tokens]
    return tokens


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

