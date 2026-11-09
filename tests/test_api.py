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


if __name__ == "__main__":
    unittest.main()
