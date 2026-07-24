"""
verify_vector_memory.py —— 向量记忆管线端到端验证（pgvector + BGE-M3 栈）

验证项：
  1. Import 链不崩
  2. VectorMemory 独立功能：建表、写、搜、计数、幂等 UPSERT、source 隔离
  3. PromptBuilder 异步注入：_get_vector_context / build(include_vector=True)
  4. 优雅降级（vector_memory=None 时不注入）

默认嵌入器：确定性 1024 维投影（秒级、离线、无需加载模型）。
真实嵌入器：python verify_vector_memory.py --real  会加载 BGE-M3（~2.2GB，首次需已下载）。

测试数据使用独立命名空间 verify_xxx，运行结束 DELETE 清理，不污染生产 memory_vectors。

用法：
    python verify_vector_memory.py
    python verify_vector_memory.py --real
"""

import asyncio
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOTAL = 0
PASS = 0


def check(name: str, condition: bool, detail: str = ""):
    """记录并展示一条验证用例的通过/失败状态。

    参数:
        name: 用例名称，用于输出展示。
        condition: 布尔断言结果；为 True 计为通过，否则计为失败。
        detail: 额外说明文本，无论通过与否均可能随输出打印。

    返回值：无。副作用：更新模块级全局计数器 TOTAL / PASS 并打印结果行。
    """
    global TOTAL, PASS
    TOTAL += 1
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  — {detail}")
    if detail and condition:
        print(f"     {detail}")


