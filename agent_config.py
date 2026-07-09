"""Environment-based configuration for the Alibaba Cloud Qwen Agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.6-plus"
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "agent_settings.json"
ENV_FILE = BASE_DIR / ".env"
ALLOWED_SETTINGS = {
    "base_url",
    "model",
    "enable_thinking",
    "max_tool_rounds",
    "timeout_seconds",
}


def load_local_env_file(path: Path = ENV_FILE) -> None:
    """Load simple KEY=VALUE entries from a same-directory .env file.

    Existing environment variables win, so PyCharm/ECS settings can override the
    local file. This keeps secrets out of source code while making local runs
    easy: create .env next to web_app.py and add DASHSCOPE_API_KEY=...
    """
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            raise ValueError(f"Invalid .env line {line_number}: empty key")
        os.environ.setdefault(key, value)


def parse_bool(value: Any, variable_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{variable_name} must be true/false, 1/0, yes/no, or on/off"
    )


def parse_positive_integer(value: Any, variable_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{variable_name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{variable_name} must be positive")
    return parsed


def load_settings_file(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if "api_key" in settings or "DASHSCOPE_API_KEY" in settings:
        raise ValueError(
            "Do not store API keys in agent_settings.json. "
            "Use the DASHSCOPE_API_KEY environment variable."
        )
    unknown = sorted(set(settings) - ALLOWED_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown settings in {path}: {', '.join(unknown)}")
    return settings


def environment_or_file(
    environment_name: str,
    settings: dict[str, Any],
    file_name: str,
    default: Any,
) -> Any:
    environment_value = os.getenv(environment_name)
    return environment_value if environment_value is not None else settings.get(file_name, default)


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    enable_thinking: bool = False
    max_tool_rounds: int = 8
    timeout_seconds: int = 120

    @classmethod
    def from_environment(cls, require_api_key: bool = True) -> "AgentConfig":
        load_local_env_file()
        settings = load_settings_file()
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if require_api_key and not api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is not configured. Create a new Alibaba Cloud "
                "Model Studio API key and add it to the PyCharm run environment."
            )
        if api_key.lower() in {"your_api_key", "replace_me", "sk-xxx"}:
            raise RuntimeError("DASHSCOPE_API_KEY still contains a placeholder value")

        base_url = str(
            environment_or_file(
                "DASHSCOPE_BASE_URL", settings, "base_url", DEFAULT_BASE_URL
            )
        ).strip().rstrip("/")
        model = str(
            environment_or_file("DASHSCOPE_MODEL", settings, "model", DEFAULT_MODEL)
        ).strip()
        if not base_url.startswith("https://"):
            raise ValueError("DASHSCOPE_BASE_URL must use https://")
        if not model:
            raise ValueError("DASHSCOPE_MODEL cannot be empty")

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            enable_thinking=parse_bool(
                environment_or_file(
                    "DASHSCOPE_ENABLE_THINKING",
                    settings,
                    "enable_thinking",
                    False,
                ),
                "DASHSCOPE_ENABLE_THINKING",
            ),
            max_tool_rounds=parse_positive_integer(
                environment_or_file(
                    "AGENT_MAX_TOOL_ROUNDS", settings, "max_tool_rounds", 8
                ),
                "AGENT_MAX_TOOL_ROUNDS",
            ),
            timeout_seconds=parse_positive_integer(
                environment_or_file(
                    "DASHSCOPE_TIMEOUT_SECONDS", settings, "timeout_seconds", 120
                ),
                "DASHSCOPE_TIMEOUT_SECONDS",
            ),
        )

    def safe_summary(self) -> dict[str, object]:
        """Return diagnostics that never expose the API key."""
        return {
            "api_key_configured": bool(self.api_key),
            "base_url": self.base_url,
            "model": self.model,
            "enable_thinking": self.enable_thinking,
            "max_tool_rounds": self.max_tool_rounds,
            "timeout_seconds": self.timeout_seconds,
            "settings_file": str(SETTINGS_FILE),
        }
