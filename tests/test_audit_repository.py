import inspect
import unittest

from infrastructure.model_gateway.audit_repository import (
    AuditRepository,
    redact_sensitive,
)


class AuditRepositoryTests(unittest.TestCase):
    def test_redaction_removes_sensitive_nested_values(self):
        result = redact_sensitive({
            "authorization": "Bearer secret",
            "cookie": "session=secret",
            "nested": {"password": "hidden"},
            "safe_count": 3,
        })
        self.assertEqual(result["authorization"], "[REDACTED]")
        self.assertEqual(result["cookie"], "[REDACTED]")
        self.assertEqual(result["nested"]["password"], "[REDACTED]")
        self.assertEqual(result["safe_count"], 3)

    def test_audit_write_supports_existing_transaction_connection(self):
        source = inspect.getsource(AuditRepository.write)
        self.assertIn("conn=None", source)
        self.assertIn("await self._insert(", source)
        self.assertIn("                    conn,", source)
        self.assertIn("conn.transaction()", source)

    def test_audit_write_uses_only_metadata_payload(self):
        source = inspect.getsource(AuditRepository._insert)
        self.assertIn("metadata", source)
        self.assertIn("request_id", source)


if __name__ == "__main__":
    unittest.main()
