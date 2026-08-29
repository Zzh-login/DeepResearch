import unittest

from infrastructure.model_gateway.audit_repository import redact_sensitive


class AuditRedactionTests(unittest.TestCase):
    def test_redacts_nested_sensitive_fields(self):
        result = redact_sensitive({
            "headers": {"Authorization": "Bearer secret"},
            "items": [{"password": "hidden"}],
            "content": "private document",
            "safe": "ok",
        })
        self.assertEqual(result["headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(result["items"][0]["password"], "[REDACTED]")
        self.assertEqual(result["content"], "[REDACTED]")
        self.assertEqual(result["safe"], "ok")

    def test_hashes_large_non_sensitive_string(self):
        result = redact_sensitive({"summary": "x" * 501})
        self.assertEqual(result["summary"]["length"], 501)
        self.assertIn("sha256", result["summary"])


if __name__ == "__main__":
    unittest.main()
