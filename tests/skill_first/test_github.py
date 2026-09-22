"""Network-free contract checks for the standalone, read-only collector."""
import base64
import hashlib
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


def blob_sha(text):
    raw = text.encode() if isinstance(text, str) else text
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def content_response(requested_path, text, **updates):
    raw = text.encode() if isinstance(text, str) else text
    data = {"type": "file", "path": requested_path, "sha": blob_sha(raw), "encoding": "base64",
            "size": len(raw), "content": base64.b64encode(raw).decode()}
    data.update(updates)
    return response(data)


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.meta = metadata()
        self.final_meta: dict | None = None
        self.files = [file_entry()]
        self.tree = {"truncated": False, "tree": []}
        self.docs = {"README.md": "Trusted base documentation"}
        self.sources = {(HEAD, "src/main.py"): "before\nnew\n\ndef surrounding_method():\n    return 42\n",
                        (MERGE_BASE, "src/main.py"): "before\nold\n\ndef surrounding_method():\n    return 41\n",
                        (MERGE_BASE, "src/old.py"): "before\nold\n"}
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
        tree_prefix = "repos/Org/repo/git/trees/"
        if endpoint.startswith(tree_prefix):
            ref = endpoint[len(tree_prefix):].removesuffix("?recursive=1")
            assert ref in {BASE, HEAD, MERGE_BASE}
            texts = self.docs if ref == BASE else {p: t for (r, p), t in self.sources.items() if r == ref}
            entries = {p: {"path": p, "type": "blob", "mode": "100644", "sha": blob_sha(t),
                           "size": len(t.encode() if isinstance(t, str) else t)} for p, t in texts.items()}
            tree = self.tree if ref == BASE else {"truncated": False, "tree": []}
            if not isinstance(tree.get("tree"), list):
                return response(tree)
            for entry in tree["tree"]:
                value = dict(entries.get(entry.get("path"), {}))
                value.update(entry)
                entries[entry.get("path")] = value
            return response({**tree, "sha": "e" * 40, "tree": list(entries.values())})
        prefix = "repos/Org/repo/contents/"
        if endpoint.startswith(prefix):
            from urllib.parse import unquote
            path, ref = endpoint[len(prefix):].split("?ref=")
            assert ref in {BASE, HEAD, MERGE_BASE}
            path = unquote(path)
            texts = self.docs if ref == BASE else {p: t for (r, p), t in self.sources.items() if r == ref}
            if path not in texts:
                return response({"message": "Not Found"}, 404)
            return content_response(path, texts[path])
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
        self.assertNotIn(".github/workflows/test.yml", result["docs"])
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
        self.fake.docs["AGENTS.md"] = "Root guidance"
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
            name = f"docs/architecture-{index}.md"
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
    fake.sources[(HEAD, "x.txt")] = physical_line + trailing_lf
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


@pytest.mark.parametrize("path", ["contributors/emails/[email protected]", "assets/literal*.txt"])
def test_unrelated_literal_paths_in_all_pinned_trees_do_not_block_review(path):
    fake = FakeGitHub()
    fake.docs[path] = "Unrelated base blob"
    for ref in (HEAD, MERGE_BASE):
        fake.sources[(ref, path)] = "Unrelated source blob"
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert set(result["docs"]) == {"README.md"}
    assert {source["path"] for source in result["sources"]} == {"src/main.py"}
    assert not any("/contents/contributors/" in argv[-1] or "/contents/assets/" in argv[-1]
                   for argv, _ in fake.calls)


