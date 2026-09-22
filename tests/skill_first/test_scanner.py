"""Shadow scanner: real private SQLite, fake authenticated gh transport only."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/hermes-pr-review/scripts"))
from pr_review_lib.github import GitHubError
from pr_review_lib.scanner import Scanner, ScannerError, ScanGitHub, load_repos, validate_repos


def pr(number=1, **updates):
    item = {"number": number, "html_url": f"https://github.com/owner/repo/pull/{number}",
            "state": "open", "draft": False, "title": "Title", "body": "Body",
            "head": {"sha": "a" * 40},
            "base": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}}}
    item.update(updates)
    return item


def api(pages, *, headers=None, repositories=None, **kwargs):
    calls = []
    responses = iter(enumerate(pages))
    if repositories is None:
        repositories = {repo: {"full_name": repo, "id": number} for number, repo in enumerate(
            ("owner/repo", "different/repo", "another/repo"), 1)}

    def transport(argv, **options):
        endpoint = argv[-1]
        calls.append(endpoint)
        assert argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
        assert options["timeout"] > 0 and options["stdin"] == subprocess.DEVNULL
        header = ""
        if endpoint in {f"repos/{repo}" for repo in repositories}:
            value = repositories[endpoint.removeprefix("repos/")]
        else:
            index, value = next(responses)
            header = headers[index] if headers else ""
        if isinstance(value, Exception):
            raise value
        body = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess(argv, 0, "HTTP/2.0 200 OK\n" + header + "\n" + body, "")
    return ScanGitHub(transport=transport, **kwargs), calls


def scan(queue, items):
    github, _ = api([items, []] if items else [[]])
    return queue.scan(["owner/repo"], github)


@pytest.mark.parametrize("value", [[], {}, "owner/repo", [" owner/repo"], ["owner/repo\n"],
                                  ["owner/repo/"], ["owner/repo?x"], ["owner/.."], [1],
                                  ["owner/repo", "OWNER/REPO"], ["https://github.com/owner/repo"]])
def test_exact_nonempty_allowlist(value):
    with pytest.raises(ValueError):
        validate_repos(value)


def test_repos_file(tmp_path):
    path = tmp_path / "repos.json"
    path.write_text('["owner/repo"]')
    assert load_repos(path) == ["owner/repo"]
    path.write_text('[]')
    with pytest.raises(ValueError):
        load_repos(path)


def test_canonical_numeric_sentinel_links_for_draft_only_listing(tmp_path):
    repo = "itsreverence/hermes-plugin-pr-review"
    repository_id = 1297652465
    query = "state=open&sort=created&direction=asc&per_page=100&page="
    calls = []

    def transport(argv, **kwargs):
        assert argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
        endpoint = argv[-1]
        calls.append(endpoint)
        headers = ""
        if endpoint == f"repos/{repo}":
            body = {"full_name": repo, "id": repository_id}
        elif endpoint == f"repos/{repo}/pulls?{query}1":
            body = [pr(11, draft=True, html_url=f"https://github.com/{repo}/pull/11",
                       base={"sha": "b" * 40, "repo": {"full_name": repo}})]
        else:
            assert endpoint == f"repos/{repo}/pulls?{query}2"
            body = []
            target = f"https://api.github.com/repositories/{repository_id}/pulls?{query}1"
            headers = "Link: " + ", ".join(f'<{target}>; rel="{rel}"' for rel in ("prev", "last", "first")) + "\n"
        return subprocess.CompletedProcess(argv, 0, "HTTP/2.0 200 OK\n" + headers + "\n" + json.dumps(body), "")

    result = Scanner(tmp_path / "private").scan([repo], ScanGitHub(transport=transport))
    assert result["wakeAgent"] is False and result["candidates"] == []
    assert result["applied_scan"] == 1 and result["repos"] == [repo]
    assert calls == [f"repos/{repo}", *(f"repos/{repo}/pulls?{query}{page}" for page in (1, 2))]


@pytest.mark.parametrize("repo,name", [("OWNER/REPO", "owner/repo"), ("owner/repo", "OWNER/REPO")])
def test_repository_identity_preserves_case_insensitive_github_names(repo, name):
    github, calls = api([[pr()], []], repositories={repo: {"full_name": name, "id": 1}})
    assert github.enumerate([repo])[0]["ref"] == repo + "#1"
    assert calls[0] == "repos/" + repo
    assert all(call.startswith("repos/" + repo) for call in calls)


@pytest.mark.parametrize("metadata", [None, [], {}, {"full_name": "owner/repo"},
                                      {"full_name": "other/repo", "id": 1},
                                      {"full_name": "owner/repo ", "id": 1},
                                      {"full_name": None, "id": 1},
                                      *({"full_name": "owner/repo", "id": value}
                                        for value in (None, True, 0, -1, 1.0, "1"))])
def test_invalid_repository_identity_fails_before_listing(tmp_path, metadata):
    queue = Scanner(tmp_path / "private")
    before = scan(queue, [pr()])
    github, calls = api([[]], repositories={"owner/repo": metadata})
    with pytest.raises(GitHubError, match="repository identity"):
        queue.scan(["owner/repo"], github)
    assert calls == ["repos/owner/repo"]
    assert queue.status() == before


@pytest.mark.parametrize("rel", ["next", "prev", "first", "last"])
@pytest.mark.parametrize("target", [
    "https://api.github.com/repositories/2/pulls?page=2",
    "https://api.github.com/repos/foreign/repo/pulls?page=2",
    "http://api.github.com/repositories/1/pulls?page=2",
    "https://foreign.example/repositories/1/pulls?page=2",
    "https://api.github.com@foreign.example/repositories/1/pulls?page=2",
    "https://api.github.com:443/repositories/1/pulls?page=2",
    "https://api.github.com/repositories/01/pulls?page=2",
    "https://api.github.com/repositories/1/issues?page=2",
])
def test_untrusted_pagination_target_never_followed_or_applied(tmp_path, rel, target):
    queue = Scanner(tmp_path / "private")
    before = scan(queue, [pr()])
    github, calls = api([[pr(2)], []], headers=[f'Link: <{target}>; rel="{rel}"\n', ""])
    with pytest.raises(GitHubError, match="invalid pagination target"):
        queue.scan(["owner/repo"], github)
    assert calls == ["repos/owner/repo",
                     "repos/owner/repo/pulls?state=open&sort=created&direction=asc&per_page=100&page=1"]
    assert queue.status() == before


def test_numeric_next_last_still_fetches_owner_routes_and_empty_sentinel():
    numeric = "https://api.github.com/repositories/1/pulls?page="
    owner = "https://api.github.com/repos/owner/repo/pulls?page="
    headers = [f'Link: <{numeric}2>; rel="next", <{owner}2>; rel="last"\n',
               f'Link: <{owner}1>; rel="prev", <{numeric}1>; rel="first"\n',
               f'Link: <{numeric}2>; rel="prev", <{numeric}2>; rel="last", <{numeric}1>; rel="first"\n']
    github, calls = api([[pr()], [pr(2)], []], headers=headers, max_requests=4, max_pages=3)
    assert [item["number"] for item in github.enumerate(["owner/repo"])] == [1, 2]
    assert calls == ["repos/owner/repo", *(f"repos/owner/repo/pulls?state=open&sort=created&direction=asc&per_page=100&page={page}"
                                         for page in (1, 2, 3))]


@pytest.mark.parametrize("header", [
    'Link: <https://api.github.com/repositories/1/pulls?page=3>; rel="next"\n',
    'Link: <https://api.github.com/repositories/1/pulls?page=3>; rel="last"\n',
])
def test_numeric_links_do_not_weaken_advertised_page_exhaustion(tmp_path, header):
    queue = Scanner(tmp_path / "private")
    before = scan(queue, [pr()])
    github, _ = api([[pr(2)], []], headers=[header, ""])
    with pytest.raises(GitHubError, match="inconsistent next page|ended before advertised pages"):
        queue.scan(["owner/repo"], github)
    assert queue.status() == before


@pytest.mark.parametrize("second_id", [1, 3])
def test_numeric_identity_is_scoped_to_current_enrolled_repository(tmp_path, second_id):
    queue = Scanner(tmp_path / "private")
    before = scan(queue, [pr()])
    github, calls = api([[], []], headers=["", f'Link: <https://api.github.com/repositories/{second_id}/pulls?page=1>; rel="first"\n'],
                        max_pages=1, max_requests=4)
    if second_id == 1:
        with pytest.raises(GitHubError, match="invalid pagination target"):
            queue.scan(["owner/repo", "another/repo"], github)
        assert queue.status() == before
    else:
        assert queue.scan(["owner/repo", "another/repo"], github)["candidates"] == []
    assert len(calls) == 4 and calls[0] == "repos/owner/repo" and calls[2] == "repos/another/repo"


def test_full_pagination_short_page_not_terminal_and_drafts(tmp_path):
    queue = Scanner(tmp_path / "private")
    github, calls = api([[pr(i) for i in range(1, 101)], [pr(101), pr(102, draft=True)], [pr(103)], []])
    result = queue.scan(["owner/repo"], github)
    assert result["wakeAgent"] is True
    assert len(result["candidates"]) == 102
    assert len(calls) == 5 and calls[0] == "repos/owner/repo"
    assert all(f"per_page=100&page={i}" in call for i, call in enumerate(calls[1:], 1))
    assert result["atomic"] is False


@pytest.mark.parametrize("bad", [{}, "{bad", [pr(number=True)], [pr(title=None)],
                               [pr(body=12)], [pr(head={"sha": "bad"})],
                               [pr(base={"sha": "b" * 40, "repo": {"full_name": "other/repo"}})],
                               [pr(html_url="https://github.com/owner/repo/pull/2")],
                               [pr(state="closed")], [pr(draft="false")]])
def test_corrupt_listing_preserves_previous_pending(tmp_path, bad):
    queue = Scanner(tmp_path / "private")
    before = scan(queue, [pr()])["candidates"]
    github, _ = api([bad, []])
    with pytest.raises(GitHubError):
        queue.scan(["owner/repo"], github)
    assert queue.status()["candidates"] == before


def test_partial_duplicate_and_page_budget_are_fail_closed(tmp_path):
    queue = Scanner(tmp_path / "private")
    scan(queue, [pr()])
    for pages, options in [([[pr(2)], OSError("offline")], {}),
                           ([[pr(2)], [pr(2)]], {}),
                           ([[pr(2)]], {"max_pages": 1}),
                           ([[pr(2)]], {"max_requests": 2})]:
        github, _ = api(pages, **options)
        with pytest.raises(GitHubError):
            queue.scan(["owner/repo"], github)
        assert [x["ref"] for x in queue.status()["candidates"]] == ["owner/repo#1"]


def test_deadline(tmp_path):
    ticks = iter([0, 0, 2])
    github, _ = api([[pr()]], clock=lambda: next(ticks), total_timeout=1)
    with pytest.raises(GitHubError, match="time budget"):
        Scanner(tmp_path / "private").scan(["owner/repo"], github)


@pytest.mark.parametrize("change", ["head", "base", "title", "body"])
def test_identity_versions_and_unchanged_completed(tmp_path, change):
    queue = Scanner(tmp_path / "private")
    first = scan(queue, [pr()])["candidates"][0]
    assert scan(queue, [pr()])["candidates"][0] == first
    claim = queue.claim()
    queue.finish(claim["id"], claim["lease_token"], "completed", attempt_id="c" * 32)
    assert scan(queue, [pr()])["wakeAgent"] is False
    item = pr()
    if change in ("head", "base"):
        item[change]["sha"] = "d" * 40
    else:
        item[change] = "Changed"
    current = scan(queue, [item])["candidates"][0]
    assert current["id"] != first["id"] and current["identity"] != first["identity"]
    old = [r for r in queue.status()["versions"] if r["id"] == first["id"]][0]
    assert old["retired_reason"] == "superseded" and old["metadata"][change if change in ("title", "body") else change + "_sha"] != current["metadata"][change if change in ("title", "body") else change + "_sha"]


def test_retry_survives_reopen_identical_scan_and_exhaustion(tmp_path):
    now = [1000]
    root = tmp_path / "private"
    queue = Scanner(root, clock=lambda: now[0], max_attempts=2)
    scan(queue, [pr()])
    claim = queue.claim()
    queue.finish(claim["id"], claim["lease_token"], "failed", retry_after=20)
    queue = Scanner(root, clock=lambda: now[0], max_attempts=2)
    assert scan(queue, [pr()])["wakeAgent"] is False
    now[0] += 20
    assert queue.status()["wakeAgent"] is True
    claim = queue.claim()
    assert claim["attempts"] == 2
    queue.finish(claim["id"], claim["lease_token"], "incomplete")
    assert scan(queue, [pr()])["wakeAgent"] is False
    assert queue.status()["versions"][0]["status"] == "held"


def test_expiry_fences_old_worker_and_retries_crash(tmp_path):
    now = [1000]
    queue = Scanner(tmp_path / "private", clock=lambda: now[0], lease_seconds=10)
    scan(queue, [pr(), pr(2)])
    first = queue.claim()
    assert queue.claim() is None
    now[0] += 11
    second = queue.claim()
    assert second and second["lease_token"] != first["lease_token"]
    with pytest.raises(ScannerError):
        queue.finish(first["id"], first["lease_token"], "completed", attempt_id="a" * 32)
    queue.finish(second["id"], second["lease_token"], "held")
    assert queue.claim() is not None


def test_completed_requires_attempt_and_no_model_ack_cli(tmp_path):
    queue = Scanner(tmp_path / "private")
    scan(queue, [pr()])
    claim = queue.claim()
    with pytest.raises(ValueError):
        queue.finish(claim["id"], claim["lease_token"], "completed")
    cli = Path(__file__).resolve().parents[2] / "skills/hermes-pr-review/scripts/pr_scan.py"
    for arguments in [["scan"], ["finish"], ["claim"]]:
        result = subprocess.run([sys.executable, str(cli), "--state-root", str(tmp_path / "cli"), *arguments], capture_output=True, text=True)
        assert result.returncode != 0
    assert not (tmp_path / "cli").exists()


def test_exclusion_only_after_complete_scan_and_removed_allowlist(tmp_path):
    queue = Scanner(tmp_path / "private")
    scan(queue, [pr(), pr(2)])
    scan(queue, [pr(draft=True)])
    assert queue.status()["wakeAgent"] is False
    assert all(not r["active"] for r in queue.status()["versions"])
    scan(queue, [pr()])
    github, _ = api([[]])
    queue.scan(["different/repo"], github)
    assert queue.claim() is None


def _claim_process(root, barrier, output):
    queue = Scanner(root)
    barrier.wait()
    output.put(queue.claim() is not None)


def test_single_global_worker_across_processes(tmp_path):
    root = tmp_path / "private"
    scan(Scanner(root), [pr(), pr(2)])
    context = multiprocessing.get_context("spawn")
    barrier, output = context.Barrier(2), context.Queue()
    workers = [context.Process(target=_claim_process, args=(root, barrier, output)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert sorted(output.get(timeout=2) for _ in workers) == [False, True]


def test_workflow_revision_requeues_done_and_fences_old_claim(tmp_path):
    queue = Scanner(tmp_path / "private")
    github, _ = api([[pr()], []])
    first = queue.scan(["owner/repo"], github, workflow_key="workflow-v1")["candidates"][0]
    claim = queue.claim()
    queue.finish(claim["id"], claim["lease_token"], "completed", attempt_id="c" * 32)
    github, _ = api([[pr()], []])
    assert not queue.scan(["owner/repo"], github, workflow_key="workflow-v1")["wakeAgent"]
    github, _ = api([[pr()], []])
    second = queue.scan(["owner/repo"], github, workflow_key="workflow-v2")["candidates"][0]
    assert first["identity"] != second["identity"]
    assert second["metadata"]["workflow_key"] == "workflow-v2"
    claim = queue.claim()
    github, _ = api([[]])
    queue.scan(["different/repo"], github, workflow_key="workflow-v2")
    with pytest.raises(ScannerError):
        queue.finish(claim["id"], claim["lease_token"], "completed", attempt_id="c" * 32)


@pytest.mark.parametrize("headers,status", [("Link: <https://api.github.com/repos/owner/repo/pulls?page=2>; rel=\"next\"\n", 200),
                                              ("", 206)])
def test_explicit_partial_http_or_empty_page_with_more_is_rejected(tmp_path, headers, status):
    def transport(argv, **kwargs):
        if argv[-1] == "repos/owner/repo":
            return subprocess.CompletedProcess(argv, 0, 'HTTP/2.0 200 OK\n\n{"full_name":"owner/repo","id":1}', "")
        return subprocess.CompletedProcess(argv, 0, f"HTTP/2.0 {status} OK\n{headers}\n[]", "")
    with pytest.raises(GitHubError):
        Scanner(tmp_path / "private").scan(["owner/repo"], ScanGitHub(transport=transport))


def test_cli_failed_scan_never_wakes_and_preserves_pending(tmp_path, monkeypatch, capsys):
    import pr_scan
    root = tmp_path / "private"
    queue = Scanner(root)
    scan(queue, [pr()])
    repos_file = tmp_path / "repos.json"
    repos_file.write_text('["owner/repo"]')
    github, _ = api([OSError("private provider stderr")])
    monkeypatch.setattr(pr_scan, "ScanGitHub", lambda **kwargs: github)
    assert pr_scan.main(["--state-root", str(root), "scan", "--repos-file", str(repos_file)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["wakeAgent"] is False and result["candidates"] == []
    assert "private provider" not in json.dumps(result)
    assert queue.status()["wakeAgent"] is True


def test_failed_reconciliation_transaction_rolls_back(tmp_path, monkeypatch):
    queue = Scanner(tmp_path / "private")
    before = scan(queue, [pr()])["candidates"]
    with monkeypatch.context() as patcher:
        def fail(*args):
            raise RuntimeError("crash before commit")
        patcher.setattr(queue, "_status", fail)
        with pytest.raises(RuntimeError):
            scan(queue, [pr(2)])
    assert queue.status()["candidates"] == before


def test_slow_scan_cannot_replace_newer_successful_scan(tmp_path):
    queue = Scanner(tmp_path / "private")
    class Slow:
        def enumerate(self, repos):
            scan(queue, [pr(2)])
            github, _ = api([[pr()], []])
            return github.enumerate(repos)
    with pytest.raises(ScannerError, match="superseded"):
        queue.scan(["owner/repo"], Slow())
    assert [row["ref"] for row in queue.status()["candidates"]] == ["owner/repo#2"]


def test_link_pagination_and_global_multi_repo_budget(tmp_path):
    links = ('Link: <https://api.github.com/repos/owner/repo/pulls?page=2>; rel="next", '
             '<https://api.github.com/repos/owner/repo/pulls?page=2>; rel="last"\n')
    github, _ = api([[pr(1)], [pr(2)], []], headers=[links, "", ""])
    queue = Scanner(tmp_path / "private")
    assert len(queue.scan(["owner/repo"], github)["candidates"]) == 2
    github, calls = api([[]], max_requests=3)
    with pytest.raises(GitHubError, match="request budget"):
        queue.scan(["owner/repo", "another/repo"], github)
    assert len(queue.status()["candidates"]) == 2
    assert calls == ["repos/owner/repo", "repos/owner/repo/pulls?state=open&sort=created&direction=asc&per_page=100&page=1",
                     "repos/another/repo"]


def test_rate_limit_retry_shares_request_budget(tmp_path):
    calls, slept = [], []
    def transport(argv, **kwargs):
        calls.append(argv[-1])
        return subprocess.CompletedProcess(argv, 1, "HTTP/2.0 429 Too Many Requests\nRetry-After: 1\n\n{}", "private stderr")
    github = ScanGitHub(transport=transport, max_requests=2, sleep=slept.append)
    with pytest.raises(GitHubError, match="request budget"):
        Scanner(tmp_path / "private").scan(["owner/repo"], github)
    assert len(calls) == 2 and len(slept) == 2


def test_failed_allowlist_change_does_not_revoke_active_claim(tmp_path):
    queue = Scanner(tmp_path / "private")
    scan(queue, [pr()])
    claim = queue.claim()
    github, _ = api([OSError("offline")])
    with pytest.raises(GitHubError):
        queue.scan(["different/repo"], github)
    assert queue.finish(claim["id"], claim["lease_token"], "completed", attempt_id="c" * 32)["status"] == "done"


def test_default_workflow_digest_is_used(tmp_path, monkeypatch):
    from pr_review_lib import workflow
    monkeypatch.setattr(workflow, "workflow_digest", lambda: "a" * 64)
    queue = Scanner(tmp_path / "private")
    first = scan(queue, [pr()])["candidates"][0]
    monkeypatch.setattr(workflow, "workflow_digest", lambda: "b" * 64)
    second = scan(queue, [pr()])["candidates"][0]
    assert first["identity"] != second["identity"]
    assert second["metadata"]["workflow_key"] == "b" * 64


def test_private_symlink_and_hardlink_rejection(tmp_path):
    root = tmp_path / "private"
    queue = Scanner(root)
    link = tmp_path / "linked-root"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        Scanner(link)
    journal = root / "scanner.sqlite3-journal"
    journal.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        queue.status()
    journal.unlink()
    os.link(queue.path, tmp_path / "hardlink")
    with pytest.raises(ValueError):
        queue.status()


def test_private_permissions_and_separate_ledger(tmp_path):
    root = tmp_path / "private"
    queue = Scanner(root)
    scan(queue, [pr()])
    assert root.stat().st_mode & 0o777 == 0o700
    assert queue.path.name == "scanner.sqlite3"
    assert queue.path.stat().st_mode & 0o777 == 0o600
    assert not (root / "state.sqlite3").exists()
    os.chmod(queue.path, 0o644)
    with pytest.raises(ValueError):
        Scanner(root)
    os.chmod(queue.path, 0o600)
    queue.path.write_bytes(b"corrupt")
    with pytest.raises(ScannerError):
        Scanner(root)
    assert queue.path.read_bytes() == b"corrupt"
