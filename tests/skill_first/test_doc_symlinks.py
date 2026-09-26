"""Document-only symlink resolution; all GitHub reads use fixture transports."""
import base64
import json

import pytest

from test_github import (BASE, HEAD, MERGE_BASE, REF, FakeGitHub, blob_sha,
                         content_response, response)
from pr_review_lib.artifacts import read_json, write_json
from pr_review_lib.github import GitHub, GitHubError
from pr_review_lib.state import State, StateError
from pr_review_lib.workflow import finalize, prepare


def fixture(links=None, documents=None):
    fake = FakeGitHub()
    fake.docs = dict(documents or {"guide.txt": "Trusted guidance é\n"})
    for path, target in (links or {"README.md": "guide.txt"}).items():
        fake.docs[path] = target
        fake.tree["tree"].append({"path": path, "type": "blob", "mode": "120000"})
    directories = {"/".join(path.split("/")[:i]) for path in fake.docs
                   for i in range(1, len(path.split("/")))}
    for path in sorted(directories):
        fake.tree["tree"].append({"path": path, "type": "tree", "mode": "040000", "sha": "e" * 40})
    return fake


def endpoints(fake):
    return [argv[-1] for argv, _ in fake.calls]


def collect(fake, **kwargs):
    return GitHub(transport=fake, **kwargs).collect(REF, stage="triage")


@pytest.mark.parametrize("links,documents,target", [
    ({"README.md": "guide.txt"}, {"guide.txt": "Root guidance"}, "guide.txt"),
    ({"README.md": "./docs/guide.txt"}, {"docs/guide.txt": "Nested guidance"}, "docs/guide.txt"),
    ({"README.md": "docs/link", "docs/link": "../guide.txt"}, {"guide.txt": "Parent guidance"}, "guide.txt"),
    ({"README.md": "docs/../guide.txt"}, {"docs/unused": "unused", "guide.txt": "Parent guidance"}, "guide.txt"),
    ({"src/AGENTS.md": "../guide.txt"}, {"guide.txt": "Ancestor guidance"}, "guide.txt"),
    ({"README.md": "docs/literal[1]*#?.txt"}, {"docs/literal[1]*#?.txt": "Literal guidance"}, "docs/literal[1]*#?.txt"),
])
def test_document_symlinks_resolve_only_pinned_tree_blobs(links, documents, target):
    fake = fixture(links, documents)
    result = collect(fake)
    selected = next(iter(links))
    assert result["incomplete_reasons"] == []
    assert result["docs"][selected] == documents[target]
    provenance = result["doc_provenance"][selected]
    assert provenance == {"ref": BASE,
                          "links": [{"path": p, "sha": blob_sha(t), "target": t} for p, t in links.items()],
                          "target": {"path": target, "sha": blob_sha(documents[target])}}
    assert result["doc_budget"] == {"limit_bytes": 60_000, "used_bytes": sum(len(t.encode()) for t in links.values()) + len(documents[target].encode())}
    assert all(f"repos/Org/repo/git/blobs/{blob_sha(t)}" in endpoints(fake) for t in links.values())
    assert not any(f"/contents/{p}?" in e for p in links for e in endpoints(fake))
    assert all(BASE in e for e in endpoints(fake) if "/contents/" in e)


@pytest.mark.parametrize("target", ["", "/guide.txt", "//evil.test/guide", "https://evil.test/guide", "file:guide.txt",
                                    "C:/guide.txt", "../guide.txt", "docs/../../guide.txt", "~user/guide", "-option",
                                    " guide.txt", "guide.txt ", "guide.txt\n", "guide\x00.txt", "guide\\txt", "guide%2etxt",
                                    "docs//guide.txt", "docs/", "docs/./../..", "guide.txt/../guide.txt"])
def test_unsafe_document_link_fails_closed_without_destination_read(target):
    fake = fixture({"README.md": target})
    result = collect(fake)
    assert "invalid_document" in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]
    assert not any("/contents/" in e for e in endpoints(fake))


@pytest.mark.parametrize("target", ["alias/guide.txt", "alias/../guide.txt", "absent/../guide.txt"])
def test_directory_symlink_or_absent_directory_cannot_be_lexically_cancelled(target):
    fake = fixture({"README.md": target, "alias": "elsewhere"})
    result = collect(fake)
    assert "invalid_document" in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]
    assert not any("/contents/" in e for e in endpoints(fake))
    assert f"repos/Org/repo/git/blobs/{blob_sha('elsewhere')}" not in endpoints(fake)


