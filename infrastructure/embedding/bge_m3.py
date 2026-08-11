"""
BGE-M3 向量化服务（全局单例模型，SentenceTransformer 本地运行）

提供两个公开接口：
  - embed_query(text)    → 单条查询向量化
  - embed_documents(texts) → 批量文档向量化（不加前缀）

技术栈：
  - BGE-M3（1024 维，sentence-transformers 本地运行，GPU 优先）
  - HF 镜像加速下载（hf-mirror.com）
  - 模型缓存统一存到 E:/robot_system/models/hf_cache
"""

import os
import asyncio
from typing import List

# ---- 全局模型缓存（避免每次调用都重新加载） ----
_global_bge_model = None

# BGE-M3 输出维度
BGE_M3_DIM = 1024
# HuggingFace 镜像（国内网络加速下载）
HF_MIRROR = "https://hf-mirror.com"
# HuggingFace 模型缓存目录（统一存到项目内，避免散落 C 盘 / 重复下载）
HF_HOME = "E:/robot_system/models/hf_cache"


def _load_bge_m3():
    """惰性加载 BGE-M3 模型，全局单例。GPU 可用时自动走 CUDA。

    模型已完整缓存到本地（HF_HOME 指定目录），因此强制离线模式：
    完全不触网，规避国内直连 huggingface.co 超时（WinError 10060）。
    """
    global _global_bge_model
    if _global_bge_model is None:
        # 必须用赋值（=）而非 setdefault：若环境已存在 HF_ENDPOINT 等变量，
        # setdefault 不会覆盖，会回退到默认 huggingface.co 导致连接超时。
        os.environ["HF_HOME"] = HF_HOME
        os.environ["HF_ENDPOINT"] = HF_MIRROR
        # 模型已本地缓存，强制离线：huggingface_hub 只走本地缓存、不发任何网络请求
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers 未安装，请先执行：\n"
                "  pip install sentence-transformers torch\n"
                f"原始错误：{e}"
            )
        try:
            _global_bge_model = SentenceTransformer("BAAI/bge-m3", local_files_only=True)
        except Exception as e:
            raise RuntimeError(
                "加载 BGE-M3 模型失败。模型应已缓存到：\n"
                f"  {HF_HOME}\n"
                "若缓存缺失，请联网执行以下代码下载（已配镜像 hf-mirror.com）：\n"
                "  >>> import os\n"
                "  >>> os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'\n"
                "  >>> from sentence_transformers import SentenceTransformer\n"
                "  >>> SentenceTransformer('BAAI/bge-m3')\n"
                f"原始错误：{e}"
            )
    return _global_bge_model


async def embed_query(text: str) -> List[float]:
    """
    单条查询向量化。

    用于：VectorMemory.search()、知识库检索等查询场景。
    BGE-M3 直接编码查询文本，不使用旧版 BGE/E5 风格的英文检索前缀。
    内部调用 model.encode(text, normalize_embeddings=True)，
    通过 asyncio.to_thread 卸载到线程池，避免阻塞事件循环。
    """
    def _do_encode():
        model = _load_bge_m3()
        return model.encode(text, normalize_embeddings=True)

    emb = await asyncio.to_thread(_do_encode)
    if hasattr(emb, "tolist"):
        return emb.tolist()
    return list(emb)


async def embed_documents(texts: List[str]) -> List[List[float]]:
    """
    批量文档向量化（不加查询前缀）

    用于：VectorMemory.add()、知识库入库等存储场景。
    内部调用 model.encode(texts, normalize_embeddings=True, batch_size=16)，
    通过 asyncio.to_thread 卸载到线程池，避免阻塞事件循环。
    """
    def _do_encode():
        model = _load_bge_m3()
        return model.encode(texts, normalize_embeddings=True, batch_size=16)

    emb = await asyncio.to_thread(_do_encode)
    if hasattr(emb, "tolist"):
        return emb.tolist()
    return [list(v) for v in emb]


async def warmup() -> None:
    """Load the local embedding model without blocking the event loop."""
    await asyncio.to_thread(_load_bge_m3)
