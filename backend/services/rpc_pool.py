import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
import aiohttp

logger = logging.getLogger("MaguraBot.RPCPool")

DEFAULT_RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-mainnet.rpc.extrnode.com",
    "https://rpc.ankr.com/solana",
]


class RPCEndpoint:
    def __init__(self, url: str):
        self.url = url
        self.latency_ms: float = 999.0
        self.is_healthy: bool = True
        self.error_count: int = 0
        self.last_checked: float = 0.0

    def mark_success(self, latency_ms: float):
        self.latency_ms = round(latency_ms, 1)
        self.is_healthy = True
        self.error_count = max(0, self.error_count - 1)
        self.last_checked = time.time()

    def mark_failure(self):
        self.error_count += 1
        if self.error_count >= 3:
            self.is_healthy = False
        self.last_checked = time.time()


class RPCPoolService:
    """
    Manages a pool of Solana RPC endpoints with automatic latency benchmarking,
    health tracking, and failover routing to prevent 429 rate limit drops during snipes.
    """

    def __init__(self, custom_endpoints: Optional[List[str]] = None):
        urls = list(dict.fromkeys((custom_endpoints or []) + DEFAULT_RPC_ENDPOINTS))
        self.endpoints = [RPCEndpoint(url) for url in urls if url.startswith("http")]
        self._session: Optional[aiohttp.ClientSession] = None
        self._benchmark_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4))
        await self.benchmark_all()
        self._benchmark_task = asyncio.create_task(self._periodic_benchmark())
        logger.info(f"RPCPool initialized with {len(self.endpoints)} endpoints. Active: {self.get_primary_endpoint()}")

    async def stop(self):
        self._running = False
        if self._benchmark_task:
            self._benchmark_task.cancel()
            try:
                await self._benchmark_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("RPCPool stopped")

    def get_primary_endpoint(self) -> str:
        healthy = [ep for ep in self.endpoints if ep.is_healthy]
        if not healthy:
            return self.endpoints[0].url if self.endpoints else "https://api.mainnet-beta.solana.com"
        best = min(healthy, key=lambda ep: ep.latency_ms)
        return best.url

    def get_pool_status(self) -> List[Dict[str, Any]]:
        return [
            {
                "url": ep.url,
                "latency_ms": ep.latency_ms,
                "is_healthy": ep.is_healthy,
                "error_count": ep.error_count,
                "is_primary": ep.url == self.get_primary_endpoint(),
            }
            for ep in self.endpoints
        ]

    async def benchmark_endpoint(self, ep: RPCEndpoint) -> float:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4))
        start = time.perf_counter()
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
        try:
            async with self._session.post(ep.url, json=payload) as resp:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                if resp.status == 200:
                    ep.mark_success(elapsed_ms)
                    return elapsed_ms
                else:
                    ep.mark_failure()
                    return 999.0
        except Exception:
            ep.mark_failure()
            return 999.0

    async def benchmark_all(self):
        tasks = [self.benchmark_endpoint(ep) for ep in self.endpoints]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _periodic_benchmark(self):
        while self._running:
            try:
                await asyncio.sleep(45)
                await self.benchmark_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"RPC benchmark error: {e}")