@pytest.mark.parametrize("failure", ["missing", "cycle", "self", "hops", "oversize"])
def test_link_chain_limits_omit_whole_document(failure):
    links = {"README.md": "absent"}
    if failure == "cycle":
        links = {"README.md": "link", "link": "README.md"}
    elif failure == "self":
        links = {"README.md": "README.md"}
    elif failure == "hops":
        links = {"README.md": "link0", **{f"link{i}": f"link{i+1}" for i in range(8)}}
    elif failure == "oversize":
        links = {"README.md": "x" * 1025}
    fake = fixture(links)
    result = collect(fake)
    assert ("missing_document" if failure == "missing" else "invalid_document") in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]
    assert len([e for e in endpoints(fake) if "/git/blobs/" in e]) <= 8
    if failure == "oversize":
        assert not any("/git/blobs/" in e for e in endpoints(fake))


def test_eight_link_hops_are_admitted():
    links = {"README.md": "link0", **{f"link{i}": f"link{i+1}" for i in range(6)}, "link6": "guide.txt"}
    fake = fixture(links)
    assert collect(fake)["incomplete_reasons"] == []


@pytest.mark.parametrize("updates", [
    {"sha": "f" * 40}, {"sha": None}, {"size": True}, {"size": 1}, {"type": "tree"},
    {"encoding": "none"}, {"content": "%%%"}, {"content": "////"},
    {"content": base64.b64encode(b"other.txt").decode()},
    {"target": "guide.txt"}, {"submodule_git_url": "https://evil.test"},
])
def test_link_blob_response_identity_encoding_and_hash_are_verified(updates):
    fake = fixture()
    value = {"sha": blob_sha("guide.txt"), "size": 9, "encoding": "base64",
             "content": base64.b64encode(b"guide.txt").decode(), **updates}
    fake.overrides[f"repos/Org/repo/git/blobs/{blob_sha('guide.txt')}"] = response(value)
    result = collect(fake)
    assert "invalid_document" in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]
    assert not any("/contents/" in e for e in endpoints(fake))


@pytest.mark.parametrize("raw", [b"\xff", b"\x00"])
def test_hash_matching_nontext_link_is_rejected(raw):
    result = collect(fixture({"README.md": raw}))
    assert "invalid_document" in result["incomplete_reasons"]


@pytest.mark.parametrize("updates", [{"sha": "bad"}, {"size": True}, {"type": "tree"}, {"mode": "160000"}])
def test_invalid_link_tree_entry_never_fetches_blob(updates):
    fake = fixture()
    fake.tree["tree"][0].update(updates)
    result = collect(fake)
    assert "invalid_document" in result["incomplete_reasons"]
    assert not any("/git/blobs/" in e for e in endpoints(fake))


@pytest.mark.parametrize("updates", [{"sha": "f" * 40}, {"type": "symlink"}, {"size": 1}, {"encoding": "none"},
                                    {"content": "%%%"}, {"content": base64.b64encode(b"Forged guidance").decode()}])
def test_destination_contents_remain_hash_checked(updates):
    fake = fixture(documents={"guide.txt": "Actual guidance"})
    fake.overrides[f"repos/Org/repo/contents/guide.txt?ref={BASE}"] = content_response("guide.txt", "Actual guidance", **updates)
    result = collect(fake)
    assert "invalid_document" in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]
    assert f"repos/Org/repo/contents/guide.txt?ref={BASE}" in endpoints(fake)


@pytest.mark.parametrize("budget", [8, 9, 10, 12, 13])
def test_link_reads_share_document_utf8_budget_without_partial_admission(budget):
    fake = fixture(documents={"guide.txt": "éé"})
    result = collect(fake, max_doc_chars=budget)
    assert result["doc_budget"]["used_bytes"] == (0 if budget < 9 else 9 if budget < 13 else 13)
    if budget < 13:
        assert "docs_budget" in result["incomplete_reasons"]
        assert "README.md" not in result["docs"]
        assert not any("/contents/" in e for e in endpoints(fake))
    else:
        assert result["docs"]["README.md"] == "éé"
        assert result["incomplete_reasons"] == []


def test_failed_chain_link_reads_still_charge_later_documents():
    fake = fixture({"README.md": "absent", "CLAUDE.md": "guide.txt"}, {"guide.txt": "ok"})
    result = collect(fake, max_doc_chars=16)
    assert result["doc_budget"]["used_bytes"] == 15
    assert result["docs"] == {}
    assert set(result["incomplete_reasons"]) == {"missing_document", "docs_budget"}


