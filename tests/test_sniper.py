import asyncio
import unittest
from backend.models.config import BotConfig
from backend.models.order import BotMode, OrderType, OrderStatus, ExitReason
from backend.models.token import TokenData, LaunchpadType, TokenSafetyScore
from backend.services.security import SecurityService
from backend.services.jupiter import JupiterService
from backend.services.solana_client import SolanaClient
from backend.engine.executor import TradeExecutor
from backend.engine.position_tracker import PositionTracker
from backend.agents.risk_agent import RiskAgent
from backend.agents.trade_agent import TradeAgent


class TestMaguraBot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = BotConfig(
            mode=BotMode.PAPER,
            buy_amount_sol=0.05,
            take_profit_pct=50.0,
            stop_loss_pct=20.0,
            paper_wallet_sol=10.0,
        )
        self.solana_client = SolanaClient(self.config.solana_rpc_url)
        self.security = SecurityService(self.solana_client)
        self.jupiter = JupiterService()
        self.executor = TradeExecutor(self.config, self.solana_client, self.jupiter)
        self.risk_agent = RiskAgent(self.config, self.security)
        self.trade_agent = TradeAgent(self.config)

    async def asyncTearDown(self):
        await self.solana_client.close()
        await self.security.close()
        await self.jupiter.close()

    async def test_paper_buy_execution(self):
        """Verify paper buy successfully executes, deducts balance, and computes tokens."""
        token = TokenData(
            mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            symbol="BONK",
            name="Bonk",
            source=LaunchpadType.PUMPFUN,
            price_sol=0.00000005,
            bonding_curve_pct=25.0,
        )

        initial_bal = self.executor.paper_balance_sol
        order, err = await self.executor.execute_buy(token, 0.1)

        self.assertIsNone(err)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertGreater(order.amount_tokens, 0)
        self.assertAlmostEqual(self.executor.paper_balance_sol, initial_bal - 0.1, places=4)

    async def test_paper_sell_execution(self):
        """Verify paper sell credits back SOL with simulated slippage."""
        order, err = await self.executor.execute_sell(
            token_mint="TestMint123",
            symbol="TEST",
            amount_tokens=1_000_000,
            current_price_sol=0.0000001,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertGreater(order.amount_sol, 0)

    async def test_risk_filter_honeypot_rejection(self):
        """Tokens with unrevoked freeze authority must be rejected."""
        token = TokenData(
            mint="RugMint1234567890",
            symbol="RUG",
            name="Rug Token",
            source=LaunchpadType.PUMPFUN,
        )
        # Mock freeze authority active
        token.safety = TokenSafetyScore(
            score=50,
            is_safe=False,
            mint_authority_revoked=True,
            freeze_authority_revoked=False,
            reasons=["Freeze authority active"],
        )

        passed, reason = await self.risk_agent.evaluate(token)
        self.assertFalse(passed)
        self.assertIn("Freeze authority active", reason)

    async def test_trade_agent_limits(self):
        """Verify trade agent enforces maximum open positions."""
        token = TokenData(
            mint="TestMint456",
            symbol="LIMIT",
            name="Limit Token",
            source=LaunchpadType.PUMPFUN,
        )

        # Under max positions (0 < 5)
        can_snipe, _ = self.trade_agent.can_snipe(token, current_position_count=2)
        self.assertTrue(can_snipe)

        # At max positions (5 >= 5)
        can_snipe_full, reason = self.trade_agent.can_snipe(token, current_position_count=5)
        self.assertFalse(can_snipe_full)
        self.assertIn("Max open positions reached", reason)


if __name__ == "__main__":
    unittest.main()
