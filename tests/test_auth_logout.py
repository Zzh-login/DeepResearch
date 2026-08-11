from fastapi.testclient import TestClient

from infrastructure.auth.jwt_handler import create_token
from interfaces.web.app import app


def test_logout_does_not_refresh_deleted_token_cookie():
    client = TestClient(app)
    client.cookies.set(
        "token",
        create_token("logout-test-user", "logout-test-user"),
        domain="testserver.local",
        path="/",
    )

    response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.cookies.get("token") is None

    set_cookie_headers = response.headers.get_list("set-cookie")
    assert len(set_cookie_headers) == 1
    assert "Max-Age=0" in set_cookie_headers[0]
