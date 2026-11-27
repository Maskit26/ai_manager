from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_seller_core.api.error_handlers import handle_unexpected_error


def test_unhandled_http_errors_return_generic_response() -> None:
    app = FastAPI(debug=False)
    app.add_exception_handler(Exception, handle_unexpected_error)

    @app.get("/fail")
    def fail() -> None:
        raise RuntimeError("alice@example.com +1 (415) 555-2671 token=private-token")

    response = TestClient(app, raise_server_exceptions=False).get("/fail")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "Internal server error"
    assert error["request_id"]
    assert "alice@example.com" not in response.text
    assert "private-token" not in response.text
