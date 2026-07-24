"""用户仓库 —— SQLite 实现

职责：
  1. 用户注册（username + password → hash → 入库）
  2. 用户登录（username + password → 查库 + bcrypt 验证）
  3. 按 user_id 查询用户信息

设计：
  - 单表、单索引、一个 SQLite 文件，零依赖（标准库 sqlite3）
  - 密码使用 bcrypt 哈希，db 里不存明文
  - 线程安全：每个请求新建连接（SQLite 默认 serialized 模式）
"""

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bcrypt

DB_PATH = Path(__file__).parent.parent.parent / "data" / "users.db"


@dataclass
class User:
    """用户数据模型，对应 users 表的一行记录。"""
    user_id: str
    username: str
    created_at: str


class UserRepository:
    """用户持久化层"""

    def __init__(self, db_path: Path = DB_PATH):
        """初始化用户仓库。

        Args:
            db_path: SQLite 数据库文件路径，默认指向 data/users.db。
                会在构造时自动调用 _ensure_table 建表。

        副作用：构造即访问磁盘创建/连接数据库文件并建表。
        """
        self._db_path = db_path
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        """打开一个 SQLite 连接，并将行工厂设为 sqlite3.Row（支持按列名访问）。"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        """确保 users 表与 username 唯一索引存在（幂等，重复调用安全）。"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    TEXT PRIMARY KEY,
                    username   TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
                ON users(username)
            """)

    # ── 注册 ──────────────────────────────────────────────

    def register(self, username: str, password: str) -> User:
        """注册新用户

        Args:
            username: 2-32 字符，trim 后入库
            password: 最少 4 字符

        Returns:
            新创建的 User 对象

        Raises:
            ValueError: 用户名已存在 / 格式不合法
        """
        username = username.strip()
        if len(username) < 2 or len(username) > 32:
            raise ValueError("用户名长度 2-32 个字符")
        if len(password) < 4:
            raise ValueError("密码至少 4 位")

        user_id = str(uuid.uuid4())
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        from datetime import datetime, timezone
        created_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?)",
                    (user_id, username, password_hash, created_at),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"用户名 '{username}' 已被注册")

        return User(user_id=user_id, username=username, created_at=created_at)

    # ── 登录 ──────────────────────────────────────────────

    def login(self, username: str, password: str) -> User:
        """用户名密码登录

        Returns:
            验证通过返回 User，失败抛出 ValueError
        """
        username = username.strip()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()

        if row is None:
            raise ValueError("用户名或密码错误")

        if not bcrypt.checkpw(
            password.encode("utf-8"),
            row["password_hash"].encode("utf-8"),
        ):
            raise ValueError("用户名或密码错误")

        return User(
            user_id=row["user_id"],
            username=row["username"],
            created_at=row["created_at"],
        )

    # ── 查询 ──────────────────────────────────────────────

    def get_by_id(self, user_id: str) -> Optional[User]:
        """按 user_id 查询用户信息。

        Args:
            user_id: 用户唯一标识。

        Returns:
            Optional[User]: 命中则返回 User 对象，未找到返回 None。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

        if row is None:
            return None

        return User(
            user_id=row["user_id"],
            username=row["username"],
            created_at=row["created_at"],
        )
