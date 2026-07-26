import pytest

from studio.execution.external_ai import ExternalToolError, run_external_tool


def test_external_ai_is_blocked_before_picture_lock():
    with pytest.raises(ExternalToolError, match="Lock Picture"):
        run_external_tool("real_esrgan", ["--help"], picture_locked=False)


def test_unknown_external_tool_is_rejected():
    with pytest.raises(ExternalToolError, match="未登记"):
        run_external_tool("invented", [], picture_locked=True)