@pytest.mark.parametrize("status", ["modified", "renamed", "copied"])
@pytest.mark.parametrize("literal,encoded", [("[ab]", "%5Bab%5D"), ("a*b", "a%2Ab")])
def test_changed_literal_paths_and_ancestor_docs_are_exact_not_globs(status, literal, encoded):
    fake = FakeGitHub()
    path = f"src/{literal}/main.py"
    old = f"old/{literal}/main.py" if status != "modified" else path
    fake.files = [file_entry(filename=path, status=status,
                             **({"previous_filename": old} if old != path else {}))]
    fake.sources[(HEAD, path)] = "before\nnew\n"
    fake.sources[(MERGE_BASE, old)] = "before\nold\n"
    matching_sibling = "a" if literal == "[ab]" else "ab"
    fake.sources[(HEAD, f"src/{matching_sibling}/main.py")] = "Do not expand the literal path"
    docs = {f"src/{literal}/AGENTS.md": "Head ancestor guidance",
            f"{old.rsplit('/', 1)[0]}/README.md": "Origin ancestor guidance",
            "docs/architecture [v1]*#?.md": "Architecture guidance"}
    fake.docs.update(docs)
    fake.docs[f"src/{matching_sibling}/AGENTS.md"] = "Unrelated sibling guidance"
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert result["files"][0]["filename"] == path
    assert result["files"][0]["patch"] == PATCH
    if old != path:
        assert result["files"][0]["previous_filename"] == old
    assert result["sources"] == [
        {"path": path, "ref": HEAD, "side": "RIGHT", "text": fake.sources[(HEAD, path)]},
        {"path": old, "ref": MERGE_BASE, "side": "LEFT", "text": fake.sources[(MERGE_BASE, old)]},
    ]
    assert result["docs"] == {"README.md": fake.docs["README.md"], **docs}
    old_dir = "old" if old != path else "src"
    assert {argv[-1] for argv, _ in fake.calls if "/contents/" in argv[-1]} == {
        f"repos/Org/repo/contents/README.md?ref={BASE}",
        f"repos/Org/repo/contents/src/{encoded}/main.py?ref={HEAD}",
        f"repos/Org/repo/contents/{old_dir}/{encoded}/main.py?ref={MERGE_BASE}",
        f"repos/Org/repo/contents/src/{encoded}/AGENTS.md?ref={BASE}",
        f"repos/Org/repo/contents/{old_dir}/{encoded}/README.md?ref={BASE}",
        f"repos/Org/repo/contents/docs/architecture%20%5Bv1%5D%2A%23%3F.md?ref={BASE}",
    }


@pytest.mark.parametrize("failure,reason", [
    ("duplicate", "invalid_tree"), ("truncated", "truncated_tree"),
    ("symlink", "invalid_source"), ("submodule", "invalid_source"),
    ("path", "invalid_source"), ("sha", "invalid_source"), ("content", "invalid_source"),
])
def test_literal_source_paths_retain_exact_evidence_gates(failure, reason):
    fake = FakeGitHub()
    path = "src/[ab]*.py"
    fake.files = [file_entry(filename=path)]
    text = "before\nnew\n"
    for ref in (HEAD, MERGE_BASE):
        fake.sources[(ref, path)] = text
    entry = {"path": path, "type": "blob", "mode": "100644", "sha": blob_sha(text), "size": len(text)}
    tree = {"sha": "e" * 40, "truncated": failure == "truncated", "tree": [entry]}
    if failure == "duplicate":
        tree["tree"].append(dict(entry))
    elif failure == "symlink":
        entry["mode"] = "120000"
    elif failure == "submodule":
        entry.update(mode="160000", type="commit")
    endpoint = f"repos/Org/repo/contents/src/%5Bab%5D%2A.py?ref={HEAD}"
    updates = {"path": {"path": "src/a.py"}, "sha": {"sha": "f" * 40},
               "content": {"content": base64.b64encode(b"x" * len(text)).decode()}}
    if failure in updates:
        fake.overrides[endpoint] = content_response(path, text, **updates[failure])
    fake.overrides[f"repos/Org/repo/git/trees/{HEAD}?recursive=1"] = response(tree)
    result = GitHub(transport=fake).collect(REF)
    assert reason in result["incomplete_reasons"]
    if failure != "truncated":
        assert not any(source["side"] == "RIGHT" for source in result["sources"])
    if failure in {"duplicate", "symlink", "submodule"}:
        assert endpoint not in [argv[-1] for argv, _ in fake.calls]
    else:
        assert endpoint in [argv[-1] for argv, _ in fake.calls]


@pytest.mark.parametrize("stage", ["triage", "review"])
@pytest.mark.parametrize("location", ["tree", "filename", "previous_filename"])
@pytest.mark.parametrize("path", ["../bad", "/absolute", "a/../bad", "a/./bad", "a//bad",
                                  "a/", "a\\b", "a%2fb", "a%252fb", "a\x00b", "a\nb",
                                  "a\x1fb", "a\x7fb", "~user/bad", "-option", " bad", "bad "])
