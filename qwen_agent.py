"""Interactive World Cup Agent backed by Alibaba Cloud Model Studio Qwen.

Run this file directly in PyCharm after configuring DASHSCOPE_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agent_config import AgentConfig
from world_cup_agent_tools import TOOL_SCHEMAS, execute_tool, get_default_tools


SYSTEM_PROMPT = """你是一个世界杯冠军预测 Agent。请默认使用中文回答。
工作规则：
1. 涉及赛程、球队数据、比分、晋级概率或冠军概率时，必须优先调用工具，不得凭记忆编造。
2. 清楚区分已经结束的真实比赛与尚未进行的模型预测。
3. 百分比、比分和排名必须忠实引用工具结果，不得擅自修改。
4. 解释预测时说明主要数据依据，同时明确模型局限；不要把概率描述为确定事实。
5. 回答保持清晰简洁。若工具返回错误，说明缺少的信息，并建议用户修正球队名称或刷新数据。
6. 当前预测模型主要使用 FIFA 积分、历史 Elo、时间衰减历史表现和 2026 赛事状态。"""


class QwenWorldCupAgent:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig.from_environment()
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "缺少 openai 依赖。请先执行："
                "python -m pip install -r requirements-web.txt"
            ) from exc

        self.client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=2,
        )
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def clear(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def _completion(self) -> Any:
        return self.client.chat.completions.create(
            model=self.config.model,
            messages=self.messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            parallel_tool_calls=True,
            extra_body={"enable_thinking": self.config.enable_thinking},
        )

    @staticmethod
    def _assistant_message_dict(message: Any) -> dict[str, Any]:
        if hasattr(message, "model_dump"):
            return message.model_dump(exclude_none=True)
        raise TypeError("Unsupported assistant message returned by the SDK")

    def ask(self, user_message: str) -> str:
        user_message = user_message.strip()
        if not user_message:
            raise ValueError("用户消息不能为空")
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(self.config.max_tool_rounds):
            completion = self._completion()
            if not completion.choices:
                raise RuntimeError("模型没有返回候选答案")
            assistant_message = completion.choices[0].message
            self.messages.append(self._assistant_message_dict(assistant_message))
            tool_calls = assistant_message.tool_calls or []

            if not tool_calls:
                content = assistant_message.content or ""
                if not content.strip():
                    raise RuntimeError("模型既没有返回文本，也没有返回工具调用")
                return content.strip()

            for tool_call in tool_calls:
                tool_result = execute_tool(
                    tool_call.function.name,
                    tool_call.function.arguments,
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            tool_result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )

        raise RuntimeError(
            f"Agent 超过 {self.config.max_tool_rounds} 轮工具调用。"
            "请换一个更具体的问题。"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alibaba Cloud Qwen World Cup Agent")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check local data and environment without calling the model API.",
    )
    return parser.parse_args()


def offline_check() -> int:
    config = AgentConfig.from_environment(require_api_key=False)
    tools = get_default_tools()
    print("OFFLINE CHECK PASSED")
    print(json.dumps(config.safe_summary(), ensure_ascii=False, indent=2))
    print(json.dumps(tools.health_check(), ensure_ascii=False, indent=2))
    print(f"registered_tools: {len(TOOL_SCHEMAS)}")
    if not config.api_key:
        print("warning: DASHSCOPE_API_KEY is not configured")
    return 0


def interactive_chat() -> int:
    agent = QwenWorldCupAgent()
    tools = get_default_tools()
    print("世界杯预测 Agent 已启动")
    print(
        f"模型: {agent.config.model} | 数据快照: {tools.snapshot_id} | "
        "输入 /help 查看命令"
    )

    while True:
        try:
            user_input = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n会话结束。")
            return 0

        if not user_input:
            continue
        command = user_input.lower()
        if command in {"/quit", "/exit"}:
            print("会话结束。")
            return 0
        if command == "/clear":
            agent.clear()
            print("对话上下文已清空。")
            continue
        if command == "/health":
            print(json.dumps(tools.health_check(), ensure_ascii=False, indent=2))
            continue
        if command == "/help":
            print("/health 查看数据状态 | /clear 清空对话 | /quit 退出")
            continue

        try:
            answer = agent.ask(user_input)
            print(f"\nAgent：{answer}")
        except Exception as exc:
            print(f"\n调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    return offline_check() if args.check else interactive_chat()


if __name__ == "__main__":
    raise SystemExit(main())
