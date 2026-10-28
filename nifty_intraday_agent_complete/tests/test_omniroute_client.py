"""Offline tests for the optional OmniRoute adapter."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llm.omniroute_client import OmniRouteClient


def response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class OmniRouteClientTests(unittest.TestCase):
    @patch("llm.omniroute_client.settings")
    def test_primary_model_success(self, settings):
        settings.omniroute_enabled = True
        settings.omniroute_model = "auto"
        settings.omniroute_fallback_model = "provider/model-b"
        sdk = Mock()
        sdk.chat.completions.create.return_value = response(" bullish ")
        client = OmniRouteClient(sdk_client=sdk)
        self.assertEqual(client.chat_completion([{"role": "user", "content": "test"}]), "bullish")
        self.assertEqual(sdk.chat.completions.create.call_count, 1)
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs["model"], "auto")

    @patch("llm.omniroute_client.settings")
    def test_fallback_model_after_primary_error(self, settings):
        settings.omniroute_enabled = True
        settings.omniroute_model = "auto"
        settings.omniroute_fallback_model = "provider/model-b"
        sdk = Mock()
        sdk.chat.completions.create.side_effect = [TimeoutError(), response("fallback result")]
        client = OmniRouteClient(sdk_client=sdk)
        self.assertEqual(client.chat_completion([{"role": "user", "content": "test"}]), "fallback result")
        self.assertEqual(
            [call.kwargs["model"] for call in sdk.chat.completions.create.call_args_list],
            ["auto", "provider/model-b"],
        )

    @patch("llm.omniroute_client.settings")
    def test_all_models_fail_returns_none(self, settings):
        settings.omniroute_enabled = True
        settings.omniroute_model = "auto"
        settings.omniroute_fallback_model = "provider/model-b"
        sdk = Mock()
        sdk.chat.completions.create.side_effect = RuntimeError("provider down")
        client = OmniRouteClient(sdk_client=sdk)
        self.assertIsNone(client.chat_completion([{"role": "user", "content": "test"}]))

    @patch("llm.omniroute_client.settings")
    def test_disabled_client_returns_none(self, settings):
        settings.omniroute_enabled = False
        settings.omniroute_model = "auto"
        settings.omniroute_fallback_model = ""
        client = OmniRouteClient(sdk_client=Mock())
        self.assertIsNone(client.chat_completion([{"role": "user", "content": "test"}]))


if __name__ == "__main__":
    unittest.main()