def test_unsafe_repository_paths_still_fail_closed(stage, location, path):
    fake = FakeGitHub()
    if location == "tree":
        fake.tree["tree"].append({"path": path, "type": "blob", "mode": "100644"})
    else:
        fake.files = [file_entry(**{location: path})]
    result = GitHub(transport=fake).collect(REF, stage=stage)
    assert ("invalid_tree" if location == "tree" else "invalid_file") in result["incomplete_reasons"]
    assert all("/contents/" not in argv[-1] or argv[-1].split("/contents/", 1)[1].split("?ref=", 1)[0]
               in {"README.md", "src/main.py"} for argv, _ in fake.calls)


@pytest.mark.parametrize("path", ["docs/[ab].md", "docs/a*.md"])
def test_explicit_document_selectors_remain_glob_disallowing(path):
    fake = FakeGitHub()
    fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps({"extraDocPaths": [path]})
    fake.docs[path] = "Not an automatic instruction document"
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_config" in result["incomplete_reasons"]
    assert path not in result["docs"]


def test_full_sources_include_surrounding_methods_at_head_and_merge_base():
    fake = FakeGitHub()
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert result["sources"] == [
        {"path": "src/main.py", "ref": HEAD, "side": "RIGHT", "text": fake.sources[(HEAD, "src/main.py")]},
        {"path": "src/main.py", "ref": MERGE_BASE, "side": "LEFT", "text": fake.sources[(MERGE_BASE, "src/main.py")]},
    ]
    assert "surrounding_method" in result["sources"][0]["text"]
    assert result["source_omissions"] == []
    assert all("?ref=" + BASE not in argv[-1] for argv, _ in fake.calls if "/contents/src/" in argv[-1])


@pytest.mark.parametrize("status,previous,expected", [
    ("added", None, [("src/main.py", HEAD, "RIGHT")]),
    ("removed", None, [("src/main.py", MERGE_BASE, "LEFT")]),
    ("renamed", "src/old.py", [("src/main.py", HEAD, "RIGHT"), ("src/old.py", MERGE_BASE, "LEFT")]),
    ("copied", "src/old.py", [("src/main.py", HEAD, "RIGHT"), ("src/old.py", MERGE_BASE, "LEFT")]),
])
def test_source_sides_and_old_paths(status, previous, expected):
    fake = FakeGitHub()
    fake.files = [file_entry(status=status, **({"previous_filename": previous} if previous else {}))]
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert [(s["path"], s["ref"], s["side"]) for s in result["sources"]] == expected
    source_calls = [argv[-1] for argv, _ in fake.calls if "/contents/src/" in argv[-1]]
    assert len(source_calls) == len(expected)


def test_triage_does_not_require_or_fetch_sources():
    fake = FakeGitHub()
    fake.sources.clear()
    fake.files[0]["patch"] = None
    result = GitHub(transport=fake).collect(REF, stage="triage")
    assert result["incomplete_reasons"] == []
    assert result["sources"] == []
    assert result["source_omissions"] == []
    assert all(HEAD not in argv[-1] and MERGE_BASE not in argv[-1]
               for argv, _ in fake.calls if "/contents/" in argv[-1] or "/git/trees/" in argv[-1])


def test_explicit_source_paths_are_pinned_quoted_and_deduplicated():
    fake = FakeGitHub()
    path = "lib/missing dependency#1?.py"
    for ref in (HEAD, MERGE_BASE):
        fake.sources[(ref, path)] = "def dependency():\n    return 'read only'\n"
    result = GitHub(transport=fake).collect(REF, source_paths=(path, path, "src/main.py"))
    assert result["incomplete_reasons"] == []
    assert len(result["sources"]) == 4
    assert {(s["path"], s["ref"]) for s in result["sources"] if s["path"] == path} == {
        (path, HEAD), (path, MERGE_BASE)}
    assert len([argv for argv, _ in fake.calls if "missing%20dependency%231%3F.py?ref=" in argv[-1]]) == 2


@pytest.mark.parametrize("paths", [None, "src/main.py", ["../bad"], ["/tmp/x"], ["a%2fb"],
                                   ["a\\b"], ["a//b"], ["a\nb"], ["a*"], ["a[b]"], [1], ["x"] * 25])
def test_invalid_explicit_source_paths_rejected_before_any_request(paths):
    fake = FakeGitHub()
    with pytest.raises(ValueError):
        GitHub(transport=fake).collect(REF, source_paths=paths)
    assert fake.calls == []


def test_triage_rejects_extra_source_requests_instead_of_silently_ignoring():
    fake = FakeGitHub()
    with pytest.raises(ValueError):
        GitHub(transport=fake).collect(REF, stage="triage", source_paths=("src/main.py",))
    assert fake.calls == []


