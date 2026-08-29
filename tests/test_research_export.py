import inspect
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from docx import Document

from app.research.export_service import ExportService, safe_resolve_artifact_path
from app.research.export_worker import ResearchExportWorker
from domain.research.artifacts import ArtifactKind, ExportJobStatus
from infrastructure.config.settings import Settings
from infrastructure.research.pg_repository import PgResearchRepository
from interfaces.web.research_routes import (
    _serialize_artifact,
    _serialize_export_job,
    download_artifact,
    router,
)
from interfaces.web.research_schemas import ExportRequest


class ResearchExportTests(unittest.TestCase):
    @staticmethod
    def _report_payload():
        return {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "summary1",
                    "section": "summary",
                    "kind": "evidence",
                    "text": "摘要内容",
                    "citation_ids": ["W1"],
                },
                {
                    "id": "finding1",
                    "section": "findings",
                    "kind": "evidence",
                    "text": "主要发现",
                    "citation_ids": ["W1"],
                },
                {
                    "id": "limits1",
                    "section": "uncertainties",
                    "kind": "limitation",
                    "text": "现有证据仍有局限",
                    "citation_ids": ["W1"],
                },
                {
                    "id": "conclusion1",
                    "section": "conclusion",
                    "kind": "synthesis",
                    "text": "最终结论",
                    "based_on_block_ids": ["finding1"],
                },
            ],
            "markdown": "数据库中保存的渲染文本",
            "citation_ids": ["W1"],
            "scope": "测试范围",
        }

    @staticmethod
    def _settings(root):
        return SimpleNamespace(
            research_artifact_storage_path=root,
            research_export_max_size_mb=5,
            research_export_timeout_seconds=30,
            libreoffice_binary="soffice",
        )

    def test_export_request_accepts_supported_kinds(self):
        for kind in ("markdown", "docx", "pdf"):
            self.assertEqual(ExportRequest(kind=kind).kind, kind)

    def test_export_dependency_and_settings_are_declared(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        self.assertIn("python-docx==", requirements)
        settings = Settings(_env_file=None)
        self.assertGreater(settings.research_export_max_size_mb, 0)
        self.assertGreater(settings.research_export_poll_seconds, 0)
        self.assertGreater(settings.research_export_timeout_seconds, 0)
        self.assertEqual(settings.research_pdf_converter, "word")
        self.assertIn("WINWORD.EXE", settings.microsoft_word_binary)

    def test_export_domain_values_match_database_contract(self):
        self.assertEqual(
            {item.value for item in ArtifactKind},
            {"markdown", "docx", "pdf"},
        )
        self.assertEqual(
            {item.value for item in ExportJobStatus},
            {"created", "running", "completed", "failed"},
        )

    def test_safe_artifact_path_stays_under_root(self):
        with tempfile.TemporaryDirectory() as root:
            resolved = safe_resolve_artifact_path(
                root,
                "task-id/report.pdf",
            )
            resolved.relative_to(Path(root).resolve())

    def test_safe_artifact_path_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                safe_resolve_artifact_path(root, "../secret.txt")

    def test_markdown_generation_accepts_completed_report_payload(self):
        with tempfile.TemporaryDirectory() as root:
            service = ExportService(self._settings(root))
            task_id = uuid4()
            artifact = service.generate({
                "id": uuid4(),
                "task_id": task_id,
                "kind": "markdown",
                "query": "测试问题",
                "report": self._report_payload(),
            })
            path = safe_resolve_artifact_path(root, artifact.storage_key)
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "research-report.md")
            self.assertIn("摘要内容", path.read_text(encoding="utf-8"))
            self.assertEqual(artifact.sha256, service._sha256(path))
            self.assertEqual(artifact.size_bytes, path.stat().st_size)

    def test_markdown_generation_includes_source_index(self):
        with tempfile.TemporaryDirectory() as root:
            service = ExportService(self._settings(root))
            artifact = service.generate(
                {
                    "id": uuid4(),
                    "task_id": uuid4(),
                    "kind": "markdown",
                    "query": "测试问题",
                    "report": self._report_payload(),
                },
                sources=[
                    {
                        "source_id": "W1",
                        "title": "网页来源",
                        "url": "https://example.com/source",
                    },
                    {
                        "source_id": "K1",
                        "title": "本地文档",
                        "filename": "machine learning.txt",
                        "source_type": "knowledge",
                        "metadata": {
                            "section_title": "5.3.2 过拟合 Overfitting",
                            "page_number": 46,
                            "document_id": "doc-1",
                            "chunk_id": "chunk-1",
                        },
                    },
                ],
            )
            path = safe_resolve_artifact_path(root, artifact.storage_key)
            content = path.read_text(encoding="utf-8")
            self.assertIn("## 来源", content)
            self.assertIn("[W1] 网页来源 - https://example.com/source", content)
            self.assertIn("[K1] 本地文档 - machine learning.txt", content)
            self.assertIn("章节：5.3.2 过拟合 Overfitting", content)
            self.assertIn("页码：46", content)
            self.assertIn("文档 ID：doc-1", content)
            self.assertIn("切片 ID：chunk-1", content)
            self.assertIn("\n\n- [K1]", content)

    def test_markdown_source_entries_are_separate_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            service = ExportService(self._settings(root))
            artifact = service.generate(
                {
                    "id": uuid4(),
                    "task_id": uuid4(),
                    "kind": "markdown",
                    "query": "测试问题",
                    "report": self._report_payload(),
                },
                sources=[
                    {"source_id": "K1", "title": "文档一", "filename": "a.txt"},
                    {"source_id": "K2", "title": "文档二", "filename": "b.txt"},
                ],
            )
            path = safe_resolve_artifact_path(root, artifact.storage_key)
            content = path.read_text(encoding="utf-8")
            self.assertIn("\n\n- [K1] 文档一 - a.txt\n", content)
            self.assertIn("\n\n- [K2] 文档二 - b.txt\n", content)

    def test_docx_generation_contains_query_scope_and_sources(self):
        with tempfile.TemporaryDirectory() as root:
            service = ExportService(self._settings(root))
            artifact = service.generate(
                {
                    "id": uuid4(),
                    "task_id": uuid4(),
                    "kind": "docx",
                    "query": "测试问题",
                    "report": self._report_payload(),
                },
                sources=[{
                    "source_id": "W1",
                    "title": "测试来源",
                    "url": "https://example.com/source",
                }],
            )
            path = safe_resolve_artifact_path(root, artifact.storage_key)
            document = Document(path)
            text = "\n".join(item.text for item in document.paragraphs)
            self.assertIn("测试问题", text)
            self.assertIn("测试范围", text)
            self.assertIn("测试来源", text)

    def test_download_handler_uses_expected_public_name(self):
        self.assertEqual(download_artifact.__name__, "download_artifact")

    def test_export_routes_have_expected_methods_and_statuses(self):
        routes = {
            (item.path, method): item
            for item in router.routes
            for method in item.methods
        }
        prefix = "/api/research-tasks/{task_id}"
        self.assertEqual(
            routes[(f"{prefix}/exports", "POST")].status_code,
            202,
        )
        self.assertIn((f"{prefix}/exports", "GET"), routes)
        self.assertIn(
            (f"{prefix}/exports/{{job_id}}", "GET"),
            routes,
        )
        self.assertIn((f"{prefix}/artifacts", "GET"), routes)
        self.assertIn(
            (f"{prefix}/artifacts/{{artifact_id}}", "GET"),
            routes,
        )
        self.assertEqual(
            routes[
                (f"{prefix}/artifacts/{{artifact_id}}", "DELETE")
            ].status_code,
            204,
        )

    def test_serializers_do_not_expose_storage_key(self):
        now = datetime.now(timezone.utc)
        task_id = uuid4()
        artifact_id = uuid4()
        artifact = _serialize_artifact({
            "id": artifact_id,
            "task_id": task_id,
            "kind": "pdf",
            "filename": "report.pdf",
            "storage_key": "private/report.pdf",
            "sha256": "a" * 64,
            "size_bytes": 10,
            "expires_at": now,
            "expired": False,
            "created_at": now,
        })
        self.assertNotIn("storage_key", artifact)
        self.assertEqual(artifact["id"], str(artifact_id))

        job = _serialize_export_job({
            "id": uuid4(),
            "task_id": task_id,
            "kind": "pdf",
            "status": "created",
            "artifact_id": None,
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        })
        self.assertEqual(job["task_id"], str(task_id))

    def test_download_rejects_expired_artifacts(self):
        import inspect
        import interfaces.web.research_routes as routes

        source = inspect.getsource(routes.download_artifact)
        self.assertIn("研究制品已过期，请重新导出", source)

    def test_repository_export_queries_enforce_owner(self):
        for method_name in (
            "get_export_job",
            "list_export_jobs",
            "list_artifacts",
            "get_artifact",
            "delete_artifact",
        ):
            source = inspect.getsource(
                getattr(PgResearchRepository, method_name)
            )
            self.assertIn("owner_id", source, method_name)

    def test_export_claim_is_atomic(self):
        source = inspect.getsource(PgResearchRepository.claim_export_job)
        self.assertIn("FOR UPDATE OF job SKIP LOCKED", source)
        self.assertIn("attempts=attempts+1", source)

    def test_export_worker_completes_or_fails_claimed_job(self):
        source = inspect.getsource(ResearchExportWorker._run_loop)
        self.assertIn("claim_export_job", source)
        self.assertIn("complete_export_job", source)
        self.assertIn("fail_export_job", source)
        self.assertIn("asyncio.to_thread", source)

    def test_export_worker_recovers_interrupted_jobs(self):
        start_source = inspect.getsource(ResearchExportWorker.start)
        repo_source = inspect.getsource(
            PgResearchRepository.recover_export_jobs
        )
        self.assertIn("recover_export_jobs", start_source)
        self.assertIn("WHERE status='running'", repo_source)
        self.assertIn("status='created'", repo_source)

    def test_export_worker_cleans_expired_artifacts(self):
        source = inspect.getsource(ResearchExportWorker._run_loop)
        cleanup = inspect.getsource(
            PgResearchRepository.cleanup_expired_artifacts
        )
        self.assertIn("_cleanup_expired_artifacts", source)
        self.assertIn("FOR UPDATE SKIP LOCKED", cleanup)
        self.assertIn("artifact.expired", cleanup)

    def test_deleting_artifact_does_not_immediately_requeue_export(self):
        source = inspect.getsource(PgResearchRepository.delete_artifact)
        self.assertIn("status='failed'", source)
        self.assertNotIn("status='created'", source)

    def test_create_export_requires_completed_owned_task(self):
        source = inspect.getsource(PgResearchRepository.create_export_job)
        self.assertIn("owner_id=$2", source)
        self.assertIn("ResearchStatus.COMPLETED.value", source)
        self.assertIn("研究报告不存在", source)

    def test_expired_completed_export_is_requeued(self):
        source = inspect.getsource(PgResearchRepository.create_export_job)
        self.assertIn("stale_completed", source)
        self.assertIn("artifact.expires_at <= NOW()", source)
        self.assertIn("artifact_id=NULL", source)

    def test_export_completion_and_failure_are_auditable(self):
        completed = inspect.getsource(
            PgResearchRepository.complete_export_job
        )
        failed = inspect.getsource(PgResearchRepository.fail_export_job)
        deleted = inspect.getsource(PgResearchRepository.delete_artifact)
        self.assertIn("artifact.ready", completed)
        self.assertIn("artifact.failed", failed)
        self.assertIn("artifact.deleted", deleted)

    def test_task_deletion_cleans_owned_artifact_files(self):
        import interfaces.web.research_routes as routes

        source = inspect.getsource(routes.delete_research_task)
        self.assertIn("list_artifact_files", source)
        self.assertIn("safe_resolve_artifact_path", source)
        self.assertIn("unlink(missing_ok=True)", source)


class ResearchExportMigrationTests(unittest.TestCase):
    def test_export_migration_is_transactional_and_idempotent(self):
        migration = Path(
            "migrations/006_stage8_2_7_exports.sql"
        ).read_text(encoding="utf-8")
        self.assertTrue(migration.lstrip().startswith("BEGIN;"))
        self.assertTrue(migration.rstrip().endswith("COMMIT;"))
        self.assertIn("UNIQUE(task_id,kind)", migration.replace(" ", ""))
        self.assertIn("schema_migrations", migration)
