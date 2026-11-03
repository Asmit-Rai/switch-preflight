"""The HTML report: escaped, self-contained, the same lines as the text report, a commit link only when pushed."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

from helpers import FIXTURES, RepoCase, git, read_verdict, run_cli, write
from preflight.htmlreport import FOOTER, render_html
from preflight.model import Result, Ruleset
from preflight.pack import commit_url
from preflight.report import summary_line, verdict_line
from preflight.verdict import build_document

TINY = str(FIXTURES / "tiny.toml")
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
RULESET = Ruleset("Flip the Switch Hackathon", 1, "https://example.org", [], [], "r.toml", SHA)
COMMIT = "1a21df49955b2b19a0614032a4f73e8964395074"
ATTACK = "<script>alert(1)</script>"


def doc_for(results: list[Result], commit: str | None = COMMIT, dirty: bool = False) -> dict:
    return build_document(RULESET, Path("/path/to/proj"), commit, dirty, results, "2026-11-04T09:12:00+05:30")


def ok(ident: str = "readme") -> Result:
    return Result(ident, "README exists", "file_exists", True, "PASS", "found README.md", "add a README")


class Page(HTMLParser):
    """Collects the text of elements with an id, every start tag with its attributes, and the CSS."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.by_id: dict[str, str] = {}
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.css = ""
        self._open: list[str | None] = []
        self._in_style = False
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in_style = tag == "style"
        if tag not in ("br", "meta"):
            self._open.append(dict(attrs).get("id"))

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False
        if tag not in ("br", "meta") and self._open:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.css += data
        for ident in filter(None, self._open):
            self.by_id[ident] = self.by_id.get(ident, "") + data

    def hrefs(self) -> list[str]:
        return [attrs.get("href") or "" for tag, attrs in self.tags if "href" in attrs]


