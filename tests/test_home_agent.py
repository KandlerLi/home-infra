from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ansible/roles/home_agent/files/agent"))

from home_agent.agent import OpenAIResponsesProvider


class FakeResponses:
    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="get_system_health",
                        arguments="{}",
                        call_id="call-1",
                    )
                ],
                output_text="",
            ),
            SimpleNamespace(output=[], output_text="The homeserver is healthy."),
        ]

    def create(self, **request):
        self.requests.append(request)
        return self.responses.pop(0)


class FakeHomeTools:
    def __init__(self) -> None:
        self.calls = []

    def call(self, name: str):
        self.calls.append(name)
        return {"status": "ok"}


class HomeAgentTests(unittest.TestCase):
    def test_provider_executes_only_named_tool_and_returns_final_text(self) -> None:
        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)
        home_tools = FakeHomeTools()
        provider = OpenAIResponsesProvider(
            api_key="unused-test-key",
            model="test-model",
            home_tools=home_tools,
            client=client,
        )

        answer = provider.respond("Check my homeserver.")

        self.assertEqual(answer, "The homeserver is healthy.")
        self.assertEqual(home_tools.calls, ["get_system_health"])
        second_input = responses.requests[1]["input"]
        self.assertEqual(second_input[-1]["type"], "function_call_output")
        self.assertEqual(second_input[-1]["call_id"], "call-1")


if __name__ == "__main__":
    unittest.main()
