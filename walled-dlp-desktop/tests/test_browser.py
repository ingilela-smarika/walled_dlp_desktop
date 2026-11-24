import pytest

from walled_dlp import browser


def test_launch_reuses_active_debug_endpoint(monkeypatch, tmp_path):
    active_file = tmp_path / "DevToolsActivePort"
    active_file.write_text("12345\n/devtools/browser/test\n")
    ws_url = "ws://127.0.0.1:12345/devtools/browser/test"

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(browser, "find_browser", lambda: "chrome")
    monkeypatch.setattr(browser, "_active_ws_url", lambda: ws_url)
    monkeypatch.setattr(browser, "_profile_in_use", lambda: pytest.fail("profile check should not run"))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("browser should not relaunch"))

    assert browser.launch("https://example.com") == ws_url
    assert active_file.exists()


def test_launch_fails_clearly_when_profile_is_locked(monkeypatch, tmp_path):
    active_file = tmp_path / "DevToolsActivePort"
    active_file.write_text("stale")

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(browser, "find_browser", lambda: "chrome")
    monkeypatch.setattr(browser, "_active_ws_url", lambda: None)
    monkeypatch.setattr(browser, "_profile_in_use", lambda: True)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("browser should not launch"))

    with pytest.raises(RuntimeError, match="profile is already in use"):
        browser.launch("https://example.com")

    assert active_file.read_text() == "stale"
