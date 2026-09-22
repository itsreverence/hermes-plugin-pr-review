"""Offline regressions for explicitly budgeted, SHA-pinned dependency evidence."""
import json

import pytest

from test_github import (
    BASE, HEAD, MERGE_BASE, REF, FakeGitHub, GitHub, GitHubError,
    blob_sha, content_response, file_entry, response,
)


MAIN = "src/main.py"
DEP = "lib/nested/dependency.py"


def add_source(fake, path, right, left=None):
    fake.sources[(HEAD, path)] = right
    fake.sources[(MERGE_BASE, path)] = right if left is None else left


def fixture():
    fake = FakeGitHub()
    add_source(fake, MAIN, "main", "base")
    add_source(fake, DEP, "éé", "old!")
    return fake


def collect(fake, *, paths=(DEP,), dependency_limit=8, source_limit=8, **kwargs):
    return GitHub(transport=fake, max_source_chars=source_limit,
                  max_dependency_chars=dependency_limit, **kwargs).collect(REF, source_paths=paths)


def source(path, ref, side, text):
    return {"path": path, "ref": ref, "side": side, "text": text}


def telemetry(mode, source_limit, source_used, dependency_limit=None, dependency_used=0):
    return {"mode": mode, "source_limit_bytes": source_limit, "source_used_bytes": source_used,
            "dependency_limit_bytes": dependency_limit, "dependency_used_bytes": dependency_used}


def test_omitted_dependency_budget_preserves_default_shared_400k_accounting():
    fake = fixture()
    add_source(fake, MAIN, "x" * 171_755, "y" * 171_756)
    add_source(fake, DEP, "z" * 30_949)
    result = GitHub(transport=fake).collect(REF, source_paths=(DEP, DEP, MAIN))
    assert result["incomplete_reasons"] == ["sources_budget"]
    assert [(s["path"], s["side"]) for s in result["sources"]] == [
        (MAIN, "RIGHT"), (MAIN, "LEFT"), (DEP, "RIGHT")]
    assert result["source_omissions"] == [
        {"path": DEP, "ref": MERGE_BASE, "side": "LEFT", "reason": "sources_budget", "required": True}]
    assert result["sources"][-1]["text"] == "z" * 30_949


def test_shared_telemetry_counts_all_admitted_source_bytes_in_one_pool():
    result = collect(fixture(), dependency_limit=None, source_limit=16)
    assert result["incomplete_reasons"] == []
    assert result["source_budget"] == telemetry("shared", 16, 16)


def test_default_and_explicit_none_match_including_requests():
    omitted, explicit = fixture(), fixture()
    first = GitHub(transport=omitted, max_source_chars=12).collect(REF, source_paths=(DEP,))
    second = collect(explicit, dependency_limit=None, source_limit=12)
    assert first == second
    assert [a for a, _ in omitted.calls] == [a for a, _ in explicit.calls]


def test_split_equality_keeps_full_utf8_text_and_both_pinned_sides():
    fake = fixture()
    result = collect(fake)
    assert result["incomplete_reasons"] == []
    assert result["source_omissions"] == []
    assert result["sources"] == [source(MAIN, HEAD, "RIGHT", "main"),
                                  source(MAIN, MERGE_BASE, "LEFT", "base"),
                                  source(DEP, HEAD, "RIGHT", "éé"),
                                  source(DEP, MERGE_BASE, "LEFT", "old!")]
    assert result["source_budget"] == telemetry("split", 8, 8, 8, 8)
    assert all(BASE not in argv[-1] for argv, _ in fake.calls if f"/contents/{DEP}?" in argv[-1])


def test_real_size_split_preserves_both_original_dependency_copies():
    fake = fixture()
    add_source(fake, MAIN, "x" * 171_755, "y" * 171_756)
    add_source(fake, DEP, "z" * 30_949)
    result = collect(fake, source_limit=400_000, dependency_limit=61_898)
    assert result["incomplete_reasons"] == []
    assert result["source_budget"] == telemetry("split", 400_000, 343_511, 61_898, 61_898)
    assert [s["text"] for s in result["sources"] if s["path"] == DEP] == ["z" * 30_949] * 2


def test_dependency_overflow_omits_whole_side_without_using_free_source_budget():
    fake = fixture()
    result = collect(fake, dependency_limit=7, source_limit=400_000)
    assert result["incomplete_reasons"] == ["dependency_sources_budget"]
    assert result["source_budget"] == telemetry("split", 400_000, 8, 7, 4)
    assert result["source_omissions"] == [
        {"path": DEP, "ref": MERGE_BASE, "side": "LEFT", "reason": "dependency_sources_budget", "required": True}]
    assert f"repos/Org/repo/contents/{DEP}?ref={MERGE_BASE}" not in [a[-1] for a, _ in fake.calls]