def test_pr_config_cannot_request_extra_sources():
    fake = FakeGitHub()
    fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps({"sourcePaths": ["secrets.py"]})
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_config" in result["incomplete_reasons"]
    assert not any("secrets.py" in argv[-1] for argv, _ in fake.calls)


def test_source_budget_omits_whole_files_and_records_each_missing_side():
    fake = FakeGitHub()
    budget = len(fake.sources[(HEAD, "src/main.py")])
    result = GitHub(transport=fake, max_source_chars=budget).collect(REF, source_paths=("missing.py",))
    assert result["sources"] == [{"path": "src/main.py", "ref": HEAD, "side": "RIGHT",
                                  "text": fake.sources[(HEAD, "src/main.py")]}]
    assert "sources_budget" in result["incomplete_reasons"]
    assert "missing_source" in result["incomplete_reasons"]
    assert result["source_omissions"] == [
        {"path": "src/main.py", "ref": MERGE_BASE, "side": "LEFT", "reason": "sources_budget", "required": True},
        {"path": "missing.py", "ref": HEAD, "side": "RIGHT", "reason": "missing_source", "required": True},
        {"path": "missing.py", "ref": MERGE_BASE, "side": "LEFT", "reason": "missing_source", "required": True},
    ]
    assert not any(f"/contents/src/main.py?ref={MERGE_BASE}" in a[-1] for a, _ in fake.calls)


def test_missing_changed_source_and_extra_dependency_are_not_clean():
    fake = FakeGitHub()
    del fake.sources[(HEAD, "src/main.py")]
    result = GitHub(transport=fake).collect(REF, source_paths=("missing.py",))
    assert "missing_source" in result["incomplete_reasons"]
    assert len(result["source_omissions"]) == 3
    assert all(o["required"] and o["reason"] == "missing_source" for o in result["source_omissions"])


@pytest.mark.parametrize("response_updates", [
    {"path": "other.py"}, {"sha": "f" * 40}, {"sha": None}, {"type": "symlink"},
    {"type": "submodule"}, {"submodule_git_url": "https://example.invalid/repo"},
    {"target": "src/main.py"}, {"size": 1}, {"content": "eA=="}, {"encoding": "none"},
])
def test_source_contents_identity_and_normal_file_are_verified(response_updates):
    fake = FakeGitHub()
    endpoint = f"repos/Org/repo/contents/src/main.py?ref={HEAD}"
    fake.overrides[endpoint] = content_response("src/main.py", fake.sources[(HEAD, "src/main.py")], **response_updates)
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_source" in result["incomplete_reasons"]
    assert not any(s["side"] == "RIGHT" for s in result["sources"])
    assert result["source_omissions"][0]["reason"] == "invalid_source"


@pytest.mark.parametrize("text", [b"\xff", b"\x00binary"])
def test_binary_sources_fail_closed(text):
    fake = FakeGitHub()
    fake.sources[(HEAD, "src/main.py")] = text
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_source" in result["incomplete_reasons"]
    assert not any(s["side"] == "RIGHT" for s in result["sources"])


@pytest.mark.parametrize("mode,kind", [("120000", "blob"), ("160000", "commit"), ("040000", "tree")])
def test_source_tree_rejects_symlinks_submodules_and_directories_before_contents(mode, kind):
    fake = FakeGitHub()
    fake.overrides[f"repos/Org/repo/git/trees/{HEAD}?recursive=1"] = response({
        "sha": "e" * 40, "truncated": False, "tree": [
            {"path": "src/main.py", "sha": blob_sha(fake.sources[(HEAD, "src/main.py")]), "type": kind, "mode": mode}]})
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_source" in result["incomplete_reasons"]
    assert not any(f"/contents/src/main.py?ref={HEAD}" in a[-1] for a, _ in fake.calls)


def test_source_404_and_denial_cannot_masquerade_as_complete():
    fake = FakeGitHub()
    endpoint = f"repos/Org/repo/contents/src/main.py?ref={HEAD}"
    fake.overrides[endpoint] = response({}, 404)
    result = GitHub(transport=fake).collect(REF)
    assert "missing_source" in result["incomplete_reasons"]
    fake.overrides[endpoint] = response({"secret": "PRIVATE"}, 403)
    with pytest.raises(GitHubError, match="403"):
        GitHub(transport=fake).collect(REF)