class EscapingTests(RepoCase):
    def test_a_script_in_a_detail_comes_out_escaped(self) -> None:
        bad = Result("readme", "t", "file_exists", True, "FAIL", f"file {ATTACK}.md", "")
        page = render_html(doc_for([bad]), self.tmp)
        self.assertIn("file &lt;script&gt;alert(1)&lt;/script&gt;.md", page)
        self.assertNotIn("<script", page.lower())

    def test_every_project_text_is_escaped(self) -> None:
        repo = self.repo({"README.md": '# Demo <img src=x onerror="alert(1)">\n'})
        meta = {"repo_url": 'javascript:alert(1)//"><b>'}
        bad = Result("readme", f"title {ATTACK}", "file_exists", True, "FAIL", "d", f'hint "><svg onload=alert(1)>')
        page = render_html(doc_for([bad]), repo, meta)
        self.assertIn("Demo &lt;img src=x onerror=&quot;alert(1)&quot;&gt;", page)  # the README's H1 is the name
        self.assertIn("title &lt;script&gt;", page)
        self.assertIn("Fix: hint &quot;&gt;&lt;svg onload=alert(1)&gt;", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("<svg", page)
        self.assertNotIn('href="javascript', page)  # not an http(s) URL, so it stays text
        self.assertIn("javascript:alert(1)//&quot;&gt;&lt;b&gt;", page)

    def test_control_and_bidi_characters_are_shown_not_obeyed(self) -> None:
        bad = Result("readme", "t", "file_exists", True, "FAIL", "evil\u202egnp.exe and a\x1bb", "")
        page = render_html(doc_for([bad]), self.tmp)
        self.assertIn("evil\\u202egnp.exe and a\\u001bb", page)
        self.assertNotIn("\u202e", page)


class BannerTests(RepoCase):
    def test_ready(self) -> None:
        page = render_html(doc_for([ok()]), self.tmp)
        self.assertIn('<section class="banner ready"><p class="state">READY</p>', page)
        self.assertIn("Every required check passed.", page)
        self.assertNotIn("NOT READY", page)

    def test_not_ready_names_the_blocking_checks(self) -> None:
        results = [
            Result("license", "t", "file_exists", True, "FAIL", "no LICENSE file found", "add a LICENSE"),
            Result("secret-scan", "t", "secret_scan", True, "SKIP", "offline", "run online"),
            Result("opt", "t", "file_exists", False, "WARN", "meh", "optional"),
            ok(),
        ]
        page = render_html(doc_for(results), self.tmp)
        self.assertIn('<section class="banner not-ready"><p class="state">NOT READY</p>', page)
        self.assertIn("<p>Blocking: license, secret-scan</p>", page)
        for status in ("PASS", "FAIL", "WARN", "SKIP"):
            self.assertIn(f'<span class="pill {status.lower()}">{status}</span>', page)
        self.assertIn('<p class="fix">Fix: add a LICENSE</p>', page)
        self.assertIn('<p class="fix">Fix: optional</p>', page)
        self.assertNotIn("Fix: add a README", page)  # a passing check needs no fix

    def test_a_ready_run_on_a_dirty_tree_says_the_gate_stays_closed(self) -> None:
        page = render_html(doc_for([ok()], dirty=True), self.tmp)
        self.assertIn("the gate stays closed until they are committed", page)
        self.assertIn("had uncommitted changes", page)

    def test_the_gate_message_and_the_approved_fingerprint(self) -> None:
        page = render_html(doc_for([ok()]), self.tmp, gate_message="GATE OK: ready at 1a21df4", approved_sha="A1B2C3D4E5F6")
        self.assertIn("<p>GATE OK: ready at 1a21df4</p>", page)
        self.assertIn("<code>sha256:A1B2C3D4E5F6</code> (the ruleset matches)", page)
        self.assertNotIn("Approved fingerprint", render_html(doc_for([ok()]), self.tmp))


class SameLinesTests(RepoCase):
    def test_summary_and_verdict_lines_equal_the_text_report(self) -> None:
        many = [Result(f"check-number-{n}", "t", "file_exists", True, "FAIL", "x", "") for n in range(40)]
        for doc in (doc_for([ok()]), doc_for([ok(), *many])):
            page = Page(render_html(doc, self.tmp))
            self.assertEqual(page.by_id["summary-line"], summary_line(doc))
            self.assertEqual(page.by_id["verdict-line"], verdict_line(doc))
        self.assertTrue(verdict_line(doc).endswith("check-number-39)"))  # never shortened

    def test_footer_facts_and_numbering(self) -> None:
        results = [ok(), Result("clean", "Clean tree", "git_clean", False, "WARN", "1 change", "commit")]
        page = render_html(doc_for(results), self.tmp)
        self.assertIn(f"<footer>{FOOTER}</footer>", page)
        self.assertEqual(FOOTER, "Made by Preflight. The model proposes, code decides.")
        self.assertIn("Flip the Switch Hackathon v1 <code>sha256:a1b2c3d4e5f6</code>", page)
        self.assertIn("<td>2026-11-04T09:12:00+05:30</td>", page)
        self.assertIn("<tr><td>2</td><td><code>clean</code></td><td>Clean tree</td><td>no</td>", page)


class SelfContainedTests(RepoCase):
    def test_no_script_and_nothing_loaded_from_anywhere(self) -> None:
        repo = self.repo({"README.md": "# Demo\n"})
        git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        head = git(repo, "rev-parse", "HEAD")
        page = render_html(doc_for([ok()], commit=head), repo)
        parsed = Page(page)
        self.assertNotIn("<script", page.lower())
        for tag, attrs in parsed.tags:
            self.assertNotIn(tag, ("script", "link", "img", "iframe", "object", "embed", "source", "video", "audio"))
            self.assertFalse({"src", "srcset", "style", "action"} & set(attrs), (tag, attrs))
            self.assertFalse(any(name.startswith("on") for name in attrs), (tag, attrs))
        self.assertEqual(sorted(parsed.hrefs()), sorted(["https://github.com/o/r", f"https://github.com/o/r/commit/{head}"]))
        self.assertNotIn("url(", parsed.css)
        self.assertNotIn("@import", parsed.css)
        self.assertNotIn("http", parsed.css)
        self.assertIn("default-src 'none'", page)  # and the page forbids loading anything at all

    def test_fifteen_checks_stay_small(self) -> None:
        results = [
            Result(f"check-number-{n}", "A realistic title for check number " + str(n), "file_exists", n % 2 == 0,
                   ["FAIL", "PASS", "SKIP", "PASS"][n % 4] if n % 2 == 0 else "WARN",
                   "a long detail that quotes a file name or two, " * 5, "a fix hint of a usual length, " * 4)
            for n in range(15)
        ]
        self.assertLess(len(render_html(doc_for(results), self.tmp).encode("utf-8")), 25_000)


class CommitLinkTests(RepoCase):
    def pushed_repo(self, origin: str | None = "https://github.com/o/r", pushed: bool = True) -> tuple[Path, str]:
        repo = self.repo({"README.md": "# Demo\n"})
        if origin:
            git(repo, "remote", "add", "origin", origin)
        if pushed:
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # what a push leaves behind, offline
        return repo, git(repo, "rev-parse", "HEAD")

    def test_link_for_a_github_origin_with_the_commit_on_a_remote_tracking_branch(self) -> None:
        repo, head = self.pushed_repo()
        url = f"https://github.com/o/r/commit/{head}"
        self.assertEqual(commit_url(repo, head), url)
        page = render_html(doc_for([ok()], commit=head), repo)
        self.assertIn(f'<a href="{url}" rel="noopener noreferrer"><code>{head[:7]}</code></a>', page)
        self.assertNotIn("not pushed yet", page)

    def test_no_link_while_the_commit_is_only_local(self) -> None:
        repo, head = self.pushed_repo(pushed=False)
        self.assertIsNone(commit_url(repo, head))
        page = render_html(doc_for([ok()], commit=head), repo)
        self.assertIn(f"<code>{head[:7]}</code> (not pushed yet)", page)
        self.assertNotIn("/commit/", page)

    def test_a_new_commit_after_the_push_is_not_pushed_yet(self) -> None:
        repo, _ = self.pushed_repo()
        write(repo, "more.txt", "x")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "more")
        self.assertIsNone(commit_url(repo, git(repo, "rev-parse", "HEAD")))

    def test_no_link_for_a_non_github_origin_or_no_origin(self) -> None:
        for origin in ("https://gitlab.com/o/r.git", None):
            with self.subTest(origin=origin):
                repo, head = self.pushed_repo(origin=origin)
                self.assertIsNone(commit_url(repo, head))
                self.assertIn("(not pushed yet)", render_html(doc_for([ok()], commit=head), repo))

    def test_odd_commit_values_never_reach_git_or_a_link(self) -> None:
        repo, _ = self.pushed_repo()
        for value in (None, "", "--all", "HEAD", "zzzzzzz", "1a2b"):
            self.assertIsNone(commit_url(repo, value))
        self.assertIn("none (the project had no commits)", render_html(doc_for([ok()], commit=None), repo))


