import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kite_auth_store

class KiteAuthStoreTests(unittest.TestCase):
    def test_saves_and_loads_token_without_exposing_it_in_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "kite_token.json"
            with patch.dict(os.environ, {"KITE_TOKEN_FILE": str(path)}):
                kite_auth_store.save_access_token("test-access-token", "public")
                self.assertEqual(kite_auth_store.load_access_token(), "test-access-token")
                status = kite_auth_store.token_status()
                self.assertTrue(status["configured"])
                self.assertEqual(status["source"], "callback_store")
                self.assertNotIn("access_token", status)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["public_token"], "public")

    def test_falls_back_to_environment_token(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"KITE_TOKEN_FILE": str(Path(directory) / "missing.json")}):
                with patch.object(kite_auth_store, "settings", SimpleNamespace(kite_access_token="env-token")):
                    self.assertEqual(kite_auth_store.load_access_token(), "env-token")

if __name__ == "__main__":
    unittest.main()
