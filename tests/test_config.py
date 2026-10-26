import tempfile
import unittest
from pathlib import Path

from app.core.config import load_env_file


class EnvFileTests(unittest.TestCase):
    def load(self, content: str, environ=None, encoding="utf-8"):
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_bytes(content.encode(encoding))
        environ = {} if environ is None else environ
        return load_env_file(path, environ), environ

    def test_basic_values_comments_quotes_and_export(self):
        loaded, env = self.load(
            "# comment\n\nLLM_PROVIDER=openai\nOPENAI_MODEL = \"gpt-4o-mini\"\nexport OPENAI_BASE_URL='https://api.openai.com/v1'\n"
            "OPENAI_TIMEOUT_SECONDS=60 # seconds\nnot a pair\n"
        )
        self.assertTrue(loaded)
        self.assertEqual(env, {
            "LLM_PROVIDER": "openai",
            "OPENAI_MODEL": "gpt-4o-mini",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_TIMEOUT_SECONDS": "60",
        })

    def test_real_environment_variables_win(self):
        _, env = self.load("LLM_PROVIDER=openai\n", environ={"LLM_PROVIDER": "none"})
        self.assertEqual(env["LLM_PROVIDER"], "none")

    def test_windows_powershell_bom_and_crlf(self):
        # `Out-File -Encoding utf8` in Windows PowerShell 5.1 writes a BOM and CRLF line endings.
        _, env = self.load("LLM_PROVIDER=openai\r\nOPENAI_MODEL=gpt-4o-mini\r\n", encoding="utf-8-sig")
        self.assertEqual(env, {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4o-mini"})

    def test_missing_file_is_fine(self):
        env = {}
        self.assertFalse(load_env_file(Path(tempfile.mkdtemp()) / "nope.env", env))
        self.assertEqual(env, {})


if __name__ == "__main__":
    unittest.main()