def test_dependency_budget_counts_utf8_and_keeps_empty_side():
    fake = fixture()
    add_source(fake, DEP, "éé", "")
    result = collect(fake, dependency_limit=3)
    assert result["incomplete_reasons"] == ["dependency_sources_budget"]
    assert result["sources"][-1] == source(DEP, MERGE_BASE, "LEFT", "")
    assert not any(s["path"] == DEP and s["side"] == "RIGHT" for s in result["sources"])
    assert result["source_budget"] == telemetry("split", 8, 8, 3, 0)


@pytest.mark.parametrize("ignored", [False, True])
@pytest.mark.parametrize("status", ["modified", "renamed", "copied", "added", "removed"])
def test_every_changed_path_and_old_name_stay_in_source_pool(status, ignored):
    fake = fixture()
    old = "legacy/original.py"
    updates = {"status": status}
    if status in {"renamed", "copied"}:
        updates["previous_filename"] = old
        add_source(fake, old, "old!", "past")
    fake.files = [file_entry(**updates)]
    if ignored:
        fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps({"ignorePatterns": [MAIN]})
    paths = (MAIN, old, DEP) if "previous_filename" in updates else (MAIN, DEP)
    result = collect(fake, paths=paths, source_limit=4, dependency_limit=400_000)
    assert result["incomplete_reasons"] == ["sources_budget"]
    assert result["source_budget"] == telemetry("split", 4, 4, 400_000, 8)
    assert len([s for s in result["sources"] if s["path"] in {MAIN, old}]) == 1
    assert all(o["reason"] == "sources_budget" and o["required"] for o in result["source_omissions"])
    assert {s["side"] for s in result["sources"] if s["path"] == DEP} == {"LEFT", "RIGHT"}


def test_nonstandard_previous_filename_is_also_reserved_to_changed_pool():
    fake = fixture()
    old = "legacy/original.py"
    fake.files[0]["previous_filename"] = old
    add_source(fake, old, "old!")
    result = collect(fake, paths=(old, DEP))
    assert result["incomplete_reasons"] == ["sources_budget"]
    assert not any(s["path"] == old for s in result["sources"])
    assert result["source_budget"] == telemetry("split", 8, 8, 8, 8)


def test_duplicate_changed_entry_cannot_hide_old_name_from_source_pool():
    fake = fixture()
    old = "legacy/original.py"
    fake.files.append(file_entry(status="copied", previous_filename=old))
    fake.meta["changed_files"] = 2
    add_source(fake, old, "old!")
    result = collect(fake, paths=(old, DEP))
    assert result["incomplete_reasons"] == ["duplicate_file", "sources_budget"]
    assert not any(s["path"] == old for s in result["sources"])
    assert result["source_budget"] == telemetry("split", 8, 8, 8, 8)


def test_order_duplicates_overlaps_and_identical_blobs_keep_original_source_shape():
    fake = fixture()
    old, other = "legacy/original.py", "lib/other.py"
    fake.files = [file_entry(status="renamed", previous_filename=old), file_entry(filename=old)]
    fake.meta["changed_files"] = 2
    for path in (MAIN, old, other, DEP):
        add_source(fake, path, "same")
    paths = (other, old, DEP, MAIN, other, DEP)
    result = collect(fake, paths=paths, source_limit=16, dependency_limit=16)
    assert result["incomplete_reasons"] == []
    expected = [(MAIN, HEAD, "RIGHT"), (old, MERGE_BASE, "LEFT"), (old, HEAD, "RIGHT"),
                (other, HEAD, "RIGHT"), (other, MERGE_BASE, "LEFT"),
                (DEP, HEAD, "RIGHT"), (DEP, MERGE_BASE, "LEFT"), (MAIN, MERGE_BASE, "LEFT")]
    assert result["sources"] == [source(p, r, s, "same") for p, r, s in expected]
    assert result["source_budget"] == telemetry("split", 16, 16, 16, 16)
    contents = [a[-1] for a, _ in fake.calls if "/contents/" in a[-1] and BASE not in a[-1]]
    assert len(contents) == len(set(contents)) == 8


@pytest.mark.parametrize("failure,reason", [("missing", "missing_source"), ("404", "missing_source"),
                                           ("symlink", "invalid_source"), ("hash", "invalid_source")])
@pytest.mark.parametrize("ref,side", [(HEAD, "RIGHT"), (MERGE_BASE, "LEFT")])
def test_split_dependency_keeps_missing_symlink_and_blob_integrity_gates(failure, reason, ref, side):
    fake = fixture()
    text = fake.sources[(ref, DEP)]
    endpoint = f"repos/Org/repo/contents/{DEP}?ref={ref}"
    if failure == "missing":
        del fake.sources[(ref, DEP)]
    elif failure == "404":
        fake.overrides[endpoint] = response({}, 404)
    elif failure == "hash":
        fake.overrides[endpoint] = content_response(DEP, "xxxx", sha=blob_sha(text))
    else:
        entries = [{"path": p, "type": "blob", "mode": "120000" if p == DEP else "100644",
                    "sha": blob_sha(t), "size": len(t.encode())}
                   for (r, p), t in fake.sources.items() if r == ref]
        fake.overrides[f"repos/Org/repo/git/trees/{ref}?recursive=1"] = response(
            {"sha": "e" * 40, "truncated": False, "tree": entries})
    result = collect(fake)
    assert result["incomplete_reasons"] == [reason]
    assert result["source_omissions"] == [
        {"path": DEP, "ref": ref, "side": side, "reason": reason, "required": True}]
    assert result["source_budget"] == telemetry("split", 8, 8, 8, 4)
    assert len([s for s in result["sources"] if s["path"] == DEP]) == 1
    if failure in {"missing", "symlink"}:
        assert endpoint not in [a[-1] for a, _ in fake.calls]


