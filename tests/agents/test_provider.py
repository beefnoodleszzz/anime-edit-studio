import json

from pydantic import BaseModel

from studio.agents.provider import ClaudeCLIProvider


class Output(BaseModel):
    value: int


def test_claude_provider_requires_structured_output(monkeypatch):
    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {
                "structured_output": {"value": 7},
                "total_cost_usd": 0.01,
                "session_id": "s",
            }
        )

    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        return Result()

    monkeypatch.setattr("studio.agents.provider.subprocess.run", run)
    output, call = ClaudeCLIProvider().generate(
        system="system", prompt="prompt", output_type=Output
    )
    assert output.value == 7
    assert "--json-schema" in captured["command"]
    assert captured["command"][captured["command"].index("--tools") + 1] == ""
    assert call.cost_usd == 0.01
