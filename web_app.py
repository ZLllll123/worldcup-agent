"""FastAPI web application for the World Cup prediction Agent.

Run this file directly in PyCharm for local testing.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_config import AgentConfig
from qwen_agent import QwenWorldCupAgent
from world_cup_agent_tools import get_default_tools


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 200
CHAT_REQUESTS_PER_MINUTE = 15


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


class PredictMatchRequest(BaseModel):
    team_a: str = Field(min_length=1, max_length=80)
    team_b: str = Field(min_length=1, max_length=80)


@dataclass
class AgentSession:
    agent: QwenWorldCupAgent
    last_access: float
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()

    def _cleanup_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_access > SESSION_TTL_SECONDS
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        if len(self._sessions) >= MAX_SESSIONS:
            oldest = min(
                self._sessions,
                key=lambda session_id: self._sessions[session_id].last_access,
            )
            self._sessions.pop(oldest, None)

    def get_or_create(self, session_id: str | None) -> tuple[str, AgentSession]:
        now = time.monotonic()
        with self._lock:
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_access = now
                return session_id, session
            self._cleanup_locked(now)
            new_id = uuid.uuid4().hex
            session = AgentSession(agent=QwenWorldCupAgent(), last_access=now)
            self._sessions[new_id] = session
            return new_id, session


class RateLimiter:
    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            requests = self._requests[client_ip]
            while requests and requests[0] < cutoff:
                requests.popleft()
            if len(requests) >= CHAT_REQUESTS_PER_MINUTE:
                return False
            requests.append(now)
            return True


app = FastAPI(
    title="World Cup Prediction Agent",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
tools = get_default_tools()
sessions = SessionStore()
rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, object]:
    config = AgentConfig.from_environment(require_api_key=False)
    return {
        "status": "ok",
        "agent": "world-cup-prediction-agent",
        "model": config.model,
        "api_key_configured": bool(config.api_key),
        **tools.health_check(),
    }


@app.get("/api/teams")
def teams() -> dict[str, object]:
    names = sorted(tools.features)
    return {"snapshot_id": tools.snapshot_id, "teams": names}


@app.get("/api/probabilities")
def probabilities(limit: int = 8) -> dict[str, object]:
    try:
        return tools.get_champion_probabilities(limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/bracket")
def bracket() -> dict[str, object]:
    return tools.get_bracket_prediction()


@app.post("/api/predict-match")
def predict_match(payload: PredictMatchRequest) -> dict[str, object]:
    try:
        return tools.predict_match(payload.team_a, payload.team_b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> dict[str, object]:
    if not rate_limiter.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试。")
    try:
        config = AgentConfig.from_environment()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        session_id, session = sessions.get_or_create(payload.session_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    def run_agent() -> str:
        with session.lock:
            session.last_access = time.monotonic()
            if len(session.agent.messages) > 60:
                session.agent.clear()
            return session.agent.ask(payload.message)

    try:
        answer = await run_in_threadpool(run_agent)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用，请稍后重试。",
        ) from exc
    return {
        "session_id": session_id,
        "answer": answer,
        "snapshot_id": tools.snapshot_id,
        "model": config.model,
    }


def main() -> int:
    """Start the web service directly from PyCharm."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Web 依赖。请先在当前 PyCharm 解释器中执行："
            "python -m pip install -r requirements-web.txt"
        ) from exc

    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    print(f"World Cup Agent Web 服务启动中：http://{host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        proxy_headers=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
