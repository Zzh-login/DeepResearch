import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.asr.client import resolve_model_path


class AsrModelResolutionTests(unittest.TestCase):
    def test_configured_local_model_is_used(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            (model_dir / "model.pt").write_bytes(b"test")
            with patch.dict(
                os.environ,
                {"ASR_MODEL_PATH": str(model_dir), "ASR_ALLOW_DOWNLOAD": "0"},
                clear=False,
            ):
                self.assertEqual(resolve_model_path(), str(model_dir.resolve()))

    def test_remote_id_requires_explicit_permission(self):
        with patch.dict(
            os.environ,
            {"ASR_MODEL_PATH": "Z:/missing", "ASR_ALLOW_DOWNLOAD": "1"},
            clear=False,
        ), patch("infrastructure.asr.client.Path.home") as home:
            home.return_value = Path("Z:/missing-home")
            self.assertEqual(resolve_model_path(), "iic/SenseVoiceSmall")


if __name__ == "__main__":
    unittest.main()
