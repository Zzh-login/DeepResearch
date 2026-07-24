"""FastAPI 依赖注入 —— 从 cookie 提取并验证 JWT，返回 user_id

用法：
  @app.get("/some-route")
  async def some_route(user_id: str = Depends(get_current_user)):
      ...
"""

from fastapi import Request, HTTPException, status

from infrastructure.auth.jwt_handler import verify_token


async def get_current_user(request: Request) -> str:
    """从 cookie 'token' 中提取并验证 JWT，返回 user_id。

    未认证时返回 401，前端据此跳转登录页。
    """
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
        )

    try:
        payload = verify_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
        )

    return payload["sub"]
