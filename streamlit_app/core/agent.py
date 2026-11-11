"""AI assistant: Masgent's pydantic-ai agent rebuilt for the web UI.

``masgent.ai_mode.ai_backend`` cannot be imported on Windows (``bullet`` needs
``termios``, KNOWN_ISSUES K06), so this module recreates the same agent from
public pieces: the same system prompt (``utils.load_system_prompts``), the same
tool functions, the same 20-message history window and the same provider/model
names. Differences (all deliberate):

* Every tool call becomes a normal **Run** (worker process, saved in the library).
* A hard **approval gate**: tools that change files only execute after the user
  approved the plan (button or an explicit "yes/proceed" message). Read-only
  tools (list/read files) are always allowed.
* All 36 tools are registered (Masgent registers 35, K07).
* Conversations are logged to ``conversation.jsonl`` (Masgent's logger fails on Windows, K08).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import functools
import inspect
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.paths import RUNS_DIR_PLACEHOLDER, ensure_masgent_importable
from core.settings import get_secret, redact

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

PROVIDERS: dict[str, dict[str, Any]] = {
    "Masgent AI (hosted, no key)": {"key": None, "model": "gpt-5-nano", "note": "Sends prompts to the Masgent team's server (masgent-ai.onrender.com). First reply can take ~1 minute (cold start)."},
    "OpenAI · GPT-5 Nano": {"key": "OPENAI_API_KEY", "model": "gpt-5-nano"},
    "Anthropic · Claude Sonnet 4.5": {"key": "ANTHROPIC_API_KEY", "model": "claude-sonnet-4-5"},
    "Google · Gemini 2.5 Flash": {"key": "GOOGLE_API_KEY", "model": "gemini-2.5-flash"},
    "xAI · Grok 4.1 Fast": {"key": "GROK_API_KEY", "model": "grok-4-1-fast-non-reasoning"},
    "DeepSeek · DeepSeek Chat": {"key": "DEEPSEEK_API_KEY", "model": "deepseek-chat"},
    "Alibaba · Qwen Flash": {"key": "DASHSCOPE_API_KEY", "model": "qwen-flash"},
}

READ_ONLY_TOOLS = {"list_files", "read_file"}
_APPROVE_RE = re.compile(r"^\s*(yes|y|yep|ok|okay|sure|proceed|go ahead|go|confirm(ed)?|approve(d)?|do it|run it|execute|looks good|lgtm)\b", re.I)
_PLAN_RE = re.compile(r"\b(plan|confirm|proceed|approve|shall i|should i|would you like)\b", re.I)


def build_model(provider: str):  # noqa: ANN201
    spec = PROVIDERS[provider]
    key = get_secret(spec["key"]) if spec["key"] else None
    if spec["key"] and not key:
        raise RuntimeError(f"{provider} needs {spec['key']}: add it in Settings → AI providers.")
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    if provider.startswith("Masgent"):
        return OpenAIChatModel(spec["model"], provider=OpenAIProvider(base_url="https://masgent-ai.onrender.com/v1",
                                                                      api_key=get_secret("OPENAI_API_KEY") or "not-needed"))
    if provider.startswith("OpenAI"):
        return OpenAIChatModel(spec["model"], provider=OpenAIProvider(api_key=key))
    if provider.startswith("Anthropic"):
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(spec["model"], provider=AnthropicProvider(api_key=key))
    if provider.startswith("Google"):
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        return GoogleModel(spec["model"], provider=GoogleProvider(api_key=key))
    if provider.startswith("xAI"):
        # Masgent imports pydantic_ai.providers.grok, which no longer exists (K22); xAI is OpenAI-compatible.
        return OpenAIChatModel(spec["model"], provider=OpenAIProvider(base_url="https://api.x.ai/v1", api_key=key))
    if provider.startswith("DeepSeek"):
        from pydantic_ai.providers.deepseek import DeepSeekProvider

        return OpenAIChatModel(spec["model"], provider=DeepSeekProvider(api_key=key))
    if provider.startswith("Alibaba"):
        from pydantic_ai.providers.alibaba import AlibabaProvider

        return OpenAIChatModel(spec["model"], provider=AlibabaProvider(api_key=key))
    raise ValueError(provider)


def _replace_token(obj: Any, token: str, value: str) -> Any:
    if isinstance(obj, str):
        return obj.replace(token, value)
    if isinstance(obj, dict):
        return {k: _replace_token(v, token, value) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_token(v, token, value) for v in obj]
    return obj


async def keep_recent_messages(messages: list) -> list:
    """Same 20-message window as Masgent's ``ai_backend.keep_recent_messages``."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, ToolCallPart, ToolReturnPart

    window = 20
    if len(messages) <= window:
        return messages
    sys_msg, sys_idx = None, None
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest) and any(isinstance(p, SystemPromptPart) for p in msg.parts):
            sys_msg, sys_idx = msg, i
            break
    for cut in range(len(messages) - window, -1, -1):
        first = messages[cut]
        if any(isinstance(p, ToolReturnPart) for p in first.parts):
            continue
        if isinstance(first, ModelResponse) and any(isinstance(p, ToolCallPart) for p in first.parts):
            continue
        res = messages[cut:]
        if sys_msg is not None and sys_idx is not None and cut > sys_idx:
            res = [sys_msg] + res
        return res
    return messages


@dataclass
class Turn:
    role: str  # user | assistant | system
    text: str = ""
    tool_runs: list[dict] = field(default_factory=list)  # {run_id, tool, params, status}
    ts: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    error: str | None = None
    plan: bool = False


