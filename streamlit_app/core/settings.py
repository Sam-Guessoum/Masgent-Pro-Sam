"""Settings (``settings.json``) and secrets (``.env``) handling.

Rules enforced here:

* Secrets live only in ``streamlit_app/.env`` (or the process environment).
* They are never written to settings, run folders, logs or result JSON.
* ``mask()`` is the only way secrets are shown in the UI.
* ``redact()`` scrubs any known secret value out of arbitrary text.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.paths import config_dir, default_workspace

#: Exact environment-variable names read by Masgent (confirmed from source).
SECRET_KEYS: dict[str, str] = {
    "MP_API_KEY": "Materials Project",
    "OPENAI_API_KEY": "OpenAI (GPT-5 Nano)",
    "ANTHROPIC_API_KEY": "Anthropic (Claude Sonnet 4.5)",
    "GOOGLE_API_KEY": "Google (Gemini 2.5 Flash)",
    "GROK_API_KEY": "xAI (Grok 4.1 Fast)",
    "DEEPSEEK_API_KEY": "DeepSeek (DeepSeek Chat)",
    "DASHSCOPE_API_KEY": "Alibaba Cloud (Qwen Flash)",
}

#: Parameter names that must never be persisted in inputs/results.
SECRET_PARAM_HINTS = ("api_key", "apikey", "token", "secret", "password")

_lock = threading.RLock()


def env_path() -> Path:
    return config_dir() / ".env"


def settings_path() -> Path:
    return config_dir() / "settings.json"


# --------------------------------------------------------------------- secrets
def read_env_file() -> dict[str, str]:
    p = env_path()
    if not p.exists():
        return {}
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(p).items() if v is not None}
    except Exception:  # pragma: no cover - dotenv missing/corrupt
        out: dict[str, str] = {}
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out


def get_secret(name: str) -> str | None:
    """Value from ``.env`` first, then from the process environment."""
    val = read_env_file().get(name) or os.environ.get(name)
    return val.strip() if val and val.strip() else None


def set_secret(name: str, value: str | None) -> None:
    """Create/update/remove one key in ``.env`` (atomic rewrite)."""
    if name not in SECRET_KEYS and not name.endswith("_API_KEY"):
        raise ValueError(f"Not a recognised secret name: {name}")
    with _lock:
        p = env_path()
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        out, found = [], False
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else None
            if key == name:
                found = True
                if value:
                    out.append(f"{name}={value.strip()}")
                continue
            out.append(line)
        if value and not found:
            out.append(f"{name}={value.strip()}")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        os.replace(tmp, p)


def all_secret_values() -> list[str]:
    vals = []
    env = read_env_file()
    for k in SECRET_KEYS:
        for v in (env.get(k), os.environ.get(k)):
            if v and len(v.strip()) >= 6:
                vals.append(v.strip())
    return sorted(set(vals), key=len, reverse=True)


def mask(value: str | None) -> str:
    """Display form of a secret: never more than the last 4 characters."""
    if not value:
        return "not set"
    v = value.strip()
    if len(v) <= 8:
        return "•" * 8
    return "•" * 8 + v[-4:]


def redact(text: Any, extra: list[str] | None = None) -> Any:
    """Replace every known secret value inside ``text`` with ``***``."""
    if not isinstance(text, str) or not text:
        return text
    for v in all_secret_values() + list(extra or []):
        if v and v in text:
            text = text.replace(v, "***")
    return text


def scrub_params(obj: Any) -> Any:
    """Recursively drop secret-like keys and redact secret values."""
    if isinstance(obj, dict):
        return {
            k: ("***" if any(h in str(k).lower() for h in SECRET_PARAM_HINTS) else scrub_params(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [scrub_params(v) for v in obj]
    return redact(obj)


# -------------------------------------------------------------------- settings
@dataclass
class Settings:
    workspace: str = ""
    default_project: str = "default"
    device: str = "auto"  # auto | cpu | cuda
    max_concurrent: int = 1
    psp_dir: str = ""  # PMG_VASP_PSP_DIR (licensed POTCAR library), optional
    seed: int = 42
    upload_limit_mb: int = 200
    mp_cache_days: int = 30
    onboarding_done: bool = False
    ai_provider: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace).resolve() if self.workspace else default_workspace()


def load_settings() -> Settings:
    with _lock:
        p = settings_path()
        data: dict[str, Any] = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        known = {f for f in Settings.__dataclass_fields__}
        s = Settings(**{k: v for k, v in data.items() if k in known})
        if os.environ.get("MASGENT_UI_WORKSPACE"):
            s.workspace = os.environ["MASGENT_UI_WORKSPACE"]
        return s


def save_settings(s: Settings) -> None:
    with _lock:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = scrub_params(asdict(s))  # settings never hold secrets
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)


def update_settings(**changes: Any) -> Settings:
    s = load_settings()
    for k, v in changes.items():
        if hasattr(s, k):
            setattr(s, k, v)
    save_settings(s)
    return s
