from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.plausible import (
    EVENT_ACCOUNT_CREATED,
    events_api_url,
    site_url,
    track_event,
)


def test_events_api_url_from_script() -> None:
    assert events_api_url("https://plausible.elpatron.me/js/script.js") == (
        "https://plausible.elpatron.me/api/event"
    )
    assert events_api_url("https://plausible.io/js/script.js") == "https://plausible.io/api/event"


def test_track_event_noop_without_domain(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, plausible_domain="")
    with patch("app.plausible.urllib.request.urlopen") as mock_open:
        track_event(settings, EVENT_ACCOUNT_CREATED)
        mock_open.assert_not_called()


def test_track_event_posts_json(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        plausible_domain="ci-bridge.elpatron.me",
        plausible_script_url="https://plausible.elpatron.me/js/script.js",
        webauthn_origin="https://ci-bridge.elpatron.me",
    )
    mock_resp = MagicMock()
    mock_resp.status = 202
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("app.plausible.urllib.request.urlopen", return_value=mock_resp) as mock_open:
        with patch("app.plausible.threading.Thread") as mock_thread:
            # Run the target synchronously instead of spawning a thread.
            def run_inline(target=None, **_kwargs):
                t = MagicMock()
                t.start = lambda: target and target()
                return t

            mock_thread.side_effect = run_inline
            track_event(settings, EVENT_ACCOUNT_CREATED, path="/", props={"x": 1})
            mock_open.assert_called_once()
            req = mock_open.call_args.args[0]
            assert req.full_url == "https://plausible.elpatron.me/api/event"
            body = req.data.decode("utf-8")
            assert "Account Created" in body
            assert "ci-bridge.elpatron.me" in body


def test_site_url(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        webauthn_origin="https://ci-bridge.elpatron.me",
    )
    assert site_url(settings, "/v1/web/upload") == "https://ci-bridge.elpatron.me/v1/web/upload"
