"""Local slash-command dispatcher for the Feishu adapter.

Intercepts messages that start with ``/`` and matches them against a static
command table. Matched commands run synchronously and skip the full agent
loop, which is dramatically faster (<1s vs 5-10s) for fixed queries like
``/help`` or ``/skills``. Unmatched slash messages fall through to the
agent loop unchanged.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Sentinel that ``/reset`` returns so the feishu adapter can clear session
# state before sending the visible acknowledgement.
RESET_MARKER = "__HERMES_RESET_SESSION__"


@dataclass
class CommandContext:
    raw_text: str
    user_id: str = ""
    user_name: str = ""
    chat_id: str = ""
    args: List[str] = field(default_factory=list)


@dataclass
class CommandResult:
    text: str
    handled: bool = True


CommandHandler = Callable[[CommandContext], Awaitable[CommandResult]]

_COMMANDS: Dict[str, Tuple[CommandHandler, str]] = {}


def register(name: str, *aliases: str, help_text: str = ""):
    """Decorator: register a coroutine as a slash command + aliases."""

    def _decor(fn: CommandHandler) -> CommandHandler:
        for n in (name,) + aliases:
            _COMMANDS[n.lower()] = (fn, help_text)
        return fn

    return _decor


def parse_command(text: str) -> Optional[Tuple[str, List[str]]]:
    """Return (command_name, args) if ``text`` is a slash command, else None.

    Tolerates leading whitespace and a trailing ``@botname`` suffix.
    """
    s = (text or "").strip()
    if not s.startswith("/"):
        return None
    s = s[1:]
    if not s:
        return None
    try:
        tokens = shlex.split(s)
    except ValueError:
        tokens = s.split()
    if not tokens:
        return None
    head = tokens[0]
    if "@" in head:
        head = head.split("@", 1)[0]
    return head.lower(), tokens[1:]


async def dispatch(ctx: CommandContext) -> Optional[CommandResult]:
    """Dispatch ``ctx.raw_text`` if it matches a registered command."""
    parsed = parse_command(ctx.raw_text)
    if not parsed:
        return None
    name, args = parsed
    entry = _COMMANDS.get(name)
    if entry is None:
        return None
    handler, _help = entry
    ctx.args = args
    try:
        result = await handler(ctx)
        return result if result.handled else None
    except Exception as exc:
        logger.exception("Slash command /%s raised", name)
        return CommandResult(text=f"❌ 命令 `/{name}` 执行失败：{exc}")


# ---------------------------------------------------------------------------
# Built-in commands
# ---------------------------------------------------------------------------


@register("help", "h", "?", help_text="显示可用命令列表")
async def cmd_help(ctx: CommandContext) -> CommandResult:
    seen: set = set()
    rows: List[Tuple[str, str]] = []
    for name, (fn, help_text) in sorted(_COMMANDS.items()):
        key = id(fn)
        if key in seen:
            continue
        seen.add(key)
        rows.append((name, help_text or "(无描述)"))
    lines = ["**🤖 Hermes 飞书命令清单**", ""]
    for name, ht in rows:
        lines.append(f"- `/{name}` — {ht}")
    lines.append("")
    lines.append("不以 `/` 开头的消息会进入完整对话模式。")
    return CommandResult(text="\n".join(lines))


@register("whoami", "me", help_text="显示当前用户身份信息")
async def cmd_whoami(ctx: CommandContext) -> CommandResult:
    name = ctx.user_name or "(未知)"
    return CommandResult(
        text=(
            "**🪪 身份信息**\n\n"
            f"- 飞书 user_id: `{ctx.user_id or '(未提供)'}`\n"
            f"- 显示名: {name}\n"
            f"- 当前 chat_id: `{ctx.chat_id or '(未提供)'}`"
        )
    )


@register("skills", "tools", help_text="按 toolset 分组列出已注册工具")
async def cmd_skills(ctx: CommandContext) -> CommandResult:
    try:
        from tools.registry import registry
        tools_map = dict(registry._tools)
    except Exception as exc:
        return CommandResult(text=f"❌ 无法读取工具表：{exc}")
    if not tools_map:
        return CommandResult(text="（工具表为空）")
    grouped: Dict[str, List[str]] = {}
    for name, entry in tools_map.items():
        toolset = getattr(entry, "toolset", "其他") or "其他"
        grouped.setdefault(toolset, []).append(name)
    lines = [f"**🧰 已注册工具（共 {len(tools_map)} 个）**", ""]
    for ts in sorted(grouped):
        lines.append(f"**{ts}**")
        for n in sorted(grouped[ts]):
            lines.append(f"- `{n}`")
        lines.append("")
    return CommandResult(text="\n".join(lines).rstrip())


@register("describe", "desc", help_text="对数据文件出 schema 摘要：/describe <path>")
async def cmd_describe(ctx: CommandContext) -> CommandResult:
    if not ctx.args:
        return CommandResult(
            text=(
                "用法：`/describe <文件路径> [sheet]`\n"
                "例：`/describe /opt/data/cache/sales.xlsx`"
            )
        )
    path = ctx.args[0]
    sheet = ctx.args[1] if len(ctx.args) > 1 else None
    try:
        from tools.data_tools import describe_dataset_tool
    except Exception as exc:
        return CommandResult(text=f"❌ describe_dataset 不可用：{exc}")
    result = describe_dataset_tool(path=path, sheet=sheet)
    if not result.get("success"):
        msg = f"❌ {result.get('error', '未知错误')}"
        hint = result.get("hint")
        if hint:
            msg += f"\n\n💡 {hint}"
        return CommandResult(text=msg)
    shape = result["shape"]
    cols = result["columns"]
    lines = [
        f"**📊 数据集摘要**",
        "",
        f"- 路径：`{result['path']}`",
        f"- 形状：{shape[0]} 行 × {shape[1]} 列",
        f"- 列：{', '.join(cols[:10])}" + ("…" if len(cols) > 10 else ""),
    ]
    if result.get("sheets"):
        lines.append(f"- Sheets：{result['sheets']}")
    missing = result.get("missing_top5") or []
    if missing:
        lines.append("- 缺失率 Top：")
        for m in missing:
            lines.append(f"  - {m['column']}: {m['missing_pct'] * 100:.1f}%")
    lines.append("")
    lines.append("发完整问题（不带 `/`）可触发深度分析。")
    return CommandResult(text="\n".join(lines))


@register("reset", "new", help_text="清空当前对话上下文，开启新会话")
async def cmd_reset(ctx: CommandContext) -> CommandResult:
    return CommandResult(text=RESET_MARKER)


@register("version", "v", help_text="显示 Hermes 版本信息")
async def cmd_version(ctx: CommandContext) -> CommandResult:
    try:
        import importlib.metadata as md
        ver = md.version("hermes-agent")
    except Exception:
        ver = "unknown"
    return CommandResult(
        text=(
            "**Hermes Agent**\n\n"
            f"- 版本：`{ver}`\n"
            "- 适配器：Feishu (lark-oapi)\n"
            "- 模型：MiniMax 中国端点"
        )
    )


@register("ping", help_text="测试机器人是否在线")
async def cmd_ping(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="🏓 pong — Hermes 在线")