class CliTests(RepoCase):
    def ready_repo(self) -> Path:
        repo = self.repo({"README.md": "# Demo\n"})
        git(repo, "remote", "add", "origin", "git@github.com:o/r.git")
        return repo

    def test_run_html_writes_the_report_and_keeps_the_exit_code(self) -> None:
        bad = self.repo({"other.txt": "x"})
        out = self.tmp / "reports" / "bad.html"
        code, stdout, _ = run_cli("run", "--project", str(bad), "--rules", TINY, "--format", "json", "--html", str(out))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["verdict"], "NOT_READY")  # stdout stays pure JSON
        page = out.read_text(encoding="utf-8")
        self.assertIn('<p class="state">NOT READY</p>', page)
        self.assertIn("<p>Blocking: readme</p>", page)
        self.assertEqual(Page(page).by_id["verdict-line"], verdict_line(read_verdict(bad)))
        self.assertEqual(list(out.parent.iterdir()), [out])  # no temp file left behind

        good = self.ready_repo()
        out = self.tmp / "good.html"
        code, stdout, _ = run_cli("run", "--project", str(good), "--rules", TINY, "--html", str(out))
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip().splitlines()[-1], "VERDICT: READY")
        page = out.read_text(encoding="utf-8")
        self.assertIn('<p class="state">READY</p>', page)
        self.assertIn('href="https://github.com/o/r"', page)
        self.assertIn("(not pushed yet)", page)

    def test_run_html_unwritable_path_is_an_error(self) -> None:
        repo = self.ready_repo()
        (self.tmp / "blocker").write_text("a file where a folder should be", encoding="utf-8")
        code, _, err = run_cli("run", "--project", str(repo), "--rules", TINY, "--html", str(self.tmp / "blocker" / "r.html"))
        self.assertEqual(code, 2)
        self.assertIn("cannot write", err)

    def test_pack_html_writes_nothing_while_the_gate_is_closed(self) -> None:
        repo = self.ready_repo()
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "chat")
        write(repo, "dirty.txt", "x")
        md, page = self.tmp / "out" / "SUBMISSION.md", self.tmp / "out" / "report.html"
        code, stdout, _ = run_cli("pack", "--project", str(repo), "--out", str(md), "--html", str(page))
        self.assertEqual(code, 1)
        self.assertIn("PACK REFUSED", stdout)
        self.assertFalse(md.exists())
        self.assertFalse(page.exists())
        self.assertFalse(page.parent.exists())

    def test_pack_html_is_written_next_to_the_pack_when_the_gate_is_open(self) -> None:
        repo = self.ready_repo()
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "chat")
        head = git(repo, "rev-parse", "HEAD")
        fingerprint = read_verdict(repo)["ruleset"]["sha256"][:12]
        md, page = self.tmp / "SUBMISSION.md", self.tmp / "report.html"
        code, stdout, _ = run_cli(
            "pack", "--project", str(repo), "--out", str(md), "--html", str(page), "--approved-sha", fingerprint
        )
        self.assertEqual(code, 0, stdout)
        self.assertIn(f"HTML WRITTEN: {page}", stdout)
        text = page.read_text(encoding="utf-8")
        self.assertIn(f'<p class="state">READY</p><p>GATE OK: ready at {head[:7]}</p>', text)
        self.assertIn(f"<code>sha256:{fingerprint}</code> (the ruleset matches)", text)
        self.assertIn(f'href="https://github.com/o/r/commit/{head}"', text)
        url = f"https://github.com/o/r/commit/{head}"
        self.assertIn(f"| Commit | [`{head[:7]}`]({url}) `{head}` |", md.read_text(encoding="utf-8"))

    def test_the_markdown_commit_row_is_unchanged_without_a_link(self) -> None:
        repo = self.ready_repo()
        run_cli("run", "--project", str(repo), "--rules", TINY, "--format", "chat")
        md = self.tmp / "p.md"
        self.assertEqual(run_cli("pack", "--project", str(repo), "--out", str(md))[0], 0)
        head = git(repo, "rev-parse", "HEAD")
        self.assertIn(f"| Commit | `{head}` |", md.read_text(encoding="utf-8"))
