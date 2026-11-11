import unittest
from fastapi.testclient import TestClient
from backend.main import app


class TestMaguraBotAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_status_endpoint(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("mode", data)
        self.assertIn("paper_balance_sol", data)
        self.assertIn("win_rate_pct", data)

    def test_config_get_and_update(self):
        # 1. Get config
        res = self.client.get("/api/config")
        self.assertEqual(res.status_code, 200)
        cfg = res.json()
        self.assertEqual(cfg["mode"], "paper")

        # 2. Update config
        update_res = self.client.post("/api/config", json={"buy_amount_sol": 0.08, "take_profit_pct": 75.0})
        self.assertEqual(update_res.status_code, 200)
        updated = update_res.json()["config"]
        self.assertEqual(updated["buy_amount_sol"], 0.08)
        self.assertEqual(updated["take_profit_pct"], 75.0)

    def test_positions_and_trades_endpoints(self):
        res_pos = self.client.get("/api/positions")
        self.assertEqual(res_pos.status_code, 200)
        self.assertIsInstance(res_pos.json(), list)

        res_trades = self.client.get("/api/trades")
        self.assertEqual(res_trades.status_code, 200)
        self.assertIsInstance(res_trades.json(), list)

    def test_dashboard_html_served(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("MAGURABOT", res.text)
        self.assertIn("Solana Memecoin Sniper", res.text)

    def test_wallet_and_analytics_endpoints(self):
        # Wallet info
        res_wallet = self.client.get("/api/wallet/info")
        self.assertEqual(res_wallet.status_code, 200)
        self.assertIn("balance_sol", res_wallet.json())

        # Generate wallet
        res_gen = self.client.post("/api/wallet/generate")
        self.assertEqual(res_gen.status_code, 200)
        data = res_gen.json()
        self.assertTrue(len(data["public_key"]) > 30)
        self.assertTrue(len(data["private_key"]) > 30)

        # Analytics
        res_analytics = self.client.get("/api/analytics")
        self.assertEqual(res_analytics.status_code, 200)
        self.assertIn("win_rate_pct", res_analytics.json())
        self.assertIn("total_realized_pnl_sol", res_analytics.json())

    def test_network_latency_endpoint(self):
        res = self.client.get("/api/network/latency")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("latency_ms", data)
        self.assertIn("rpc_url", data)

    def test_token_audit_endpoint(self):
        res = self.client.get("/api/tokens/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263/audit")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("score", data)
        self.assertIn("links", data)
        self.assertIn("solscan", data["links"])
        self.assertIn("dexscreener", data["links"])

    def test_backtest_endpoint(self):
        payload = {
            "initial_balance_sol": 10.0,
            "buy_amount_sol": 0.1,
            "take_profit_pct": 50.0,
            "stop_loss_pct": 20.0,
            "trailing_stop_pct": 15.0,
            "enable_scale_out": True,
            "simulated_token_count": 20,
        }
        res = self.client.post("/api/backtest/run", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("total_trades", data)
        self.assertIn("win_rate_pct", data)
        self.assertIn("equity_curve", data)
        self.assertIn("net_pnl_sol", data)
        self.assertTrue(len(data["equity_curve"]) > 0)


    def test_trades_export_endpoint(self):
        res = self.client.get("/api/trades/export")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn("magurabot_trades.csv", res.headers.get("content-disposition", ""))
        self.assertIn("Symbol", res.text)
        self.assertIn("Realized_PnL_SOL", res.text)


    def test_rpc_pool_endpoint(self):
        res = self.client.get("/api/rpc/pool")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("primary", data)
        self.assertIn("endpoints", data)
        self.assertTrue(len(data["endpoints"]) > 0)

    def test_pump_curve_endpoint(self):
        res = self.client.get("/api/pump/curve/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263?deposited_sol=42.5")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("progress_pct", data)
        self.assertAlmostEqual(data["progress_pct"], 50.0, places=1)
        self.assertIn("sol_needed_to_graduate", data)
        self.assertFalse(data["is_migrated"])

    def test_token_holders_endpoint(self):
        res = self.client.get("/api/tokens/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263/holders")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)
        self.assertIn("top_holders", data)


if __name__ == "__main__":
    unittest.main()
