"""links_ok against a local server, the video checks against a fake fetch. Fully offline."""

from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
from typing import Any

from helpers import RepoCase, closed_port_url, local_web, no_network
from preflight import net
from preflight.checks.video import parse_duration, youtube_id
from preflight.markdown import extract_links, is_placeholder_host

OK = (200, {}, "fine")
ROUTES: dict[str, Any] = {
    "/ok": OK,
    "/missing": (404, {}, "nope"),
    "/gone": (410, {}, "gone"),
    "/boom": (500, {}, "oops"),
    "/redirect": (302, {"Location": "/ok"}, ""),
    "/redirect-to-missing": (302, {"Location": "/missing"}, ""),
    "/loop": (302, {"Location": "/loop"}, ""),
    "/no-head": {"HEAD": (405, {}, ""), "GET": (200, {}, "fine")},
    "/blocked": (403, {}, "go away"),
}


class MarkdownLinkTests(unittest.TestCase):
    def test_links_are_found_in_prose_markdown_and_html_but_not_in_code(self) -> None:
        text = (
            "See [docs](https://a.dev/docs) and https://b.dev/page, or <https://c.dev/x>.\n"
            '<a href="https://d.dev/y?a=1&amp;b=2">d</a> ![img](https://e.dev/i.png)\n'
            "`https://inline.dev/skip`\n```sh\ncurl https://fenced.dev/skip\n```\n"
            "<!-- https://comment.dev/skip -->\n(https://f.dev/z).\n"
        )
        self.assertEqual(
            extract_links(text),
            ["https://a.dev/docs", "https://b.dev/page", "https://c.dev/x", "https://d.dev/y?a=1&b=2",
             "https://e.dev/i.png", "https://f.dev/z"],
        )

    def test_placeholder_hosts(self) -> None:
        for url in ("http://localhost:3000", "https://docs.example.invalid/setup", "https://example.com",
                    "https://www.example.org/x", "https://app.localhost/", "https://foo.test/"):
            self.assertTrue(is_placeholder_host(url), url)
        for url in ("https://github.com/x", "http://127.0.0.1:8000/", "https://notexample.com/"):
            self.assertFalse(is_placeholder_host(url), url)