def test_relevant_docs_exclude_vendor_unrelated_readmes_and_ci_files():
    fake = FakeGitHub()
    wanted = ["AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "src/AGENTS.md", "src/README.rst",
              "src/CLAUDE.md", "ARCHITECTURE.md", "docs/architecture.md", "docs/workflow.md",
              ".github/copilot-instructions.md"]
    unwanted = [f"vendor/lib{i}/README.md" for i in range(50)] + [
        "other/AGENTS.md", "other/README.md", "vendor/lib/architecture.md",
        "docs/translations/es/README.md", ".github/workflows/a.yml", ".github/workflows/b.yaml"]
    fake.docs.update({p: "Relevant guidance" for p in wanted})
    fake.docs.update({p: "x" * 80_000 for p in unwanted})
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert set(result["docs"]) == set(wanted + ["README.md"])
    endpoints = [a[-1] for a, _ in fake.calls if "/contents/" in a[-1]]
    assert all(not any("/contents/" + p + "?" in endpoint for endpoint in endpoints) for p in unwanted)


def test_rename_ancestor_docs_and_trusted_extra_docs_are_required():
    fake = FakeGitHub()
    fake.files = [file_entry(status="renamed", previous_filename="old/location.py")]
    fake.sources[(MERGE_BASE, "old/location.py")] = "before\nold\n"
    fake.docs.update({"old/AGENTS.md": "Old subtree guidance", "src/AGENTS.md": "New subtree guidance",
                      "unrelated/README.md": "Explicit trusted extra",
                      ".github/hermes-pr-reviewer.json": json.dumps({"extraDocPaths": ["unrelated/README.md", "absent.md"]})})
    result = GitHub(transport=fake).collect(REF)
    assert set(result["docs"]) >= {"old/AGENTS.md", "src/AGENTS.md", "unrelated/README.md"}
    assert "missing_document" in result["incomplete_reasons"]
    assert {"path": "absent.md", "ref": BASE, "reason": "missing_document", "required": True} in result["doc_omissions"]


def test_document_known_to_tree_but_missing_contents_fails_closed():
    fake = FakeGitHub()
    fake.overrides[f"repos/Org/repo/contents/README.md?ref={BASE}"] = response({}, 404)
    result = GitHub(transport=fake).collect(REF)
    assert "missing_document" in result["incomplete_reasons"]
    assert {"path": "README.md", "ref": BASE, "reason": "missing_document", "required": True} in result["doc_omissions"]


def test_doc_budget_and_optional_absence_have_distinct_omissions():
    fake = FakeGitHub()
    fake.docs["AGENTS.md"] = "x" * 100
    result = GitHub(transport=fake, max_doc_chars=30).collect(REF)
    assert "docs_budget" in result["incomplete_reasons"]
    assert {"path": "AGENTS.md", "ref": BASE, "reason": "docs_budget", "required": True} in result["doc_omissions"]
    assert {"path": "WORKFLOW.md", "ref": BASE, "reason": "missing_document", "required": False} in result["doc_omissions"]
    assert result["docs"]["README.md"] == fake.docs["README.md"]


@pytest.mark.parametrize("path", ["AGENTS.md", ".github/hermes-pr-reviewer.json"])
def test_trusted_docs_and_config_reject_tree_symlinks_even_when_contents_reports_file(path):
    fake = FakeGitHub()
    fake.docs[path] = "{}"
    fake.tree["tree"] = [{"path": path, "type": "blob", "mode": "120000"}]
    result = GitHub(transport=fake).collect(REF)
    assert ("invalid_config" if path.endswith(".json") else "invalid_document") in result["incomplete_reasons"]
    assert not any("/contents/" + path + "?" in a[-1] for a, _ in fake.calls)


def test_doc_blob_hash_is_verified_against_tree_not_just_contents_metadata():
    fake = FakeGitHub()
    fake.overrides[f"repos/Org/repo/contents/README.md?ref={BASE}"] = content_response("README.md", "forged doc")
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_document" in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]


def test_ignored_copy_cannot_cancel_required_original_source():
    fake = FakeGitHub()
    fake.meta["changed_files"] = 2
    fake.files.append(file_entry(filename="vendor/copy.py", status="copied", previous_filename="src/main.py"))
    fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps({"ignorePatterns": ["vendor/*"]})
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert {(s["path"], s["side"]) for s in result["sources"]} == {("src/main.py", "RIGHT"), ("src/main.py", "LEFT")}
    assert result["source_omissions"] == [{"path": "vendor/copy.py", "ref": HEAD, "side": "RIGHT",
                                           "reason": "ignored_file", "required": False}]


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, None])
def test_source_budget_requires_positive_integer(budget):
    with pytest.raises(ValueError):
        GitHub(max_source_chars=budget)


