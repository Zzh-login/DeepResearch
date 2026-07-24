"""JWT 签发与验证

使用 HS256 签名算法。密钥持久化到 data/.jwt_secret，首次启动自动生成。

Token 载荷：
  - sub:   user_id
  - uname: username
  - exp:   过期时间（Unix timestamp）
  - iat:   签发时间
"""

import os
import time
from pathlib import Path

import jwt

SECRET_FILE = Path(__file__).parent.parent.parent / "data" / ".jwt_secret"
TOKEN_EXPIRE_DAYS = 7
ALGORITHM = "HS256"


def _load_or_create_secret() -> str:
    """加载（或首次生成）JWT 签名密钥。

    若密钥文件 data/.jwt_secret 已存在，则读取并返回其中内容；
    否则用 os.urandom 生成 64 字符 hex 字符串，写入文件并尝试设置
    权限为 0600（仅当前用户可读写，失败则忽略），再返回该密钥。

    Returns:
        str: 用于 HS256 签名的密钥字符串。
    """
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()

    secret = os.urandom(32).hex()
    SECRET_FILE.write_text(secret, encoding="utf-8")
    # 限制文件权限（仅当前用户可读写）
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


SECRET = _load_or_create_secret()


def create_token(user_id: str, username: str) -> str:
    """为用户签发 JWT access token。

    载荷包含 sub(user_id)、uname(username)、iat(签发时间)、
    exp(过期时间，默认 7 天)，使用模块级 SECRET 与 HS256 算法签名。

    Args:
        user_id: 用户唯一标识，写入载荷的 sub 字段。
        username: 用户名，写入载荷的 uname 字段。

    Returns:
        str: 已签名的 JWT 字符串。
    """
    now = int(time.time())
    payload = {
        "sub": user_id,
        "uname": username,
        "iat": now,
        "exp": now + TOKEN_EXPIRE_DAYS * 86400,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    """验证 JWT 签名与有效期，返回解码后的载荷。

    使用模块级 SECRET 与 HS256 算法调用 jwt.decode。
    当签名错误、已过期或格式非法时，jwt 库会抛出对应异常
    （ExpiredSignatureError / InvalidTokenError 等），由调用方捕获。

    Args:
        token: 待验证的 JWT 字符串。

    Returns:
        dict: 解码后的 payload（含 sub、uname、iat、exp 等字段）。
    """
    return jwt.decode(token, SECRET, algorithms=[ALGORITHM])
