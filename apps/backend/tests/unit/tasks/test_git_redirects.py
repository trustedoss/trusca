"""Git clones refuse HTTP redirects (security review on #522, N-L2).

The SSRF guard validates a project's URL and pins its address; a redirect
would send the clone to a host nobody checked. The one redirect forges answer
routinely, GitLab's 301 to the same path with ``.git`` appended, is retried
once on the same host.
"""

from __future__ import annotations

import http.server
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tasks.scan_source import build_git_fetch_commands, redirect_retry_url


@pytest.fixture
def redirecting_server() -> Iterator[str]:
    """An HTTP server that answers every request with a 301 elsewhere."""

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            self.send_response(301)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere.git/info/refs")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/acme/widgets"
    finally:
        server.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")
def test_the_clone_command_does_not_follow_a_redirect(
    redirecting_server: str, tmp_path: Path
) -> None:
    (clone,) = build_git_fetch_commands(
        clone_url=redirecting_server, target=tmp_path / "repo", ref=None, resolve_option=None
    )
    done = subprocess.run(clone, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert done.returncode != 0
    assert "returned error: 301" in done.stderr
    assert redirect_retry_url(redirecting_server, done.stderr) == redirecting_server + ".git"


@pytest.mark.parametrize(
    ("url", "error", "expected"),
    [
        (
            "https://gitlab.example.com/group/project",
            "fatal: unable to access: The requested URL returned error: 301",
            "https://gitlab.example.com/group/project.git",
        ),
        (
            "https://user:token@gitlab.example.com/group/project/",
            "The requested URL returned error: 302",
            "https://user:token@gitlab.example.com/group/project.git",
        ),
        ("https://gitlab.example.com/group/project.git", "returned error: 301", None),
        ("https://gitlab.example.com/group/project", "returned error: 404", None),
        ("ssh://git@gitlab.example.com/group/project", "returned error: 301", None),
    ],
)
def test_only_a_refused_redirect_on_an_http_url_without_git_is_retried(
    url: str, error: str, expected: str | None
) -> None:
    assert redirect_retry_url(url, error) == expected
