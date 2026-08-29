import unittest

from domain.research.models import (
    TERMINAL_STATUSES,
    ResearchStatus,
    require_transition,
)


class ResearchStateMachineTests(unittest.TestCase):
    """直接打状态机核心 require_transition，不经过任何 mock，规则焊死在这里。"""

    def test_main_flow_is_allowed(self):
        # 主流程：created → planning → searching → reading → writing → verifying → completed
        flow = [
            ("created", "planning"),
            ("planning", "searching"),
            ("searching", "reading"),
            ("reading", "writing"),
            ("writing", "verifying"),
            ("verifying", "completed"),
        ]
        for current, target in flow:
            require_transition(current, target)  # 不抛异常即通过

    def test_repair_flow_allows_verifying_back_to_writing(self):
        # 修复流程关键迁移：verifying → writing 必须合法（本次修复点）
        require_transition("verifying", "writing")

    def test_repair_cycle_is_round_trip(self):
        # 完整修复往返：writing → verifying → writing → verifying
        for current, target in [
            ("writing", "verifying"),
            ("verifying", "writing"),
            ("writing", "verifying"),
        ]:
            require_transition(current, target)

    def test_created_and_running_tasks_can_pause(self):
        require_transition("created", "paused")
        for status in ("planning", "searching", "reading", "writing", "verifying"):
            require_transition(status, "paused")

    def test_paused_task_can_resume(self):
        require_transition("paused", "created")

    def test_completed_only_from_verifying(self):
        # 只有 verifying 才能 completed；writing 不能直接跳 completed
        require_transition("verifying", "completed")
        with self.assertRaises(ValueError):
            require_transition("writing", "completed")

    def test_terminal_states_allow_no_transition(self):
        # 终态（completed/failed/cancelled）之后不能再迁移
        for terminal in TERMINAL_STATUSES:
            for target in ResearchStatus:
                if terminal == target:
                    continue
                with self.assertRaises(ValueError):
                    require_transition(terminal.value, target.value)

    def test_unknown_status_raises(self):
        with self.assertRaises(ValueError):
            require_transition("bogus", "completed")
