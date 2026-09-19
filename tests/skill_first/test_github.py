"""Network-free contract checks for the standalone, read-only collector."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "skills/hermes-pr-review/scripts"))
from pr_review_lib.artifacts import read_json, write_json  # noqa: E402
from pr_review_lib.github import GitHub, GitHubError, parse_ref  # noqa: E402
from pr_review_lib.state import State  # noqa: E402
from pr_review_lib.workflow import finalize, prepare  # noqa: E402

BASE = "a" * 40
HEAD = "b" * 40
MERGE_BASE = "d" * 40
REF = "Org/repo#12"
PATCH = "@@ -1,2 +1,2 @@\n before\n-old\n+new"


def metadata(**updates):
    value = {
        "number": 12, "html_url": "https://github.com/Org/repo/pull/12",
        "base": {"sha": BASE, "repo": {"full_name": "Org/repo"}},
        "head": {"sha": HEAD}, "state": "open", "draft": False,
        "title": "Fix", "body": "Untrusted PR text", "changed_files": 1,
    }
    value.update(updates)
    return value


def file_entry(**updates):
    value = {"filename": "src/main.py", "status": "modified", "patch": PATCH,
             "additions": 1, "deletions": 1, "changes": 2}
    value.update(updates)
    return value


def response(body=None, status=200, headers=None, stderr=""):
    lines = [f"HTTP/2.0 {status} Status"]
    lines.extend(f"{key}: {value}" for key, value in (headers or {}).items())
    return subprocess.CompletedProcess([], 0 if status < 400 else 1,
                                       "\r\n".join(lines) + "\r\n\r\n" + json.dumps(body), stderr)


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.meta = metadata()
        self.final_meta: dict | None = None
        self.files = [file_entry()]
        self.tree = {"truncated": False, "tree": []}
        self.docs = {"README.md": "Trusted base documentation"}
        self.overrides = {}
        self.meta_calls = 0

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        assert argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
        assert "--include" in argv
        assert kwargs["timeout"] > 0
        assert not kwargs.get("shell")
        endpoint = argv[-1]
        if endpoint in self.overrides:
            value = self.overrides[endpoint]
            return value() if callable(value) else value
        if endpoint == "repos/Org/repo/pulls/12":
            self.meta_calls += 1
            return response(self.final_meta if self.final_meta and self.meta_calls > 1 else self.meta)
        if endpoint == f"repos/Org/repo/compare/{BASE}...{HEAD}?per_page=100&page=1":
            return response({"files": self.files, "base_commit": {"sha": BASE},
                             "merge_base_commit": {"sha": MERGE_BASE}})
        if endpoint == f"repos/Org/repo/git/trees/{BASE}?recursive=1":
            return response(self.tree)
        prefix = "repos/Org/repo/contents/"
        if endpoint.startswith(prefix):
            from urllib.parse import unquote
            path, ref = endpoint[len(prefix):].split("?ref=")
            assert ref == BASE
            path = unquote(path)
            if path not in self.docs:
                return response({"message": "Not Found"}, 404)
            text = self.docs[path]
            return response({"type": "file", "path": path, "encoding": "base64",
                             "size": len(text.encode()),
                             "content": base64.b64encode(text.encode()).decode()})
        raise AssertionError("Unexpected endpoint: " + endpoint)


class RefTests(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(parse_ref(REF), REF)
        self.assertEqual(parse_ref("https://github.com/Org/repo/pull/12"), REF)
        self.assertEqual(parse_ref("org/.github#1"), "org/.github#1")

    def test_reject_without_repair(self):
        for value in [None, "", " " + REF, REF + "\n", "org/repo#0", "org/repo#01",
                      "org/repo#-1", "org/repo#１", "../repo#1", "org/..#1",
                      "-org/repo#1", "org/-repo#1", "org/repo;whoami#1",
                      "org/repo%2fother#1", "https://evil.test/Org/repo/pull/12",
                      "https://github.com/Org/repo/pull/12/files",
                      "https://github.com/Org/repo/pull/12?x=y",
                      "https://github.com/Org/repo/pull/12#issuecomment-1",
                      "https://github.com/Org/repo/pull/12/", "Org/repo#12junk"]:
            with self.subTest(value=value), self.assertRaises((ValueError, GitHubError)):
                parse_ref(value)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub()
        # Every subprocess boundary is replaced; tests never use real gh/network.
        self.patcher = patch("pr_review_lib.github.subprocess.run", self.fake)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def collect(self, **kwargs):
        return GitHub(sleep=lambda _: None, **kwargs).collect(REF)

    def test_metadata_and_pinned_complete_snapshot(self):
        result = self.collect()
        self.assertEqual(result["repo"], "Org/repo")
        self.assertEqual(result["number"], 12)
        self.assertEqual(result["head_sha"], HEAD)
        self.assertEqual(result["base_sha"], BASE)
        self.assertEqual(result["merge_base_sha"], MERGE_BASE)
        self.assertEqual(result["state"], "open")
        self.assertFalse(result["draft"])
        self.assertEqual(result["files"][0]["patch"], PATCH)
        self.assertEqual(result["docs"]["README.md"], self.fake.docs["README.md"])
        self.assertEqual(result["incomplete_reasons"], [])
        self.assertEqual(self.fake.meta_calls, 2)
        for argv, _ in self.fake.calls:
            self.assertNotIn("POST", argv)
            self.assertNotIn("graphql", argv)
            self.assertNotIn("--paginate", argv)
            self.assertNotIn("/pulls/12/files", argv[-1])

    def test_injected_transport(self):
        self.assertEqual(GitHub(transport=self.fake).metadata(REF)["title"], "Fix")

    def test_metadata_rejects_bad_identity_sha_and_types(self):
        bad = [metadata(number=13), metadata(html_url="https://github.com/other/repo/pull/12"),
               metadata(head={"sha": "b" * 39}), metadata(head={"sha": "x" * 40}),
               metadata(base={"sha": BASE, "repo": {"full_name": "Org/other"}}),
               metadata(changed_files=True), metadata(changed_files=-1), metadata(draft="false"),
               metadata(state="surprise"), metadata(title=[]), metadata(body={}), []]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(GitHubError):
                self.fake.meta = value
                GitHub().metadata(REF)

    def test_docs_discovery_and_quoted_extra_paths(self):
        path = "docs/design notes#1?.md"
        self.fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps(
            {"extraDocPaths": [path], "ignorePatterns": ["**/vendor/**"]})
        self.fake.docs[path] = "design notes"
        for name in ["docs/architecture.md", ".github/workflows/test.yml", "CONTRIBUTING.md", "AGENTS.md"]:
            self.fake.tree["tree"].append({"path": name, "type": "blob", "mode": "100644"})
            self.fake.docs[name] = "trusted text"
        result = self.collect()
        self.assertIn(path, result["docs"])
        self.assertIn(".github/workflows/test.yml", result["docs"])
        self.assertEqual(result["policy"]["ignorePatterns"], ["**/vendor/**"])
        self.assertTrue(any("design%20notes%231%3F.md?ref=" in args[-1] for args, _ in self.fake.calls))

    def test_merge_base_sha_is_validated_not_assumed_equal_to_base(self):
        endpoint = f"repos/Org/repo/compare/{BASE}...{HEAD}?per_page=100&page=1"
        for merge_base in [None, {}, {"sha": "bad"}, {"sha": "f" * 41}, {"sha": []}]:
            with self.subTest(merge_base=merge_base):
                self.fake.overrides[endpoint] = response({
                    "files": self.fake.files, "base_commit": {"sha": BASE},
                    "merge_base_commit": merge_base,
                })
                result = self.collect()
                self.assertIsNone(result["merge_base_sha"])
                self.assertIn("invalid_merge_base", result["incomplete_reasons"])

    def test_explicit_ignored_files_are_retained_not_hidden(self):
        self.fake.files = [file_entry(filename="vendor/blob.bin", patch=None)]
        self.fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps(
            {"ignorePatterns": ["**/vendor/**"]})
        result = self.collect()
        self.assertEqual(result["incomplete_reasons"], [])
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0]["filename"], "vendor/blob.bin")
        self.assertTrue(result["files"][0]["ignored"])
        self.assertIsNone(result["files"][0]["patch"])

    def test_metadata_error_redacts_response_content(self):
        self.fake.meta = metadata(title="PRIVATE", body="PRIVATE", head={"sha": "PRIVATE"})
        with self.assertRaises(GitHubError) as raised:
            GitHub().metadata(REF)
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_invalid_config_fails_closed(self):
        for config in ['{', '[]', '{"graphContext":"on"}', '{"command":"bad"}',
                       '{"extraDocPaths":["../evil"]}', '{"extraDocPaths":["a//b"]}',
                       '{"extraDocPaths":["a\\\\b"]}', '{"extraDocPaths":"README.md"}',
                       '{"ignorePatterns":[1]}', '{"extraDocPaths":["README.md"],"extraDocPaths":[]}']:
            with self.subTest(config=config):
                self.fake.docs[".github/hermes-pr-reviewer.json"] = config
                self.assertIn("invalid_config", self.collect()["incomplete_reasons"])

    def test_optional_not_found_is_not_denied(self):
        endpoint = f"repos/Org/repo/contents/AGENTS.md?ref={BASE}"
        self.fake.overrides[endpoint] = response({"message": "secret"}, 403, stderr="secret")
        with self.assertRaises(GitHubError) as raised:
            self.collect()
        self.assertNotIn("secret", str(raised.exception))
        self.assertIn("403", str(raised.exception))

    def test_denied_docs_rate_limit_is_never_absence(self):
        endpoint = f"repos/Org/repo/contents/README.md?ref={BASE}"
        self.fake.overrides[endpoint] = response({"message": "PRIVATE"}, 429)
        with self.assertRaises(GitHubError) as raised:
            self.collect(max_attempts=1)
        self.assertIn("429", str(raised.exception))
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_document_count_budget_and_trusted_sha(self):
        for index in range(40):
            name = f"docs/{index}/ARCHITECTURE.md"
            self.fake.tree["tree"].append({"path": name, "type": "blob", "mode": "100644"})
            self.fake.docs[name] = "Architecture"
        result = self.collect()
        self.assertIn("docs_count_limit", result["incomplete_reasons"])
        self.assertLessEqual(len(result["docs"]), 32)

    def test_unauthenticated_metadata_never_reads_docs(self):
        self.fake.overrides["repos/Org/repo/pulls/12"] = response({}, 401)
        with self.assertRaises(GitHubError):
            self.collect()
        self.assertEqual(len(self.fake.calls), 1)

    def test_file_cap_and_count_mismatch(self):
        self.fake.meta["changed_files"] = 301
        self.fake.files = [file_entry(filename=f"f{i}.py") for i in range(300)]
        reasons = self.collect()["incomplete_reasons"]
        self.assertIn("compare_file_limit", reasons)
        self.assertIn("file_count_mismatch", reasons)
        self.fake.meta["changed_files"] = 2
        self.fake.files = [file_entry()]
        self.assertIn("file_count_mismatch", self.collect()["incomplete_reasons"])

    def test_missing_binary_malformed_and_truncated_patches(self):
        for changes in [{"patch": None}, {"patch": ""}, {"patch": "Binary files differ"},
                        {"patch": "@@ -1,2 +1,2 @@\n-old\n+new"}, {"additions": 2}]:
            with self.subTest(changes=changes):
                self.fake.files = [file_entry(**changes)]
                self.assertTrue(self.collect()["incomplete_reasons"])

    def test_valid_add_delete_and_no_newline_patches_preserved(self):
        for value in [file_entry(status="added", additions=1, deletions=0, changes=1,
                                 patch="@@ -0,0 +1 @@\n+new\n\\ No newline at end of file"),
                      file_entry(status="removed", additions=0, deletions=1, changes=1,
                                 patch="@@ -1 +0,0 @@\n-old"),
                      file_entry(status="renamed", previous_filename="src/old.py")]:
            with self.subTest(value=value):
                self.fake.files = [value]
                result = self.collect()
                self.assertEqual(result["incomplete_reasons"], [])
                self.assertEqual(result["files"][0]["patch"], value["patch"])

    def test_limits_omit_whole_patch_instead_of_cutting_hunks(self):
        result = self.collect(max_patch_chars=10, max_doc_chars=5)
        self.assertIn("patch_budget", result["incomplete_reasons"])
        self.assertFalse(result["files"][0].get("patch"))
        self.assertIn("docs_budget", result["incomplete_reasons"])
        self.assertLessEqual(sum(map(len, result["docs"].values())), 5)

    def test_truncated_tree_and_malformed_contents(self):
        self.fake.tree["truncated"] = True
        self.assertIn("truncated_tree", self.collect()["incomplete_reasons"])
        endpoint = f"repos/Org/repo/contents/README.md?ref={BASE}"
        for value in [None, [], {"type": "file", "encoding": "base64", "content": "%%%"},
                      {"type": "symlink", "target": "/etc/passwd"}]:
            with self.subTest(value=value):
                self.fake.overrides[endpoint] = response(value)
                self.assertIn("invalid_document", self.collect()["incomplete_reasons"])

    def test_races_detected(self):
        for updates in [{"head": {"sha": "c" * 40}},
                        {"base": {"sha": "c" * 40, "repo": {"full_name": "Org/repo"}}},
                        {"state": "closed"}, {"draft": True}, {"changed_files": 2}]:
            with self.subTest(updates=updates):
                self.fake.meta_calls = 0
                self.fake.final_meta = metadata(**updates)
                result = self.collect()
                self.assertIn("stale_snapshot", result["incomplete_reasons"])
                self.assertEqual(result["head_sha"], HEAD)

    def test_malformed_compare_and_tree_fail_closed(self):
        compare = f"repos/Org/repo/compare/{BASE}...{HEAD}?per_page=100&page=1"
        for value in [[], {}, {"files": None}, {"files": [{}]},
                      {"files": [file_entry(), file_entry()]},
                      {"files": [file_entry(filename="../bad")]},
                      {"files": [file_entry()], "base_commit": {"sha": "c" * 40}}]:
            with self.subTest(value=value):
                self.fake.overrides[compare] = response(value)
                self.assertTrue(self.collect()["incomplete_reasons"])
        self.fake.overrides.pop(compare)
        self.fake.tree = {"tree": "wrong"}
        self.assertIn("invalid_tree", self.collect()["incomplete_reasons"])


class RequestTests(unittest.TestCase):
    def call_with(self, side_effect, **kwargs):
        with patch("pr_review_lib.github.subprocess.run", side_effect=side_effect) as run:
            result = GitHub(**kwargs).metadata(REF)
            return result, run

    def test_rate_limit_retry_after_and_get_only(self):
        slept = []
        result, run = self.call_with(
            [response({}, 429, {"Retry-After": "2"}, "private token"), response(metadata())],
            sleep=slept.append)
        self.assertEqual(slept, [2])
        self.assertEqual(result["number"], 12)
        for call in run.call_args_list:
            argv = call.args[0]
            self.assertEqual(argv[argv.index("--method") + 1], "GET")
            self.assertLessEqual(call.kwargs["timeout"], 20)

    def test_attempt_limit_sanitized_errors(self):
        for status in [401, 403, 429, 500, 503]:
            with self.subTest(status=status):
                with patch("pr_review_lib.github.subprocess.run", return_value=response(
                        {"secret": "PRIVATE"}, status, stderr="PRIVATE")) as run:
                    with self.assertRaises(GitHubError) as raised:
                        GitHub(sleep=lambda _: None, max_attempts=2).metadata(REF)
                    self.assertNotIn("PRIVATE", str(raised.exception))
                    self.assertLessEqual(run.call_count, 2)

    def test_timeouts_bad_json_and_missing_gh_are_sanitized(self):
        for effect in [FileNotFoundError("PRIVATE"),
                       subprocess.TimeoutExpired("PRIVATE", 20, output="PRIVATE"),
                       subprocess.CompletedProcess([], 0, "PRIVATE", "PRIVATE"),
                       subprocess.CompletedProcess([], 1, "", "auth PRIVATE")]:
            with self.subTest(effect=effect):
                with patch("pr_review_lib.github.subprocess.run", side_effect=[effect] if isinstance(effect, Exception)
                           else None, return_value=effect):
                    with self.assertRaises(GitHubError) as raised:
                        GitHub(max_attempts=1).metadata(REF)
                    self.assertNotIn("PRIVATE", str(raised.exception))

    def test_global_request_budget(self):
        fake = FakeGitHub()
        with self.assertRaises(GitHubError) as raised:
            GitHub(transport=fake, max_requests=2).collect(REF)
        self.assertIn("budget", str(raised.exception))
        self.assertEqual(len(fake.calls), 2)

    def test_time_budget_and_retry_after_bound(self):
        times = iter([0, 0, 3])
        fake = FakeGitHub()
        with self.assertRaises(GitHubError):
            GitHub(transport=fake, clock=lambda: next(times), total_timeout=2).metadata(REF)
        with patch("pr_review_lib.github.subprocess.run", return_value=response({}, 429, {"Retry-After": "999999"})) as run:
            with self.assertRaises(GitHubError):
                GitHub(sleep=lambda _: self.fail("must not sleep beyond budget")).metadata(REF)
            self.assertEqual(run.call_count, 1)

    def test_secondary_rate_limit_retry_and_ordinary_denial(self):
        for headers, attempts in [({"Retry-After": "1"}, 2), ({}, 1)]:
            with self.subTest(headers=headers):
                slept = []
                with patch("pr_review_lib.github.subprocess.run", return_value=response(
                        {}, 403, headers)) as run:
                    with self.assertRaises(GitHubError):
                        GitHub(sleep=slept.append, max_attempts=2).metadata(REF)
                    self.assertEqual(run.call_count, attempts)
                    self.assertEqual(len(slept), attempts - 1)

    def test_untrusted_retry_timing_is_redacted(self):
        with patch("pr_review_lib.github.subprocess.run", return_value=response(
                {}, 429, {"Retry-After": "PRIVATE"})):
            with self.assertRaises(GitHubError) as raised:
                GitHub(sleep=lambda _: self.fail("invalid delay must not sleep")).metadata(REF)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_response_size_limit(self):
        with patch("pr_review_lib.github.MAX_RESPONSE_CHARS", 10):
            with patch("pr_review_lib.github.subprocess.run", return_value=response(metadata())):
                with self.assertRaises(GitHubError):
                    GitHub().metadata(REF)

    def test_duplicate_json_keys_fail_closed(self):
        raw = response(metadata())
        raw.stdout = raw.stdout.replace('"number": 12', '"number": 13, "number": 12')
        with patch("pr_review_lib.github.subprocess.run", return_value=raw):
            with self.assertRaises(GitHubError):
                GitHub().metadata(REF)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e"])
@pytest.mark.parametrize("trailing_lf", ["", "\n"])
@pytest.mark.parametrize("line", [1, 999])
def test_collected_patch_citations_use_physical_lf_lines(tmp_path, separator, trailing_lf, line):
    fake = FakeGitHub()
    physical_line = f"real{separator}@@ -0,0 +999 @@{separator}+forged"
    patch_text = f"@@ -0,0 +1 @@\n+{physical_line}{trailing_lf}"
    fake.files = [file_entry(filename="x.txt", status="added", additions=1,
                             deletions=0, changes=1, patch=patch_text)]
    github, state = GitHub(transport=fake), State(tmp_path / "state")
    attempt = prepare(state, github, REF, "review")
    assert attempt["status"] == "prepared"
    directory = state.root / "attempts" / attempt["id"]
    snapshot = read_json(directory / "input.json")["snapshot"]
    assert snapshot["incomplete_reasons"] == []
    assert snapshot["files"][0]["patch"] == patch_text
    payload = {"schema_version": 1, "stage": "review", "coverage": "complete",
               "summary": "Citation regression fixture.", "limitations": [],
               "findings": [{"path": "x.txt", "side": "RIGHT", "line": line,
                             "severity": "warning", "title": "Citation fixture",
                             "evidence": physical_line if line == 1 else "forged",
                             "why_it_matters": "Only physical lines are valid citations.",
                             "suggested_fix": "Retain the complete physical line."}]}
    result_path = tmp_path / "model.json"
    write_json(result_path, payload)
    result = finalize(state, github, attempt["id"], result_path, model="test-fixture")
    if line == 1:
        assert result["status"] == "completed"
        finding = read_json(directory / "result.json")["result"]["findings"][0]
        assert finding["evidence"] == physical_line
        assert finding["commit_sha"] == HEAD
    else:
        assert result["status"] == "failed"
        assert result["error"] == "result_or_input_invalid"
        assert not (directory / "result.json").exists()


@pytest.mark.parametrize("field", ["title", "body"])
@pytest.mark.parametrize("stage", ["triage", "review"])
def test_collection_metadata_race_cannot_reuse_completed_triage(tmp_path, field, stage):
    fake = FakeGitHub()
    github, state = GitHub(transport=fake), State(tmp_path / "state")
    first = prepare(state, github, REF, stage)
    assert first["status"] == "prepared"
    payload = {"schema_version": 1, "stage": stage, "coverage": "complete",
               "summary": "Original metadata assessed.", "limitations": []}
    if stage == "triage":
        payload.update(decision="review", reason="Implementation needs assessment.", confidence="high")
    else:
        payload["findings"] = []
    result_path = tmp_path / "model.json"
    write_json(result_path, payload)
    assert finalize(state, github, first["id"], result_path, model="test-fixture")["status"] == "completed"
    unchanged = prepare(state, github, REF, stage)
    assert unchanged["status"] == "skipped"
    assert unchanged["previous_id"] == first["id"]

    # Initial metadata is still A; only the final collection GET observes B.
    fake.meta_calls = 0
    fake.final_meta = metadata(**{field: "Changed metadata"})
    raced = prepare(state, github, REF, stage)
    assert fake.meta_calls == 2
    if stage == "triage":
        assert raced["status"] == "incomplete"
        assert raced["error"] == "stale_snapshot"
        assert raced["previous_id"] is None
        snapshot = read_json(state.root / "attempts" / raced["id"] / "input.json")["snapshot"]
        assert snapshot[field] == fake.meta[field]
        assert snapshot["incomplete_reasons"] == ["stale_snapshot"]
        # Once B is stable, it needs a fresh triage rather than reusing A.
        assert prepare(state, github, REF, stage)["status"] == "prepared"
    else:
        assert raced["status"] == "skipped"
        assert raced["previous_id"] == first["id"]
    assert state.get(first["id"])["status"] == "completed"


if __name__ == "__main__":
    unittest.main()
