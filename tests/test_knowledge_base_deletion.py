import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from infrastructure.knowledge.pg_repository import KnowledgeBaseBusyError
from interfaces.web.knowledge_routes import (
    _cleanup_knowledge_base_files,
    delete_knowledge_base,
)


class FakeDeleteRepository:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.received_kb_id = None

    async def delete_knowledge_base_with_documents(self, kb_id):
        self.received_kb_id = kb_id
        if self.error is not None:
            raise self.error
        return self.result


class KnowledgeBaseFileCleanupTests(unittest.TestCase):
    def test_cleanup_deletes_only_files_inside_knowledge_base_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "originals" / "user" / "kb"
            root.mkdir(parents=True)
            inside = root / "document.txt"
            inside.write_text("inside", encoding="utf-8")
            outside = Path(temp_dir) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")

            deleted, failed = _cleanup_knowledge_base_files(
                [str(inside), str(outside)],
                root,
            )

            self.assertEqual((deleted, failed), (1, 1))
            self.assertFalse(inside.exists())
            self.assertTrue(outside.exists())
            self.assertFalse(root.exists())


class DeleteKnowledgeBaseRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_removes_database_records_and_stored_file(self):
        kb_id = uuid4()
        user_id = "delete-test-user"

        with tempfile.TemporaryDirectory() as temp_dir:
            kb_dir = (
                Path(temp_dir) / "originals" / user_id / str(kb_id)
            )
            kb_dir.mkdir(parents=True)
            stored_file = kb_dir / "document.txt"
            stored_file.write_text("test", encoding="utf-8")
            repo = FakeDeleteRepository(result=[str(stored_file)])

            with (
                patch(
                    "interfaces.web.knowledge_routes._get_repo",
                    return_value=repo,
                ),
                patch(
                    "interfaces.web.knowledge_routes.get_settings",
                    return_value=SimpleNamespace(
                        knowledge_storage_path=temp_dir,
                    ),
                ),
            ):
                result = await delete_knowledge_base(
                    str(kb_id),
                    SimpleNamespace(),
                    user_id,
                )

            self.assertEqual(repo.received_kb_id, kb_id)
            self.assertEqual(
                result,
                {"ok": True, "deleted_files": 1, "cleanup_failed": 0},
            )
            self.assertFalse(stored_file.exists())
            self.assertFalse(kb_dir.exists())

    async def test_delete_returns_404_for_unknown_or_unowned_knowledge_base(self):
        repo = FakeDeleteRepository(result=None)
        with patch(
            "interfaces.web.knowledge_routes._get_repo",
            return_value=repo,
        ):
            with self.assertRaises(HTTPException) as context:
                await delete_knowledge_base(
                    str(uuid4()),
                    SimpleNamespace(),
                    "delete-test-user",
                )

        self.assertEqual(context.exception.status_code, 404)

    async def test_delete_returns_409_while_document_is_being_ingested(self):
        repo = FakeDeleteRepository(
            error=KnowledgeBaseBusyError("indexing"),
        )
        with patch(
            "interfaces.web.knowledge_routes._get_repo",
            return_value=repo,
        ):
            with self.assertRaises(HTTPException) as context:
                await delete_knowledge_base(
                    str(uuid4()),
                    SimpleNamespace(),
                    "delete-test-user",
                )

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("indexing", context.exception.detail)


if __name__ == "__main__":
    unittest.main()
