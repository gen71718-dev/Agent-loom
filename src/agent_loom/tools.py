"""工具层：agent 的"手脚"。

每个函数的 docstring 就是模型看到的工具说明，写清楚"什么时候该用"比写清楚"参数类型"更重要。
"""

from __future__ import annotations

import ast
import logging
import operator
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node: ast.AST) -> float | int:
    """在 AST 白名单上求值，绝不用 eval()。

    直接 eval 模型生成的字符串等于把远程代码执行交给模型，是 agent 项目最常见的安全漏洞。
    """
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    raise ValueError("表达式包含不允许的语法")


@tool
def calculator(expression: str) -> str:
    """计算一个纯数学表达式。

    当用户问题涉及算术、百分比、幂运算时使用，例如 "(12+8)*3/2"。
    只支持 + - * / // % ** 和括号，不支持变量和函数。
    """
    try:
        return str(_eval(ast.parse(expression, mode="eval")))
    except Exception as exc:
        return f"计算失败：{exc}"


@tool
def current_time(timezone: str = "Asia/Shanghai") -> str:
    """查询某个时区的当前时间。

    任何涉及"现在/今天/还有几天"的问题都必须调用它，不要凭训练数据猜测。
    timezone 用 IANA 名称，例如 "Asia/Shanghai"、"UTC"、"America/New_York"。
    """
    try:
        return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return f"未知时区：{timezone}"


@tool
def notify(channel: str, message: str) -> str:
    """向指定渠道发送通知。

    这是**有副作用的对外操作**，执行前需要人工审批（见 APPROVAL_REQUIRED_TOOLS 配置）。
    当用户明确要求"通知 / 提醒 / 发给某人 / 发到某个渠道"时使用。
    channel 例如 email、sms、webhook；message 是要发送的正文。
    """
    ticket = uuid.uuid4().hex[:8]
    logger.info("[notify] channel=%s ticket=%s message=%s", channel, ticket, message)
    return f"已提交到 {channel} 渠道，回执号 {ticket}"


TOOLS = [calculator, current_time, notify]
