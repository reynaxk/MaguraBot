import base64
import logging
import time
import uuid
from typing import Optional, Tuple
from backend.models.config import BotConfig
from backend.models.order import BotMode, Order, OrderType, OrderStatus
from backend.models.token import TokenData
from backend.services.jupiter import JupiterService, WSOL_MINT
from backend.services.solana_client import SolanaClient

logger = logging.getLogger("MaguraBot.Executor")


class TradeExecutor:
    def __init__(self, config: BotConfig, solana_client: SolanaClient, jupiter: JupiterService):
        self.config = config
        self.solana_client = solana_client
        self.jupiter = jupiter
        self.paper_balance_sol = config.paper_wallet_sol

    def get_public_key(self) -> Optional[str]:
        """Return base58 public key of configured wallet if available."""
        if not self.config.solana_private_key:
            return None
        kp = self.solana_client.load_keypair(self.config.solana_private_key)
        return str(kp.pubkey()) if kp else None

    async def execute_buy(self, token: TokenData, amount_sol: float) -> Tuple[Optional[Order], Optional[str]]:
        """Execute buy order in paper simulation or live on-chain."""
        order = Order(
            token_mint=token.mint,
            symbol=token.symbol,
            order_type=OrderType.BUY,
            mode=self.config.mode,
            amount_sol=amount_sol,
            slippage_bps=self.config.slippage_bps,
        )

        if self.config.mode == BotMode.PAPER:
            return await self._execute_paper_buy(token, order, amount_sol)
        else:
            return await self._execute_live_buy(token, order, amount_sol)

    async def execute_sell(
        self,
        token_mint: str,
        symbol: str,
        amount_tokens: float,
        current_price_sol: float,
    ) -> Tuple[Optional[Order], Optional[str]]:
        """Execute sell order to close position."""
        order = Order(
            token_mint=token_mint,
            symbol=symbol,
            order_type=OrderType.SELL,
            mode=self.config.mode,
            amount_sol=amount_tokens * current_price_sol,
            amount_tokens=amount_tokens,
            slippage_bps=self.config.slippage_bps,
        )

        if self.config.mode == BotMode.PAPER:
            return await self._execute_paper_sell(order, amount_tokens, current_price_sol)
        else:
            return await self._execute_live_sell(order, token_mint, amount_tokens)

    # ----------------------------------------------------
    # Paper Trading Implementation
    # ----------------------------------------------------
    async def _execute_paper_buy(
        self,
        token: TokenData,
        order: Order,
        amount_sol: float,
    ) -> Tuple[Optional[Order], Optional[str]]:
        if self.paper_balance_sol < amount_sol:
            err = f"Insufficient paper SOL balance ({self.paper_balance_sol:.3f} SOL available)"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        # Base price calculation (fallback to standard bonding curve or dex price)
        price_sol = token.price_sol if token.price_sol > 0 else (0.00000003 + (token.bonding_curve_pct * 0.00000005))
        # Add 1.5% simulated slippage
        effective_price = price_sol * 1.015
        tokens_received = amount_sol / effective_price

        self.paper_balance_sol -= amount_sol
        order.status = OrderStatus.FILLED
        order.price_sol = effective_price
        order.price_usd = effective_price * 180.0  # reference benchmark SOL price
        order.amount_tokens = tokens_received
        order.tx_hash = f"paper_tx_{uuid.uuid4().hex[:12]}"
        order.completed_at = time.time()

        logger.info(
            f"[PAPER BUY] Bought {tokens_received:,.0f} {token.symbol} for {amount_sol} SOL @ {effective_price:.9f} SOL"
        )
        return order, None

    async def _execute_paper_sell(
        self,
        order: Order,
        amount_tokens: float,
        current_price_sol: float,
    ) -> Tuple[Optional[Order], Optional[str]]:
        # Add 1.5% simulated slippage
        effective_price = current_price_sol * 0.985
        sol_received = amount_tokens * effective_price

        self.paper_balance_sol += sol_received
        order.status = OrderStatus.FILLED
        order.price_sol = effective_price
        order.price_usd = effective_price * 180.0
        order.amount_sol = sol_received
        order.tx_hash = f"paper_tx_{uuid.uuid4().hex[:12]}"
        order.completed_at = time.time()

        logger.info(
            f"[PAPER SELL] Sold {amount_tokens:,.0f} {order.symbol} for {sol_received:.4f} SOL @ {effective_price:.9f} SOL"
        )
        return order, None

    # ----------------------------------------------------
    # Live On-Chain Trading Implementation
    # ----------------------------------------------------
    async def _execute_live_buy(
        self,
        token: TokenData,
        order: Order,
        amount_sol: float,
    ) -> Tuple[Optional[Order], Optional[str]]:
        keypair = self.solana_client.load_keypair(self.config.solana_private_key)
        if not keypair:
            err = "Live trading active but SOLANA_PRIVATE_KEY is missing or invalid"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        wallet_pubkey = str(keypair.pubkey())
        lamports = int(amount_sol * 1_000_000_000)

        # 1. Fetch quote from Jupiter
        quote = await self.jupiter.get_quote(
            input_mint=WSOL_MINT,
            output_mint=token.mint,
            amount_lamports=lamports,
            slippage_bps=self.config.slippage_bps,
        )
        if not quote:
            err = f"Could not obtain swap quote for {token.symbol}"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        # 2. Get serialized swap transaction
        raw_tx_b64 = await self.jupiter.get_swap_transaction(
            quote_response=quote,
            user_public_key=wallet_pubkey,
            priority_fee_lamports=self.config.priority_fee_microlamports,
        )
        if not raw_tx_b64:
            err = "Failed to construct Jupiter swap transaction"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        # 3. Sign & Broadcast transaction
        try:
            tx_sig = await self.solana_client.send_raw_transaction(raw_tx_b64)
            if tx_sig:
                order.status = OrderStatus.FILLED
                order.tx_hash = tx_sig
                order.completed_at = time.time()
                out_amount = float(quote.get("outAmount", 0))
                order.amount_tokens = out_amount
                order.price_sol = amount_sol / out_amount if out_amount > 0 else 0.0
                logger.info(f"[LIVE BUY SUCCESS] {order.symbol} Tx: {tx_sig}")
                return order, None
            else:
                err = "Transaction submission failed"
                order.status = OrderStatus.FAILED
                order.error_message = err
                return order, err
        except Exception as e:
            err = f"Transaction error: {e}"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

    async def _execute_live_sell(
        self,
        order: Order,
        token_mint: str,
        amount_tokens: float,
    ) -> Tuple[Optional[Order], Optional[str]]:
        keypair = self.solana_client.load_keypair(self.config.solana_private_key)
        if not keypair:
            err = "Live trading active but SOLANA_PRIVATE_KEY is missing"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        wallet_pubkey = str(keypair.pubkey())
        # Quote: Token -> WSOL
        quote = await self.jupiter.get_quote(
            input_mint=token_mint,
            output_mint=WSOL_MINT,
            amount_lamports=int(amount_tokens),
            slippage_bps=self.config.slippage_bps,
        )
        if not quote:
            err = f"Could not obtain sell quote for {order.symbol}"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        raw_tx_b64 = await self.jupiter.get_swap_transaction(
            quote_response=quote,
            user_public_key=wallet_pubkey,
            priority_fee_lamports=self.config.priority_fee_microlamports,
        )
        if not raw_tx_b64:
            err = "Failed to construct Jupiter sell transaction"
            order.status = OrderStatus.FAILED
            order.error_message = err
            return order, err

        tx_sig = await self.solana_client.send_raw_transaction(raw_tx_b64)
        if tx_sig:
            order.status = OrderStatus.FILLED
            order.tx_hash = tx_sig
            order.completed_at = time.time()
            return order, None

        err = "Live sell transaction submission failed"
        order.status = OrderStatus.FAILED
        order.error_message = err
        return order, err
