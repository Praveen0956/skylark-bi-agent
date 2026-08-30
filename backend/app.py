"""
app.py
FastAPI entrypoint: serves the chat frontend and exposes the agent as an API.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import ask_agent

app = FastAPI(title="Skylark BI Agent")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

REQUIRED_ENV_VARS = [
    "MONDAY_API_TOKEN",
    "WORK_ORDERS_BOARD_ID",
    "DEALS_BOARD_ID",
    "ANTHROPIC_API_KEY",
]


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None


@app.get("/api/health")
def health():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    return JSONResponse(
        {
            "status": "ok" if not missing else "missing_env_vars",
            "missing_env_vars": missing,
        }
    )


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        result = ask_agent(req.message, req.history)
        return {"reply": result["reply"], "history": result["messages"]}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