class LinksOkTests(RepoCase):
    def check(self, repo: Path, check_type: str, **kwargs: Any):  # type: ignore[override]
        """Most tests here talk to the local server, so they use the real fetch (to 127.0.0.1 only)."""
        kwargs.setdefault("fetch", net.fetch)
        return super().check(repo, check_type, **kwargs)

    def readme(self, web_base: str, *paths: str, extra: str = "") -> Path:
        body = "# Demo\n\n" + "\n".join(f"- [link]({web_base}{p})" for p in paths) + "\n" + extra
        return self.repo({"README.md": body})

    def test_ok_and_redirect_pass(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/ok", "/redirect", "/no-head")
            out = self.check(repo, "links_ok", file="README.md")
        self.assertStatus(out, "PASS", "3 link(s) OK")

    def test_head_is_tried_first_then_get(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/no-head")
            self.check(repo, "links_ok", file="README.md")
        self.assertEqual([m for m, _ in web.requests], ["HEAD", "GET"])

    def test_404_410_and_500_fail_with_the_status(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/ok", "/missing", "/gone", "/boom")
            out = self.check(repo, "links_ok", file="README.md")
        self.assertStatus(out, "FAIL", "3 bad link(s)", "HTTP 404", "HTTP 410", "HTTP 500")

    def test_a_redirect_to_a_missing_page_fails(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/redirect-to-missing")
            self.assertStatus(self.check(repo, "links_ok", file="README.md"), "FAIL", "HTTP 404")

    def test_a_redirect_loop_is_a_dead_link(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/ok", "/loop")
            self.assertStatus(self.check(repo, "links_ok", file="README.md"), "FAIL", "redirect")

    def test_placeholder_hosts_fail_even_offline_and_never_touch_the_network(self) -> None:
        repo = self.repo({"README.md": "[setup](https://docs.example.invalid/setup) and http://localhost:8080/app\n"})
        for offline in (True, False):
            out = self.check(repo, "links_ok", offline=offline, fetch=no_network)
            self.assertStatus(out, "FAIL", "2 bad link(s)", "placeholder host", "docs.example.invalid")

    def test_offline_with_real_links_is_a_skip(self) -> None:
        repo = self.repo({"README.md": "[x](https://github.com/o/n)\n"})
        self.assertStatus(self.check(repo, "links_ok", offline=True, fetch=no_network), "SKIP", "offline", "1 link")

    def test_no_links_passes_even_offline(self) -> None:
        repo = self.repo({"README.md": "# Nothing to see\n"})
        self.assertStatus(self.check(repo, "links_ok", offline=True, fetch=no_network), "PASS", "no links")

    def test_a_blocked_site_is_unverified_not_dead(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/ok", "/blocked")
            out = self.check(repo, "links_ok", file="README.md")
        self.assertStatus(out, "SKIP", "cannot verify 1 link(s)", "HTTP 403")

    def test_ignore_lets_the_ruleset_skip_a_blocked_site(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/ok", "/blocked")
            out = self.check(repo, "links_ok", file="README.md", ignore=["/blocked$"])
        self.assertStatus(out, "PASS", "1 link(s) OK", "1 ignored")

    def test_a_dead_host_is_a_failure_when_other_links_prove_we_are_online(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.repo({"README.md": f"{web.base}/ok and {closed_port_url()}\n"})
            out = self.check(repo, "links_ok", file="README.md")
        self.assertStatus(out, "FAIL", "1 bad link(s)", "connection refused")

    def test_when_nothing_answers_at_all_it_is_a_skip_not_a_failure(self) -> None:
        repo = self.repo({"README.md": f"{closed_port_url()}\n"})
        self.assertStatus(self.check(repo, "links_ok", file="README.md"), "SKIP", "cannot verify", "connection refused")

    def test_more_links_than_max_links_is_a_skip_that_says_so(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.readme(web.base, "/ok", "/redirect", "/no-head")
            out = self.check(repo, "links_ok", file="README.md", max_links=2)
        self.assertStatus(out, "SKIP", "3 links found but max_links is 2", "1 not checked")

    def test_extra_files_and_missing_files(self) -> None:
        with local_web(ROUTES) as web:
            repo = self.repo({"README.md": f"{web.base}/ok\n", "docs/GUIDE.md": f"{web.base}/missing\n"})
            out = self.check(repo, "links_ok", file="README.md", extra_files=["docs/GUIDE.md"])
            self.assertStatus(out, "FAIL", "HTTP 404")
            out = self.check(repo, "links_ok", file="README.md", extra_files=["docs/NOPE.md"])
            self.assertStatus(out, "FAIL", "docs/NOPE.md not found")

    def test_links_in_code_are_not_checked(self) -> None:
        repo = self.repo({"README.md": "Run `curl http://localhost:3000` or:\n```\nhttp://localhost:8080\n```\n"})
        self.assertStatus(self.check(repo, "links_ok", fetch=no_network), "PASS", "no links")

    def test_the_fetch_function_is_injectable(self) -> None:
        seen: list[str] = []

        def fake(url: str, **kwargs: Any) -> net.Response:
            seen.append(url)
            return net.Response(200, url)

        repo = self.repo({"README.md": "[a](https://one.dev/a) [b](https://two.dev/b)\n"})
        out = self.check(repo, "links_ok", fetch=fake)
        self.assertStatus(out, "PASS", "2 link(s) OK")
        self.assertEqual(sorted(seen), ["https://one.dev/a", "https://two.dev/b"])


class NetFetchTests(unittest.TestCase):
    def test_real_fetch_returns_error_statuses_and_follows_redirects(self) -> None:
        with local_web(ROUTES) as web:
            self.assertEqual(net.fetch(web.base + "/ok").status, 200)
            self.assertEqual(net.fetch(web.base + "/missing").status, 404)
            redirected = net.fetch(web.base + "/redirect")
            self.assertEqual((redirected.status, redirected.url), (200, web.base + "/ok"))
            self.assertEqual(net.fetch(web.base + "/ok", method="HEAD").body, b"")
            self.assertEqual(net.fetch(web.base + "/ok", max_bytes=0).body, b"")
            self.assertEqual(net.fetch(web.base + "/ok").text(), "fine")

    def test_no_response_raises_net_error_with_a_reason(self) -> None:
        with self.assertRaises(net.NetError) as caught:
            net.fetch(closed_port_url(), timeout=2)
        self.assertEqual(caught.exception.reason, "connection refused")
        with self.assertRaises(net.NetError):
            net.fetch("http://[bad-url/")


YT_PAGE = '<html>...{"videoDetails":{"videoId":"abc","lengthSeconds":"%d","title":"x"}}...</html>'


class VideoLinkPresentTests(RepoCase):
    def test_pass_and_fail(self) -> None:
        repo = self.repo({"README.md": "Demo: [watch](https://youtu.be/dQw4w9WgXcQ)\n"})
        self.assertStatus(self.check(repo, "video_link_present"), "PASS", "youtu.be")
        for body in ("no video here\n", "see `https://youtu.be/dQw4w9WgXcQ` in code\n"):
            other = self.repo({"README.md": body})
            self.assertStatus(self.check(other, "video_link_present"), "FAIL", "no video link")

    def test_other_hosts_and_custom_pattern(self) -> None:
        repo = self.repo({"README.md": "[v](https://www.loom.com/share/abc123) [x](https://vimeo.com/123)\n"})
        self.assertStatus(self.check(repo, "video_link_present"), "PASS", "loom.com")
        self.assertStatus(self.check(repo, "video_link_present", pattern=r"twitch\.tv"), "FAIL")


class YouTubeParsingTests(unittest.TestCase):
    def test_video_ids_from_every_url_form(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=5",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://youtube.com/shorts/dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=x",
        ):
            self.assertEqual(youtube_id(url), "dQw4w9WgXcQ", url)
        self.assertIsNone(youtube_id("https://www.youtube.com/channel/UCxyz"))
        self.assertIsNone(youtube_id("https://vimeo.com/123"))

    def test_duration_parsing_never_invents_a_value(self) -> None:
        self.assertEqual(parse_duration(YT_PAGE % 167), 167)
        self.assertEqual(parse_duration('<meta itemprop="duration" content="PT2M47S">'), 167)
        self.assertEqual(parse_duration('<meta itemprop="duration" content="PT1H1M1S">'), 3661)
        self.assertIsNone(parse_duration("<html>consent wall</html>"))
        self.assertIsNone(parse_duration('<meta itemprop="duration" content="PT">'))


class VideoMaxSecondsTests(RepoCase):
    YT = "[demo](https://www.youtube.com/watch?v=dQw4w9WgXcQ)\n"

    def page(self, seconds: int | None, status: int = 200):
        def fetch(url: str, **kwargs: Any) -> net.Response:
            self.assertEqual(url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            body = (YT_PAGE % seconds) if seconds is not None else "<html>nothing</html>"
            return net.Response(status, url, {}, body.encode())
        return fetch

    def test_short_enough_passes_and_too_long_fails(self) -> None:
        repo = self.repo({"README.md": self.YT})
        self.assertStatus(self.check(repo, "video_max_seconds", max_seconds=180, fetch=self.page(167)), "PASS", "2:47", "167 s")
        self.assertStatus(self.check(repo, "video_max_seconds", max_seconds=180, fetch=self.page(180)), "PASS")
        self.assertStatus(self.check(repo, "video_max_seconds", max_seconds=180, fetch=self.page(192)), "FAIL", "3:12", "over the 180 s limit")

    def test_unreadable_duration_is_a_skip_never_a_guess(self) -> None:
        repo = self.repo({"README.md": self.YT})
        out = self.check(repo, "video_max_seconds", max_seconds=180, fetch=self.page(None))
        self.assertStatus(out, "SKIP", "cannot read duration; check by hand")
        out = self.check(repo, "video_max_seconds", max_seconds=180, fetch=self.page(120, status=429))
        self.assertStatus(out, "SKIP", "cannot read duration")

    def test_network_problems_and_offline_are_skips(self) -> None:
        repo = self.repo({"README.md": self.YT})

        def down(url: str, **kwargs: Any) -> net.Response:
            raise net.NetError("timed out")

        self.assertStatus(self.check(repo, "video_max_seconds", max_seconds=180, fetch=down), "SKIP", "timed out", "check by hand")
        self.assertStatus(self.check(repo, "video_max_seconds", max_seconds=180, offline=True, fetch=no_network), "SKIP", "offline")

    def test_vimeo_and_no_video_are_skips(self) -> None:
        vimeo = self.repo({"README.md": "[v](https://vimeo.com/123456)\n"})
        self.assertStatus(self.check(vimeo, "video_max_seconds", max_seconds=180, fetch=no_network), "SKIP", "vimeo.com", "check by hand")
        none = self.repo({"README.md": "no video\n"}, name="none")
        self.assertStatus(self.check(none, "video_max_seconds", max_seconds=180, fetch=no_network), "SKIP", "no video link")

    def fake_ffprobe(self, output: str) -> dict[str, str]:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "ffprobe"
        script.write_text(f"#!/bin/sh\necho '{output}'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return {"PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"}

    def test_local_file_uses_ffprobe(self) -> None:
        repo = self.repo({"demo.mp4": b"\0\0not really a video"})
        env = self.fake_ffprobe("95.5")
        out = self.check(repo, "video_max_seconds", max_seconds=180, local_file="demo.mp4", env=env, fetch=no_network)
        self.assertStatus(out, "PASS", "1:36", "limit 180 s")
        env = self.fake_ffprobe("200.0")
        out = self.check(repo, "video_max_seconds", max_seconds=180, local_file="demo.mp4", env=env, fetch=no_network)
        self.assertStatus(out, "FAIL", "3:20", "over the 180 s limit")

    def test_local_file_without_ffprobe_or_with_unreadable_output_is_a_skip(self) -> None:
        repo = self.repo({"demo.mp4": b"\0\0data"})
        empty = self.tmp / "empty-bin"
        empty.mkdir()
        out = self.check(repo, "video_max_seconds", max_seconds=180, local_file="demo.mp4", env={"PATH": str(empty)}, fetch=no_network)
        self.assertStatus(out, "SKIP", "ffprobe not installed")
        out = self.check(repo, "video_max_seconds", max_seconds=180, local_file="demo.mp4", env=self.fake_ffprobe("N/A"), fetch=no_network)
        self.assertStatus(out, "SKIP", "ffprobe could not read the duration")
        out = self.check(repo, "video_max_seconds", max_seconds=180, local_file="missing.mp4", env=self.fake_ffprobe("1"), fetch=no_network)
        self.assertStatus(out, "SKIP", "missing.mp4 not found")


if __name__ == "__main__":
    unittest.main()
