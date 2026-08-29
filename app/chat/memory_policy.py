def build_memory_policy(mode: str) -> dict[str, bool]:
    policies = {
        "normal": {
            "conversation": True, "user": True,
            "knowledge": False, "research": False,
        },
        "knowledge": {
            "conversation": True, "user": False,
            "knowledge": True, "research": False,
        },
        "hybrid": {
            "conversation": True, "user": True,
            "knowledge": True, "research": False,
        },
        "deep_research": {
            "conversation": True, "user": True,
            "knowledge": True, "research": True,
        },
    }
    try:
        return policies[mode].copy()
    except KeyError:
        raise ValueError(f"不支持的回答模式: {mode}") from None

USER_MEMORY_TYPES = {
    "normal": frozenset({
        # 普通聊天允许读取全部稳定用户记忆，允许读取用户偏好、个人资料、项目背景、决策、目标、约束。
        "preference",
        "profile",
        "project",
        "decision",
        "goal",
        "constraint",
    }),
    "knowledge": frozenset({
        # 仅允许影响回答格式，不允许读取用户事实。
        "preference",
    }),
    "hybrid": frozenset({
        # 混合模式只读取安全偏好。
        "preference",
    }),
    "deep_research": frozenset({
        # 研究可以读取研究目标、项目背景和约束。
        "preference",
        "project",
        "goal",
        "constraint",
    }),
}

def allowed_user_memory_types(mode: str) -> frozenset[str]:
    try:
        return USER_MEMORY_TYPES[mode]
    except KeyError:
        raise ValueError(f"不支持的回答模式: {mode}") from None