class ChatSession:
    """One conversation. Agent turns run in a background thread; the UI polls."""

    def __init__(self, manager, storage, project: str, provider: str, model=None, tool_timeout: float = 6 * 3600):  # noqa: ANN001
        self.id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.manager, self.storage, self.project, self.provider = manager, storage, project, provider
        self.dir = storage.project_dir(project) / "chats" / self.id
        self.session_dir = self.dir / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.turns: list[Turn] = []
        self.history: list = []
        self.approved = False
        self.busy = False
        self.cancel_requested = False
        self._lock = threading.RLock()
        self._model = model
        self._agent = None
        self.tool_timeout = tool_timeout
        self._log({"event": "session_start", "provider": provider, "project": project})

    # ---------------------------------------------------------------- agent
    def agent(self):  # noqa: ANN201
        if self._agent is None:
            ensure_masgent_importable()
            os.environ.setdefault("MASGENT_SESSION_RUNS_DIR", RUNS_DIR_PLACEHOLDER)
            from pydantic_ai import Agent

            from masgent.utils import tools as mtools
            from masgent.utils.utils import load_system_prompts

            from core.registry import discover_tools

            wrapped = [self._wrap(name, getattr(mtools, name)) for name in discover_tools()]
            prompt = load_system_prompts() + (
                "\n\nUI NOTE: The user approves plans with an Approve button in the interface; a tool may answer "
                "'not approved yet' if you call it before approval. Paths starting with {SESSION} refer to the current session folder."
            )
            model = self._model or build_model(self.provider)
            try:  # pydantic-ai >= 2: history processors are a capability (Masgent's kwarg was removed, K24)
                from pydantic_ai.capabilities import ProcessHistory

                self._agent = Agent(model=model, system_prompt=prompt, tools=wrapped,
                                    capabilities=[ProcessHistory(keep_recent_messages)])
            except ImportError:
                self._agent = Agent(model=model, system_prompt=prompt, tools=wrapped, history_processors=[keep_recent_messages])
        return self._agent

    def _wrap(self, name: str, func):  # noqa: ANN001, ANN202
        sig = inspect.signature(func)
        session = self

        @functools.wraps(func)
        def tool(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            if session.cancel_requested:
                return {"status": "error", "message": "The user cancelled this request."}
            if name not in READ_ONLY_TOOLS and not session.approved:
                return {"status": "error", "message": "Not approved yet. Present the complete PLAN and wait for the user to approve it."}
            params = dict(sig.bind_partial(*args, **kwargs).arguments)
            params = _replace_token(params, "{SESSION}", session.session_dir.as_posix())
            entry = {"run_id": None, "tool": name, "params": params, "status": "queued"}
            with session._lock:
                session.turns[-1].tool_runs.append(entry)
            try:
                rid = session.manager.submit(name, params, project=session.project, origin="ai",
                                             session_dir=session.session_dir, label=f"AI · {name}")
            except Exception as e:  # noqa: BLE001
                entry["status"] = "failed"
                return {"status": "error", "message": f"Could not start: {e}"}
            entry["run_id"] = rid
            meta = session.manager.wait(rid, timeout=session.tool_timeout) or {}
            entry["status"] = meta.get("status", "unknown")
            rec = session.storage.get_run(rid)
            result = (rec.result() if rec else None) or {"status": "error", "message": meta.get("error") or "No result."}
            result = _replace_token(json.loads(json.dumps(result, default=str)), "{SESSION}", session.session_dir.as_posix())
            result.pop("traceback", None)
            result["run_id"] = rid
            session._log({"event": "tool", "tool": name, "run_id": rid, "status": entry["status"]})
            return result

        tool.__signature__ = sig  # type: ignore[attr-defined]
        return tool

    # ---------------------------------------------------------------- turns
    def submit(self, text: str, approve: bool = False) -> None:
        if self.busy:
            raise RuntimeError("Wait for the current answer to finish.")
        if approve or _APPROVE_RE.match(text):
            self.approved = True
        with self._lock:
            self.turns.append(Turn("user", text))
            self.turns.append(Turn("assistant"))
        self.busy, self.cancel_requested = True, False
        self._log({"event": "user", "text": redact(text), "approved": self.approved})
        threading.Thread(target=self._run_turn, args=(text,), daemon=True, name=f"chat-{self.id}").start()

    def _run_turn(self, text: str) -> None:
        turn = self.turns[-1]
        try:
            asyncio.run(self._stream(text, turn))
        except Exception as e:  # noqa: BLE001
            turn.error = redact(f"{type(e).__name__}: {e}")
        finally:
            ran_tools = any(t["tool"] not in READ_ONLY_TOOLS for t in turn.tool_runs)
            if ran_tools:
                self.approved = False  # every new plan needs a new approval
            turn.plan = bool(not turn.tool_runs and not turn.error and _PLAN_RE.search(turn.text or ""))
            self.busy = False
            self._log({"event": "assistant", "text": redact(turn.text), "tools": [t.get("run_id") for t in turn.tool_runs],
                       "error": turn.error})

    async def _stream(self, text: str, turn: Turn) -> None:
        from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

        agent = self.agent()
        async with agent.run_stream_events(text, message_history=self.history) as events:
            async for ev in events:
                if self.cancel_requested:
                    break
                if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart) and ev.part.content:
                    turn.text += ev.part.content
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                    turn.text += ev.delta.content_delta
                elif type(ev).__name__ == "AgentRunResultEvent":
                    self.history = list(ev.result.all_messages())

    def cancel(self) -> None:
        self.cancel_requested = True
        for t in self.turns[-1].tool_runs if self.turns else []:
            if t.get("run_id") and t.get("status") in ("queued", "running"):
                self.manager.cancel(t["run_id"])

    def _log(self, obj: dict) -> None:
        obj["ts"] = dt.datetime.now().isoformat(timespec="seconds")
        with open(self.dir / "conversation.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