@pytest.mark.parametrize("dependency_limit", [None, 8])
def test_explicit_dependency_ancestors_load_only_relevant_trusted_base_guidance(dependency_limit):
    fake = fixture()
    docs = {"lib/AGENTS.md": "Trusted parent", "lib/nested/README.md": "Trusted subtree",
            "lib/nested/architecture.rst": "Trusted design"}
    fake.docs.update(docs)
    fake.docs["lib/sibling/AGENTS.md"] = "Unrelated"
    fake.docs["lib/nested/arbitrary.md"] = "Not guidance"
    for path in docs:
        add_source(fake, path, "Untrusted head instructions")
    result = collect(fake, dependency_limit=dependency_limit, source_limit=16)
    assert result["incomplete_reasons"] == []
    assert result["docs"] == {"README.md": fake.docs["README.md"], **docs}
    assert all(BASE in a[-1] for a, _ in fake.calls if any(f"/contents/{p}?" in a[-1] for p in docs))
    assert {s["path"] for s in result["sources"]} == {MAIN, DEP}


def test_explicit_ignored_changed_path_still_loads_its_ancestor_guidance():
    fake = fixture()
    fake.docs[".github/hermes-pr-reviewer.json"] = json.dumps({"ignorePatterns": [MAIN]})
    fake.docs["src/AGENTS.md"] = "Required when caller restores source"
    result = collect(fake, paths=(MAIN, DEP))
    assert result["incomplete_reasons"] == []
    assert result["docs"]["src/AGENTS.md"] == fake.docs["src/AGENTS.md"]


@pytest.mark.parametrize("gate", ["bytes", "count"])
def test_explicit_dependency_guidance_retains_document_budget_and_count(gate):
    fake = fixture()
    fake.docs = {"lib/nested/AGENTS.md": "é" * 3}
    if gate == "count":
        fake.docs.update({f"lib/nested/architecture-{i}.md": "x" for i in range(32)})
    result = collect(fake, max_doc_chars=5 if gate == "bytes" else 60_000)
    reason = "docs_budget" if gate == "bytes" else "docs_count_limit"
    assert result["incomplete_reasons"] == [reason]
    omissions = [o for o in result["doc_omissions"] if o["required"]]
    assert len(omissions) == 1
    assert omissions[0]["ref"] == BASE and omissions[0]["reason"] == reason
    assert len(result["docs"]) == (0 if gate == "bytes" else 32)
    assert result["source_budget"] == telemetry("split", 8, 8, 8, 8)


def test_split_does_not_expand_request_budget():
    fake = fixture()
    with pytest.raises(GitHubError, match="request budget"):
        collect(fake, max_requests=4)
    assert len(fake.calls) == 4


@pytest.mark.parametrize("value", [0, -1, 400_001, True, False, 1.5, "8", [], {}, float("inf"), float("nan")])
def test_dependency_budget_validation_rejects_before_io(value):
    fake = fixture()
    with pytest.raises(ValueError, match="dependency"):
        GitHub(transport=fake, max_dependency_chars=value)
    assert fake.calls == []


@pytest.mark.parametrize("value", [1, 400_000])
def test_dependency_budget_inclusive_bounds(value):
    fake = fixture()
    add_source(fake, DEP, "", "")
    result = collect(fake, dependency_limit=value)
    assert result["incomplete_reasons"] == []
    assert result["source_budget"] == telemetry("split", 8, 8, value, 0)


@pytest.mark.parametrize("stage,paths", [("review", ()), ("triage", ()), ("triage", (DEP,))])
def test_split_requires_explicit_paths_and_review_before_collect_io(stage, paths):
    fake = fixture()
    with pytest.raises(ValueError):
        GitHub(transport=fake, max_dependency_chars=8).collect(REF, stage=stage, source_paths=paths)
    assert fake.calls == []


@pytest.mark.parametrize("paths", [None, DEP, ["../bad"], ["a*"], [DEP] * 25])
def test_split_preserves_explicit_path_validation_before_io(paths):
    fake = fixture()
    with pytest.raises(ValueError):
        collect(fake, paths=paths)
    assert fake.calls == []


def test_triage_shared_telemetry_has_no_source_usage():
    fake = fixture()
    result = GitHub(transport=fake).collect(REF, stage="triage")
    assert result["source_budget"] == telemetry("shared", 400_000, 0)
    assert result["sources"] == []
