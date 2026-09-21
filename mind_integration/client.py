"""
Mind Integration Client — connessione opzionale alla cognitive API di PramaIA-Mind.

Abilitare con USE_MIND_INTEGRATION=true in .env.
"""
import os
import httpx
import logging

logger = logging.getLogger("PramaIA-PrivacyManager.mind")

MIND_URL = os.getenv("MIND_URL", "http://localhost:8100")
USE_MIND = os.getenv("USE_MIND_INTEGRATION", "false").lower() == "true"


class MindClient:
    """
    Client HTTP per comunicare con PramaIA-Mind.
    Usare come dipendenza FastAPI o istanza singleton.
    """

    def __init__(self, base_url: str = MIND_URL):
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if not USE_MIND:
            logger.info("Mind integration disabled (USE_MIND_INTEGRATION=false)")
            return
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        logger.info(f"MindClient connected to {self.base_url}")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    def is_available(self) -> bool:
        return USE_MIND and self._client is not None

    async def chat(self, user_id: str, message: str, session_id: str | None = None) -> dict:
        """Invia un messaggio alla chat cognitiva di Mind."""
        if not self.is_available():
            raise RuntimeError("Mind integration is not enabled")
        response = await self._client.post(
            "/api/mind/chat",
            json={"user_id": user_id, "message": message, "session_id": session_id},
        )
        response.raise_for_status()
        return response.json()

    async def health(self) -> bool:
        """Verifica che Mind sia raggiungibile."""
        if not self._client:
            return False
        try:
            r = await self._client.get("/api/health", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False


# Singleton — importare e usare ovunque nel backend
mind_client = MindClient()
