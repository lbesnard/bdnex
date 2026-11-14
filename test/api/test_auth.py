"""The login: a form and a session cookie for browsers, Basic and the token for scripts, and a limit on wrong guesses."""

import base64

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api.test_api import library, settings  # noqa: E402,F401
from bdnex.api import auth  # noqa: E402
from bdnex.api.app import create_app  # noqa: E402

PAGE = {"X-Requested-With": "bdnex"}
BROWSER = {"Accept": "text/html,application/xhtml+xml"}


def app_for(settings, **kw):
    return TestClient(create_app(settings, **kw))


def login(web, name="me", secret="s3cret"):
    return web.post("/api/login", json={"username": name, "password": secret}, headers=PAGE)


def test_a_browser_gets_the_login_form_and_a_script_gets_the_challenge(library):
    web = app_for(library, password="s3cret", username="me")
    page = web.get("/", headers=BROWSER)
    assert page.status_code == 401 and "Log in" in page.text and "www-authenticate" not in page.headers
    script = web.get("/")
    assert script.status_code == 401 and script.headers["www-authenticate"].startswith("Basic") and "Log in" not in script.text
    from_the_page = web.get("/api/stats", headers={"X-Bdnex-Page": "1"})
    assert from_the_page.status_code == 401 and "www-authenticate" not in from_the_page.headers
    assert from_the_page.headers["x-bdnex-login"] == "password"


def test_logging_in_sets_a_session_cookie_that_opens_everything_until_logout(library):
    web = app_for(library, password="s3cret", username="me")
    assert login(web, "me", "nope").status_code == 401
    assert login(web).status_code == 200
    header = web.post("/api/login", json={"username": "me", "password": "s3cret"}, headers=PAGE).headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header and "secure" not in header  # plain http here
    cookie = web.cookies.get(auth.COOKIE)  # the session that is about to be logged out
    assert cookie
    assert web.get("/api/stats").status_code == 200 and "Log in" not in web.get("/", headers=BROWSER).text
    assert web.post("/api/logout").status_code == 403  # needs the page's header
    assert web.post("/api/logout", headers=PAGE).status_code == 200
    assert web.get("/api/stats").status_code == 401
    web.cookies.set(auth.COOKIE, cookie)  # the old cookie is dead on the server, not only deleted in the browser
    assert web.get("/api/stats").status_code == 401


def test_a_login_needs_the_page_header_and_a_secure_cookie_over_https(library):
    web = app_for(library, password="s3cret", username="me")
    assert web.post("/api/login", json={"username": "me", "password": "s3cret"}).status_code == 403
    secure = web.post("/api/login", json={"username": "me", "password": "s3cret"}, headers=PAGE,
                      ).headers
    assert "secure" not in secure["set-cookie"].lower()
    https = TestClient(create_app(library, password="s3cret", username="me"), base_url="https://testserver")
    assert "secure" in login(https).headers["set-cookie"].lower()


def test_five_wrong_guesses_lock_the_client_out_for_a_while_and_the_right_one_does_not_reset_it_early(library):
    web = app_for(library, password="s3cret", username="me")
    for _ in range(auth.MAX_FAILURES):
        assert login(web, "me", "wrong").status_code == 401
    locked = login(web)  # even the right password waits
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    basic = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
    assert web.get("/api/stats", headers=basic).status_code == 429  # the other ways in too


def test_wrong_basic_logins_count_too_but_a_missing_login_does_not(library):
    web = app_for(library, password="s3cret", username="me")
    for _ in range(10):
        assert web.get("/api/stats").status_code == 401  # a browser first asking is not a guess
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()}
    for _ in range(auth.MAX_FAILURES):
        web.get("/api/stats", headers=bad)
    assert web.get("/api/stats", headers=bad).status_code == 429


def test_behind_a_proxy_each_forwarded_client_has_its_own_count(settings, library):
    from bdnex.config import build_settings

    proxied = build_settings({"config_version": 2, "collection_path": str(settings.collection_path), "library": str(settings.library_path),
                              "api": {"behind_proxy": True}})[0]
    web = app_for(proxied, password="s3cret", username="me")
    for _ in range(auth.MAX_FAILURES):
        web.post("/api/login", json={"username": "me", "password": "x"}, headers={**PAGE, "X-Forwarded-For": "203.0.113.9"})
    assert web.post("/api/login", json={"username": "me", "password": "s3cret"}, headers={**PAGE, "X-Forwarded-For": "203.0.113.9"}).status_code == 429
    assert web.post("/api/login", json={"username": "me", "password": "s3cret"}, headers={**PAGE, "X-Forwarded-For": "198.51.100.4"}).status_code == 200
    unproxied = app_for(library, password="s3cret", username="me")  # the header is ignored unless asked for
    for _ in range(auth.MAX_FAILURES):
        unproxied.post("/api/login", json={"username": "me", "password": "x"}, headers={**PAGE, "X-Forwarded-For": "1.1.1.1"})
    assert unproxied.post("/api/login", json={"username": "me", "password": "s3cret"}, headers={**PAGE, "X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_the_throttle_and_the_sessions_in_isolation():
    now = [0.0]
    throttle = auth.Throttle(clock=lambda: now[0])
    for _ in range(auth.MAX_FAILURES):
        throttle.failed("a")
    assert throttle.wait("a") > 0 and throttle.wait("b") == 0
    now[0] += auth.LOCK + 1
    assert throttle.wait("a") == 0
    throttle.failed("a"); throttle.succeeded("a")
    assert throttle._failures == {}
    clock = [0.0]
    sessions = auth.Sessions(hours=1, clock=lambda: clock[0])
    token = sessions.start()
    assert sessions.valid(token) and not sessions.valid("nope") and not sessions.valid(None)
    clock[0] = 3601
    assert not sessions.valid(token)


def test_every_answer_has_the_safety_headers_and_a_huge_body_is_refused(library):
    web = app_for(library)
    page = web.get("/")
    assert page.headers["x-frame-options"] == "DENY" and page.headers["x-content-type-options"] == "nosniff"
    csp = page.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "content-security-policy" not in web.get("/docs").headers  # the API docs page loads its script from a CDN
    big = web.post("/api/jobs", content=b"x" * 10, headers={"content-length": "5000000", "content-type": "application/json"})
    assert big.status_code == 413


def test_the_login_form_files_are_served_and_the_static_page_has_no_inline_script(library):
    web = app_for(library, password="x")
    assert web.get("/static/login.js").status_code == 200 and web.get("/static/app.css").status_code == 200
    from pathlib import Path
    index = (Path(auth.__file__).parent / "static" / "index.html").read_text()
    assert "<script>" not in index  # the content security policy would block it