def test_source_budget_counts_utf8_bytes_and_keeps_empty_files():
    fake = FakeGitHub()
    fake.sources[(HEAD, "src/main.py")] = "éé"
    fake.sources[(MERGE_BASE, "src/main.py")] = ""
    result = GitHub(transport=fake, max_source_chars=3).collect(REF)
    assert result["incomplete_reasons"] == ["sources_budget"]
    assert result["sources"] == [{"path": "src/main.py", "ref": MERGE_BASE, "side": "LEFT", "text": ""}]


def test_source_blob_digest_rejects_same_length_content_with_claimed_correct_sha():
    fake = FakeGitHub()
    original = fake.sources[(HEAD, "src/main.py")]
    fake.overrides[f"repos/Org/repo/contents/src/main.py?ref={HEAD}"] = content_response(
        "src/main.py", "x" * len(original), sha=blob_sha(original))
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_source" in result["incomplete_reasons"]
    assert not any(s["side"] == "RIGHT" for s in result["sources"])


@pytest.mark.parametrize("mode", ["100644", "100755"])
def test_normal_tree_modes_are_read_as_data_only(mode):
    fake = FakeGitHub()
    text = "import os\nos.system('never execute this')\n"
    fake.sources[(HEAD, "src/main.py")] = text
    fake.overrides[f"repos/Org/repo/git/trees/{HEAD}?recursive=1"] = response({
        "sha": "e" * 40, "truncated": False, "tree": [{"path": "src/main.py", "type": "blob",
        "mode": mode, "sha": blob_sha(text), "size": len(text)}]})
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert result["sources"][0]["text"] == text
    assert all(argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"] for argv, _ in fake.calls)


@pytest.mark.parametrize("malformed", ["truncated", "duplicate", "invalid_sha", "invalid_size", "invalid_tree_sha"])
def test_source_tree_gaps_and_ambiguities_fail_closed(malformed):
    fake = FakeGitHub()
    text = fake.sources[(HEAD, "src/main.py")]
    entry = {"path": "src/main.py", "type": "blob", "mode": "100644", "sha": blob_sha(text), "size": len(text)}
    tree = {"sha": "e" * 40, "truncated": malformed == "truncated", "tree": [entry]}
    if malformed == "duplicate":
        tree["tree"].append(dict(entry))
    elif malformed == "invalid_sha":
        entry["sha"] = "bad"
    elif malformed == "invalid_size":
        entry["size"] = True
    elif malformed == "invalid_tree_sha":
        tree["sha"] = "bad"
    fake.overrides[f"repos/Org/repo/git/trees/{HEAD}?recursive=1"] = response(tree)
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"]
    if malformed != "truncated":
        assert not any(s["side"] == "RIGHT" for s in result["sources"])


def test_deep_changed_ancestors_not_sibling_guidance_are_collected():
    fake = FakeGitHub()
    path = "src/package/nested/main.py"
    fake.files = [file_entry(filename=path)]
    for ref in (HEAD, MERGE_BASE):
        fake.sources[(ref, path)] = fake.sources[(ref, "src/main.py")]
    wanted = {"src/AGENTS.md", "src/package/CLAUDE.md", "src/package/nested/README.md"}
    fake.docs.update({p: "Relevant" for p in wanted})
    fake.docs["src/package/sibling/AGENTS.md"] = "Unrelated"
    result = GitHub(transport=fake).collect(REF)
    assert result["incomplete_reasons"] == []
    assert set(result["docs"]) == wanted | {"README.md"}


def test_invalid_preferred_config_never_activates_fallback_ignores():
    fake = FakeGitHub()
    fake.docs[".github/hermes-pr-reviewer.json"] = "{}"
    fake.docs[".hermes/pr-reviewer.json"] = json.dumps({"ignorePatterns": ["*"]})
    fake.tree["tree"] = [{"path": ".github/hermes-pr-reviewer.json", "mode": "120000"}]
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_config" in result["incomplete_reasons"]
    assert result["policy"]["ignorePatterns"] == []
    assert not any("/contents/.hermes/pr-reviewer.json?" in a[-1] for a, _ in fake.calls)


if __name__ == "__main__":
    unittest.main()
