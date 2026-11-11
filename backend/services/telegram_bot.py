import asyncio
import logging
from typing import Optional, Callable
import aiohttp

logger = logging.getLogger("MaguraBot.TelegramController")


class TelegramBotController:
    """Interactive Telegram polling listener to control MaguraBot via chat commands."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        engine=None,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.engine = engine
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_update_id = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def start(self):
        if not self.bot_token or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Interactive Telegram controller started")

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
        logger.info("Interactive Telegram controller stopped")

    async def send_message(self, text: str, target_chat_id: Optional[str] = None):
        cid = target_chat_id or self.chat_id
        if not self.bot_token or not cid:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        session = await self._get_session()
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            async with session.post(url, json=payload) as resp:
                await resp.json()
        except Exception as e:
            logger.debug(f"Failed to send Telegram message: {e}")

    async def _poll_loop(self):
        while self._running:
            try:
                session = await self._get_session()
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {"offset": self._last_update_id + 1, "timeout": 10}
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        for u in updates:
                            self._last_update_id = u["update_id"]
                            msg = u.get("message", {})
                            text = msg.get("text", "").strip()
                            cid = str(msg.get("chat", {}).get("id", ""))
                            if text:
                                await self._handle_command(text, cid)
            except Exception as e:
                logger.debug(f"Telegram polling error: {e}")
            await asyncio.sleep(2)

    async def _handle_command(self, cmd: str, chat_id: str):
        cmd = cmd.lower()
        if not self.engine:
            return

        if cmd.startswith("/start") or cmd.startswith("/help"):
            reply = (
                "🎯 <b>MaguraBot Solana Sniper Controller</b>\n\n"
                "Available commands:\n"
                "• <code>/status</code> - View bot metrics & wallet balance\n"
                "• <code>/positions</code> - List active open trades & PnL\n"
                "• <code>/pause</code> - Pause automated sniping\n"
                "• <code>/resume</code> - Resume automated sniping\n"
                "• <code>/panic</code> - Emergency Exit (Sell All immediately)"
            )
            await self.send_message(reply, chat_id)

        elif cmd.startswith("/status"):
            m = self.engine.get_metrics()
            reply = (
                f"📊 <b>MaguraBot Status</b>\n"
                f"• Mode: <b>{m['mode'].upper()}</b>\n"
                f"• Auto-Snipe: <b>{'ACTIVE' if m.get('auto_snipe') else 'PAUSED'}</b>\n"
                f"• Open Positions: <b>{m['open_positions_count']} / 5</b>\n"
                f"• Wallet Balance: <b>{m['paper_balance_sol']:.3f} SOL</b>\n"
                f"• Total PnL: <b>{m['total_pnl_sol']:+.4f} SOL</b>\n"
                f"• Win Rate: <b>{m['win_rate_pct']:.1f}%</b> ({m['total_trades']} trades)"
            )
            await self.send_message(reply, chat_id)

        elif cmd.startswith("/positions"):
            open_pos = [p for p in self.engine.positions.values() if p.status == "open"]
            if not open_pos:
                await self.send_message("ℹ️ No active open positions right now.", chat_id)
                return

            lines = ["💼 <b>Active Open Positions:</b>\n"]
            for p in open_pos:
                sign = '+' if p.pnl_pct >= 0 else ''
                lines.append(f"• <b>{p.symbol}</b>: {sign}{p.pnl_pct:.1f}% ({sign}{p.pnl_sol:.4f} SOL)")
            await self.send_message("\n".join(lines), chat_id)

        elif cmd.startswith("/pause"):
            self.engine.config.auto_snipe = False
            await self.send_message("⏸️ <b>Auto-Snipe PAUSED.</b> Token discovery will continue without automatic buys.", chat_id)

        elif cmd.startswith("/resume"):
            self.engine.config.auto_snipe = True
            await self.send_message("▶️ <b>Auto-Snipe RESUMED.</b> Safe tokens will be sniped automatically.", chat_id)

        elif cmd.startswith("/panic"):
            await self.engine.emergency_sell_all()
            await self.send_message("🚨 <b>EMERGENCY SELL ALL TRIGGERED!</b> All active positions have been queued for immediate exit.", chat_id)