@pytest.mark.parametrize("endpoint_kind", ["link", "destination"])
def test_missing_blob_response_omits_required_document(endpoint_kind):
    fake = fixture()
    endpoint = (f"repos/Org/repo/git/blobs/{blob_sha('guide.txt')}" if endpoint_kind == "link"
                else f"repos/Org/repo/contents/guide.txt?ref={BASE}")
    fake.overrides[endpoint] = response({}, 404)
    result = collect(fake)
    assert "missing_document" in result["incomplete_reasons"]
    assert "README.md" not in result["docs"]


def test_links_share_global_request_and_deadline_limits():
    fake = fixture()
    with pytest.raises(GitHubError, match="request budget"):
        collect(fake, max_requests=4)
    assert len(fake.calls) == 4
    assert "/git/blobs/" in endpoints(fake)[-1]
    fake = fixture()
    now = [0]
    def slow_link():
        now[0] = 121
        return response({})
    fake.overrides[f"repos/Org/repo/git/blobs/{blob_sha('guide.txt')}"] = slow_link
    with pytest.raises(GitHubError, match="time budget"):
        collect(fake, clock=lambda: now[0])


@pytest.mark.parametrize("path", [".github/hermes-pr-reviewer.json", ".hermes/pr-reviewer.json"])
def test_valid_internal_config_symlink_is_still_rejected(path):
    fake = fixture({path: "../policy.json"}, {"policy.json": "{}"})
    result = collect(fake)
    assert "invalid_config" in result["incomplete_reasons"]
    assert not any("/git/blobs/" in e or "/contents/" in e for e in endpoints(fake))


@pytest.mark.parametrize("ref", [HEAD, MERGE_BASE])
def test_review_source_symlinks_remain_rejected(ref):
    fake = fixture()
    fake.overrides[f"repos/Org/repo/git/trees/{ref}?recursive=1"] = response({
        "sha": "e" * 40, "truncated": False, "tree": [{"path": "src/main.py", "type": "blob", "mode": "120000",
        "sha": blob_sha("../guide.txt"), "size": 12}]})
    result = GitHub(transport=fake).collect(REF)
    assert "invalid_source" in result["incomplete_reasons"]
    assert not any(s["ref"] == ref for s in result["sources"])
    assert f"repos/Org/repo/git/blobs/{blob_sha('../guide.txt')}" not in endpoints(fake)


@pytest.mark.parametrize("stage", ["review", "triage"])
def test_symlink_provenance_prepare_finalize_and_dedup(tmp_path, stage):
    fake = fixture()
    github, state = GitHub(transport=fake), State(tmp_path / "state")
    attempt = prepare(state, github, REF, stage)
    assert attempt["status"] == "prepared"
    directory = state.root / "attempts" / attempt["id"]
    snapshot = read_json(directory / "input.json")["snapshot"]
    context = (directory / "context.md").read_text()
    assert "## Trusted document provenance" in context
    rendered_provenance = context.split("## Trusted document provenance\n\n", 1)[1].split("\n\n##", 1)[0]
    assert json.loads(rendered_provenance) == snapshot["doc_provenance"]
    payload = {"schema_version": 1, "stage": stage, "coverage": "complete", "summary": "Symlink fixture.", "limitations": []}
    if stage == "review":
        payload["findings"] = []
    else:
        payload.update(decision="review", reason="Fixture review needed.", confidence="high")
    result_path = tmp_path / "result.json"
    write_json(result_path, payload)
    assert finalize(state, github, attempt["id"], result_path, model="test-fixture")["status"] == "completed"
    assert read_json(directory / "result.json")["status"] == "completed"
    assert prepare(state, github, REF, stage)["status"] == "skipped"
    # Equal text at the same fixture commit, but changed link identity must not reuse judgment.
    fake.docs["README.md"] = "./guide.txt"
    assert prepare(state, github, REF, stage)["status"] == "prepared"


def test_unsafe_guidance_cannot_prepare_or_finalize_clean(tmp_path):
    fake = fixture({"README.md": "/etc/passwd"})
    github, state = GitHub(transport=fake), State(tmp_path / "state")
    attempt = prepare(state, github, REF, "review")
    assert attempt["status"] == "incomplete"
    assert "invalid_document" in attempt["error"]
    with pytest.raises(StateError, match="only active prepared"):
        finalize(state, github, attempt["id"], tmp_path / "absent.json", model="test-fixture")
