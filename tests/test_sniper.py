import asyncio
import unittest
from unittest.mock import AsyncMock
from backend.models.config import BotConfig
from backend.models.order import BotMode, OrderType, OrderStatus, ExitReason, Position
from backend.models.token import TokenData, LaunchpadType, TokenSafetyScore
from backend.services.security import SecurityService
from backend.services.jupiter import JupiterService
from backend.services.solana_client import SolanaClient
from backend.engine.executor import TradeExecutor
from backend.engine.position_tracker import PositionTracker
from backend.agents.risk_agent import RiskAgent
from backend.agents.trade_agent import TradeAgent
from backend.engine.sniper import SniperEngine


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

        # At max positions
        can_snipe_full, reason = self.trade_agent.can_snipe(token, current_position_count=self.config.max_positions)
        self.assertFalse(can_snipe_full)
        self.assertIn("Max open positions reached", reason)

    async def test_blacklist_rejection(self):
        """Tokens matching blacklisted keywords or creators must be rejected."""
        # 1. Keyword match
        token = TokenData(
            mint="ScamMint123",
            symbol="SCAMDOGE",
            name="Super Scam Coin",
            source=LaunchpadType.PUMPFUN,
            safety=TokenSafetyScore(score=95, mint_authority_revoked=True, freeze_authority_revoked=True),
        )
        passed, reason = await self.risk_agent.evaluate(token)
        self.assertFalse(passed)
        self.assertIn("blacklisted keyword", reason)

        # 2. Creator match
        self.config.blacklist_creators.append("EvilCreatorWallet123")
        safe_token = TokenData(
            mint="SafeMint456",
            symbol="GOOD",
            name="Good Coin",
            creator="EvilCreatorWallet123",
            source=LaunchpadType.PUMPFUN,
            safety=TokenSafetyScore(score=95, mint_authority_revoked=True, freeze_authority_revoked=True),
        )
        passed2, reason2 = await self.risk_agent.evaluate(safe_token)
        self.assertFalse(passed2)
    async def test_scale_out_execution(self):
        """When scale out is enabled, Take Profit should trigger PARTIAL_TAKE_PROFIT."""
        self.config.enable_scale_out = True
        triggered_reasons = []

        async def mock_exit_callback(pos, reason):
            triggered_reasons.append(reason)

        tracker = PositionTracker(self.config, mock_exit_callback)
        pos = Position(
            token_mint="TestMoon123",
            symbol="MOON",
            name="Moon Token",
            mode=BotMode.PAPER,
            buy_order_id="b1",
            buy_price_sol=0.0001,
            buy_price_usd=0.018,
            sol_invested=0.1,
            tokens_held=1000.0,
            current_price_sol=0.0001,
            current_price_usd=0.018,
            peak_price_sol=0.0001,
            take_profit_price=0.00015,
            stop_loss_price=0.00008,
        )
        tracker.add_position(pos)
        self.assertEqual(pos.initial_tokens_held, 1000.0)

        # Simulate +60% pump
        pos.current_price_sol = 0.00016
        await tracker._evaluate_position(pos)

        self.assertIn(ExitReason.PARTIAL_TAKE_PROFIT, triggered_reasons)
        self.assertTrue(pos.scale_out_triggered)

    async def test_full_take_profit_without_moonbag(self):
        """When enable_scale_out is False, Take Profit must trigger full TAKE_PROFIT and sell 100%."""
        self.config.enable_scale_out = False
        triggered_reasons = []

        async def mock_exit_callback(pos, reason):
            triggered_reasons.append(reason)

        tracker = PositionTracker(self.config, mock_exit_callback)
        pos = Position(
            token_mint="TestFullTP123",
            symbol="FULL",
            name="Full Exit Token",
            mode=BotMode.PAPER,
            buy_order_id="b2",
            buy_price_sol=0.0001,
            buy_price_usd=0.018,
            sol_invested=0.1,
            tokens_held=1000.0,
            current_price_sol=0.0001,
            current_price_usd=0.018,
            peak_price_sol=0.0001,
            take_profit_price=0.00015,
            stop_loss_price=0.00008,
        )
        tracker.add_position(pos)

        # Simulate +60% pump
        pos.current_price_sol = 0.00016
        await tracker._evaluate_position(pos)

        self.assertIn(ExitReason.TAKE_PROFIT, triggered_reasons)
        self.assertNotIn(ExitReason.PARTIAL_TAKE_PROFIT, triggered_reasons)
        self.assertFalse(pos.scale_out_triggered)

    async def test_meteora_listener_init(self):
        """Verify MeteoraListener instantiates and cleanly starts and stops."""
        from backend.services.meteora import MeteoraListener
        discovered = []
        listener = MeteoraListener(lambda t: discovered.append(t))
        self.assertFalse(listener._running)
        await listener.start()
        self.assertTrue(listener._running)
        await listener.stop()
        self.assertFalse(listener._running)

    async def test_breakeven_lock(self):
        """Verify that when position reaches +20% gain, Stop-Loss is raised to breakeven."""
        mock_exit_callback = asyncio.iscoroutinefunction
        tracker = PositionTracker(self.config, lambda p, r: None)
        pos = Position(
            token_mint="TestBreakeven123",
            symbol="SAFE",
            name="Safe Token",
            mode=BotMode.PAPER,
            buy_order_id="b2",
            buy_price_sol=0.0001,
            buy_price_usd=0.018,
            sol_invested=0.1,
            tokens_held=1000.0,
            current_price_sol=0.0001,
            current_price_usd=0.018,
            peak_price_sol=0.0001,
            take_profit_price=0.00015,
            stop_loss_price=0.00008,
        )
        tracker.add_position(pos)
        self.assertFalse(pos.breakeven_locked)
        self.assertEqual(pos.stop_loss_price, 0.00008)

        # Simulate +25% pump
        pos.current_price_sol = 0.000125
        await tracker._evaluate_position(pos)

        self.assertTrue(pos.breakeven_locked)
        self.assertGreaterEqual(pos.stop_loss_price, pos.buy_price_sol)

    def test_pump_curve_math(self):
        """Verify constant product bonding curve calculation on Pump.fun."""
        from backend.services.pump_curve import PumpCurveCalculator
        # 0 SOL deposited = 0%
        c0 = PumpCurveCalculator.calculate_bonding_progress(0.0)
        self.assertEqual(c0["progress_pct"], 0.0)
        self.assertFalse(c0["is_graduating"])

        # 85 SOL deposited = 100% (Graduated)
        c85 = PumpCurveCalculator.calculate_bonding_progress(85.0)
        self.assertEqual(c85["progress_pct"], 100.0)
        self.assertTrue(c85["is_migrated"])

        # Estimate tokens out for 0.1 SOL buy
        tokens = PumpCurveCalculator.estimate_tokens_out(10.0, 0.1)
        self.assertGreater(tokens, 0)

    def test_jito_dynamic_tip_escalator(self):
        """Verify dynamic tip escalator scales up for high-confidence tokens and respects cap."""
        from backend.services.jito import JitoService
        jito = JitoService()

        # Grade A token (95/100) -> 1.8x
        tip_a = jito.calculate_dynamic_tip(base_tip_sol=0.001, safety_score=95, max_tip_sol=0.01)
        self.assertAlmostEqual(tip_a, 0.0018, places=4)

        # Grade B token (80/100) -> 1.3x
        tip_b = jito.calculate_dynamic_tip(base_tip_sol=0.001, safety_score=80, max_tip_sol=0.01)
        self.assertAlmostEqual(tip_b, 0.0013, places=4)

        # Hard cap protection test
        tip_capped = jito.calculate_dynamic_tip(base_tip_sol=0.01, safety_score=95, max_tip_sol=0.01)
        self.assertEqual(tip_capped, 0.01)

    async def test_lifetime_metrics_exceeding_100_trades(self):
        """Verify trade counter and cumulative lifetime PnL persist accurately past 100 trades."""
        from unittest.mock import AsyncMock
        engine = SniperEngine(self.config)
        engine.storage.save_trade = AsyncMock()
        engine.alerts.send_exit_alert = AsyncMock()

        # Simulate closing 120 trades with +0.05 SOL each
        for i in range(120):
            pos = Position(
                id=f"trade_{i}",
                token_mint=f"Mint_{i}",
                symbol=f"TKN{i}",
                name=f"Token {i}",
                mode=BotMode.PAPER,
                buy_order_id=f"buy_{i}",
                buy_price_sol=0.0001,
                buy_price_usd=0.018,
                sol_invested=0.05,
                tokens_held=500.0,
                current_price_sol=0.0002,
                current_price_usd=0.036,
                peak_price_sol=0.0002,
                take_profit_price=0.0002,
                stop_loss_price=0.00008,
            )
            engine.positions[pos.id] = pos
            await engine.close_position(pos.id, ExitReason.TAKE_PROFIT)

        metrics = engine.get_metrics()
        self.assertEqual(metrics["total_trades"], 120)
        self.assertGreater(metrics["total_trades"], 100)
        self.assertEqual(engine.lifetime_trades_count, 120)
        expected_pnl = 120 * (500.0 * 0.0002 * 0.985 - 0.05)
        self.assertAlmostEqual(metrics["total_pnl_sol"], expected_pnl, places=3)
        self.assertEqual(metrics["win_rate_pct"], 100.0)

        await engine.solana_client.close()
        await engine.security.close()
        await engine.jupiter.close()
        await engine.alerts.close()

    async def test_scale_out_pnl_aggregation(self):
        """Verify scale-out partial profits are summed into pos.pnl_sol and lifetime_pnl_sol."""
        from unittest.mock import AsyncMock
        engine = SniperEngine(self.config)
        engine.storage.save_trade = AsyncMock()
        engine.alerts.send_exit_alert = AsyncMock()

        # Pos where scale-out sold 50% for 0.04 SOL profit earlier
        pos = Position(
            id="scale_out_pos",
            token_mint="Mint_ScaleOut",
            symbol="SCALE",
            name="Scale Token",
            mode=BotMode.PAPER,
            buy_order_id="buy_scale",
            buy_price_sol=0.0001,
            buy_price_usd=0.018,
            sol_invested=0.025,  # remaining half invested
            tokens_held=250.0,
            current_price_sol=0.0002,  # final leg sells 250 * 0.0002 = 0.05 SOL -> profit 0.025 SOL
            current_price_usd=0.036,
            peak_price_sol=0.0002,
            take_profit_price=0.0002,
            stop_loss_price=0.00008,
            scale_out_triggered=True,
            realized_pnl_sol=0.04,  # First leg realized profit
        )
        engine.positions[pos.id] = pos
        await engine.close_position(pos.id, ExitReason.TAKE_PROFIT)

        closed = engine.closed_trades[0]
        # Total profit = 0.04 (TP1) + (250 tokens * 0.0002 price * 0.985 slippage - 0.025 cost)
        expected_pnl = 0.04 + (250.0 * 0.0002 * 0.985 - 0.025)
        self.assertAlmostEqual(closed.pnl_sol, expected_pnl, places=4)
        self.assertAlmostEqual(engine.lifetime_pnl_sol, expected_pnl, places=4)

        await engine.solana_client.close()
        await engine.security.close()
        await engine.jupiter.close()
        await engine.alerts.close()

    async def test_concurrency_slot_reservation(self):
        """Verify _in_flight_snipes and _snipe_lock prevent slot overflow when tokens arrive simultaneously."""
        from unittest.mock import AsyncMock
        engine = SniperEngine(self.config)
        engine.config.max_positions = 2

        # Mock trade_agent to focus purely on testing concurrency slot reservation
        engine.trade_agent.can_snipe = lambda token, count: (count < engine.config.max_positions, "Slot available" if count < engine.config.max_positions else "Max positions reached")
        engine.risk_agent.evaluate = AsyncMock(return_value=(True, "Safe"))
        
        async def slow_execute(token, amount):
            await asyncio.sleep(0.05)
            # simulate opening position
            pos = Position(
                id=f"pos_{token.mint}",
                token_mint=token.mint,
                symbol=token.symbol,
                name=token.name,
                mode=BotMode.PAPER,
                buy_order_id="buy",
                buy_price_sol=0.0001,
                buy_price_usd=0.018,
                sol_invested=0.05,
                tokens_held=500.0,
                current_price_sol=0.0001,
                current_price_usd=0.018,
                peak_price_sol=0.0001,
                take_profit_price=0.0002,
                stop_loss_price=0.00008,
            )
            engine.positions[pos.id] = pos

        engine._execute_snipe = AsyncMock(side_effect=slow_execute)

        tokens = [
            TokenData(mint=f"Mint_Concurrent_{i}", symbol=f"C{i}", name=f"Concurrent {i}", source=LaunchpadType.PUMPFUN)
            for i in range(5)
        ]

        # Dispatch 5 simultaneous discovery tasks
        await asyncio.gather(*[engine._process_token(t) for t in tokens])

        # Exactly 2 positions should be executed/open because max_positions is 2
        self.assertEqual(len(engine.positions), 2)

        await engine.solana_client.close()
        await engine.security.close()
        await engine.jupiter.close()
        await engine.alerts.close()

    async def test_solana_sign_transaction(self):
        """Verify SolanaClient signs VersionedTransactions correctly with solders Keypair."""
        import base64
        from solders.transaction import VersionedTransaction
        pub, priv = SolanaClient.generate_keypair()
        keypair = SolanaClient.load_keypair(priv)
        self.assertIsNotNone(keypair)

        # Create sample versioned transaction
        sample_tx = VersionedTransaction.default()
        raw_b64 = base64.b64encode(bytes(sample_tx)).decode("utf-8")

        signed_b64 = SolanaClient.sign_transaction(raw_b64, keypair)
        self.assertTrue(len(signed_b64) > 50)
        signed_bytes = base64.b64decode(signed_b64)
        deserialized = VersionedTransaction.from_bytes(signed_bytes)
        self.assertEqual(len(deserialized.signatures), 1)

    async def test_live_buy_insufficient_balance_rejected(self):
        """Verify live buy is rejected when wallet balance is below buy_amount + fees."""
        pub, priv = SolanaClient.generate_keypair()
        self.config.mode = BotMode.LIVE
        self.config.solana_private_key = priv
        self.executor.config = self.config

        # Mock balance to 0.01 SOL (less than 0.05 + 0.005)
        self.solana_client.get_balance = AsyncMock(return_value=0.01)

        token = TokenData(
            mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            symbol="BONK",
            name="Bonk",
            source=LaunchpadType.PUMPFUN,
            price_sol=0.00000005,
        )

        order, err = await self.executor.execute_buy(token, 0.05)
        self.assertIsNotNone(err)
        self.assertIn("Insufficient live wallet balance", err)
        self.assertEqual(order.status, OrderStatus.FAILED)


if __name__ == "__main__":
    unittest.main()


