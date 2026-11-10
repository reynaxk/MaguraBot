import asyncio
import logging
from typing import Optional
import aiohttp

logger = logging.getLogger("MaguraBot.Alerts")


class AlertService:
    def __init__(
        self,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        discord_webhook_url: Optional[str] = None,
    ):
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook_url = discord_webhook_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_snipe_alert(self, symbol: str, mint: str, amount_sol: float, mode: str):
        title = f"🎯 [SNIPED] {symbol} ({mode.upper()})"
        msg = (
            f"**{title}**\n"
            f"• Token: `{symbol}`\n"
            f"• Amount: `{amount_sol:.3f} SOL`\n"
            f"• Mint: `{mint}`\n"
            f"• [DexScreener](https://dexscreener.com/solana/{mint})"
        )
        await self._dispatch(msg)

    async def send_exit_alert(self, symbol: str, pnl_pct: float, pnl_sol: float, reason: str, mode: str):
        emoji = "🚀" if pnl_pct >= 0 else "🛑"
        sign = "+" if pnl_pct >= 0 else ""
        title = f"{emoji} [EXIT - {reason.upper()}] {symbol} ({mode.upper()})"
        msg = (
            f"**{title}**\n"
            f"• PnL: `{sign}{pnl_pct:.1f}%` ({sign}{pnl_sol:.4f} SOL)\n"
            f"• Strategy: `{reason}`"
        )
        await self._dispatch(msg)

    async def _dispatch(self, text: str):
        session = await self._get_session()
        
        # 1. Discord Webhook
        if self.discord_webhook_url:
            try:
                payload = {"content": text}
                async with session.post(self.discord_webhook_url, json=payload) as resp:
                    if resp.status >= 400:
                        logger.debug(f"Discord webhook failed: {resp.status}")
            except Exception as e:
                logger.debug(f"Discord alert exception: {e}")

        # 2. Telegram Bot
        if self.telegram_bot_token and self.telegram_chat_id:
            try:
                url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
                payload = {
                    "chat_id": self.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                }
                async with session.post(url, json=payload) as resp:
                    if resp.status >= 400:
                        logger.debug(f"Telegram alert failed: {resp.status}")
            except Exception as e:
                logger.debug(f"Telegram alert exception: {e}")