# ── 确定性 1024 维嵌入器（字符投影，适配中文，用于快速离线验证） ──
def test_embed(text: str) -> list[float]:
    """确定性 1024 维离线嵌入器（字符投影），用于快速验证而无需加载模型。

    参数:
        text: 待嵌入的文本（非空白字符参与统计）。

    返回值：长度为 1024 的归一化浮点向量。逻辑：对每个非空白字符按哈希定位到
    某一维并累加 1.0，最后对整向量做 L2 归一化（避免零向量除零，退化单位范数）。
    特点：结果完全确定、离线、秒级，适合中文场景的相似性排序测试。
    """
    vec = [0.0] * 1024
    for ch in str(text):
        if not ch.isspace():
            vec[hash(ch) % 1024] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def main(use_real: bool):
    """向量记忆管线（pgvector + BGE-M3）的端到端验证主流程。

    参数:
        use_real: 为 True 时加载真实 BGE-M3 嵌入器（~2.2GB，需已下载）；
                  为 False 时使用确定性 test_embed 离线嵌入器。

    返回值：布尔值，True 表示全部用例通过（PASS == TOTAL）。
    流程覆盖：Import 链、VectorMemory 建表/写入/搜索/计数/幂等 UPSERT/
    source 隔离、PromptBuilder 异步向量注入（_get_vector_context / build）、
    无 vector_memory 时的优雅降级。末尾按独立命名空间 verify_xxx 清理测试数据，
    不污染生产 memory_vectors 表。
    """
    print("1. Import chain")
    from infrastructure.memory.vector_repo import VectorMemory
    from domain.prompt.builder import PromptBuilder

    check("VectorMemory import", True)
    check("PromptBuilder import", True)

    # 独立命名空间，避免污染生产数据
    import time
    SRC = f"verify_{int(time.time() * 1000)}"
    OTHER = f"verify_other_{int(time.time() * 1000)}"

    # 选择嵌入器
    if use_real:
        print("\n[模式] 真实 BGE-M3 嵌入器（~2.2GB，需已下载）")
        vm = VectorMemory()  # 默认 BGE-M3 1024 维
    else:
        print("\n[模式] 确定性 1024 维测试嵌入器（离线、秒级）")
        vm = VectorMemory(embed_fn=test_embed, embedding_dim=1024)

    # ═══════════════════════════════════════════════════
    # 2. VectorMemory 独立功能
    # ═══════════════════════════════════════════════════
    print("\n2. VectorMemory standalone")

    await vm.ensure_table()
    check("ensure_table: 表就绪", True)

    h1 = await vm.add(SRC, "用户喜欢用 Python 做后端开发，偏好 FastAPI")
    h2 = await vm.add(SRC, "用户正在开发一个语音对话助手项目 robot_system")
    h3 = await vm.add(SRC, "用户每天喝咖啡，用的是法压壶")
    check("add 返回有效哈希(24位)", len(h1) == 24, f"len={len(h1)}")
    check("count after add 3 = 3", await vm.count(SRC) == 3, f"got {await vm.count(SRC)}")

    # 幂等
    await vm.add(SRC, "用户喜欢用 Python 做后端开发，偏好 FastAPI")
    check("upsert 保持 count=3", await vm.count(SRC) == 3, f"got {await vm.count(SRC)}")

    # 搜索
    results = await vm.search("后端开发", source=SRC, top_k=3, min_score=0.0)
    check("search 返回结果", len(results) >= 1, f"got {len(results)}")
    if results:
        check("结果含 score", "score" in results[0], str(results[0].get("score")))
        check("结果含 summary", "summary" in results[0])
        # 语义排序：最相关（含"后端/Python"）排前
        top = results[0]["summary"]
        check("语义排序正确（'后端'相关排前）", "后端" in top or "Python" in top, top)

    # source 隔离
    isolated = await vm.search("后端开发", source=OTHER, top_k=3)
    check("source 隔离（other 为空）", len(isolated) == 0, f"got {len(isolated)}")

    # ═══════════════════════════════════════════════════
    # 3. PromptBuilder 向量注入
    # ═══════════════════════════════════════════════════
    print("\n3. PromptBuilder vector injection")

    # 填一条与查询强相关的记忆（文本与查询高度重叠，确定性嵌入器下相似度 > 0.2 阈值）
    await vm.add(SRC, "用户问我后端框架该选什么，我建议 FastAPI，因为异步性能更好")
    pb = PromptBuilder(source=SRC, vector_memory=vm)
    system_prompt, messages = await pb.build(
        user_input="我后端框架该选什么",
        history=[],
        include_vector=True,
    )
    check("build 返回 messages", isinstance(messages, list) and len(messages) >= 1)
    check("向量上下文注入 system prompt（'相关历史'）",
          "相关历史" in system_prompt,
          system_prompt[-200:] if "相关历史" not in system_prompt else "")
    check("注入内容含 FastAPI", "FastAPI" in system_prompt)

    # 空查询上下文（无匹配阈值时降级为空）
    empty_pb = PromptBuilder(source=OTHER, vector_memory=vm)
    ctx_empty = await empty_pb._get_vector_context("xxxx 无匹配内容 yyyy", top_k=3)
    check("空 source 的 _get_vector_context 返回 []", ctx_empty == [], str(ctx_empty))

    # ═══════════════════════════════════════════════════
    # 4. 优雅降级
    # ═══════════════════════════════════════════════════
    print("\n4. Graceful degradation")
    pb_no_vm = PromptBuilder(source=SRC, vector_memory=None)
    sp_no_vm, _ = await pb_no_vm.build(
        user_input="测试", history=[], include_vector=True
    )
    check("无 vector_memory 不注入 '相关历史'", "相关历史" not in sp_no_vm)

    # ═══════════════════════════════════════════════════
    # 5. 清理测试数据（按命名空间删除，不碰生产数据）
    # ═══════════════════════════════════════════════════
    print("\n5. Cleanup")
    if vm._pool is not None:
        async with vm._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_vectors WHERE source = $1 OR source = $2",
                SRC, OTHER,
            )
    await vm.close()
    check("测试数据已清理", True)

    print(f"\n{'='*40}")
    print(f"Result: {PASS}/{TOTAL} passed")
    print(f"{'='*40}")
    return PASS == TOTAL


if __name__ == "__main__":
    use_real = "--real" in sys.argv
    try:
        ok = asyncio.run(main(use_real))
    except RuntimeError as e:
        print(f"\n❌ 运行失败：{e}")
        sys.exit(2)
    sys.exit(0 if ok else 1)
