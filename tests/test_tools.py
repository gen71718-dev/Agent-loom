"""工具的纯单元测试：不需要网络、不需要 Redis、不需要 API Key。"""

import pytest

from agent_loom.tools import TOOLS, calculator, current_time


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("1+1", "2"), ("(12+8)*3/2", "30.0"), ("2**10", "1024"), ("-5 + 3", "-2")],
)
def test_calculator_evaluates_arithmetic(expression: str, expected: str) -> None:
    assert calculator.invoke({"expression": expression}) == expected


def test_calculator_rejects_code_execution() -> None:
    """白名单求值的核心价值：模型生成的恶意表达式不能变成远程代码执行。"""
    result = calculator.invoke({"expression": "__import__('os').system('whoami')"})
    assert result.startswith("计算失败")


def test_current_time_returns_formatted_timestamp() -> None:
    assert "2026" in current_time.invoke({"timezone": "UTC"})


def test_current_time_handles_unknown_timezone() -> None:
    assert current_time.invoke({"timezone": "Mars/Olympus"}).startswith("未知时区")


def test_tools_are_registered() -> None:
    assert [tool.name for tool in TOOLS] == ["calculator", "current_time"]
