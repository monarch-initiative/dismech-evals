"""Publication boundaries, provenance checks and safe reuse require no inference."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from dismech_evals import cli


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "OUTCOMES", tmp_path / cli.CACHE_PATH)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def command(*args, cwd):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def git_repo(repo):
    remote = repo / "remote.git"
    command("git", "init", "--bare", str(remote), cwd=repo)
    command("git", "init", "-b", "main", cwd=repo)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.org")):
        command("git", "config", key, value, cwd=repo)
    command("git", "remote", "add", "origin", str(remote), cwd=repo)
    (repo / "code.txt").write_text("code\n")
    (repo / ".gitignore").write_text("remote.git/\n")
    command("git", "add", "--", "code.txt", ".gitignore", cwd=repo)
    command("git", "commit", "-m", "base", cwd=repo)
    command("git", "push", "origin", "main", cwd=repo)
    return remote


def history(repo, text="inputs: {}\n"):
    target = repo / cli.CACHE_PATH / "Example" / "evidence_claim_match.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def test_publication_writes_only_generated_data_and_noop_is_stable(repo):
    remote = git_repo(repo)
    before = command("git", "rev-parse", "HEAD", cwd=repo)
    target = history(repo)
    (repo / "code.txt").write_text("uncommitted code edit\n")
    result = CliRunner().invoke(cli.main, ["publish"])
    assert result.exit_code == 0, result.output
    head = command("git", "rev-parse", "HEAD", cwd=repo)
    assert head != before
    assert (
        command("git", "--git-dir", str(remote), "rev-parse", "main", cwd=repo) == head
    )
    assert (
        command("git", "diff", "--name-only", before, head, cwd=repo)
        == target.relative_to(repo).as_posix()
    )
    assert command("git", "show", "HEAD:code.txt", cwd=repo) == "code"
    assert CliRunner().invoke(cli.main, ["publish"]).exit_code == 0
    assert command("git", "rev-parse", "HEAD", cwd=repo) == head


def test_publication_rejects_unrelated_staged_code(repo):
    remote = git_repo(repo)
    before = command("git", "rev-parse", "HEAD", cwd=repo)
    history(repo)
    (repo / "code.txt").write_text("staged code\n")
    command("git", "add", "--", "code.txt", cwd=repo)
    result = CliRunner().invoke(cli.main, ["publish"])
    assert result.exit_code != 0
    assert "unrelated" in result.output
    assert (
        command("git", "--git-dir", str(remote), "rev-parse", "main", cwd=repo)
        == before
    )


def test_publication_rejects_a_feature_commit_or_remote_movement(repo):
    git_repo(repo)
    (repo / "code.txt").write_text("feature\n")
    command("git", "add", "--", "code.txt", cwd=repo)
    command("git", "commit", "-m", "feature", cwd=repo)
    history(repo)
    result = CliRunner().invoke(cli.main, ["publish"])
    assert result.exit_code != 0
    assert "Main moved" in result.output


def test_history_deletions_are_rejected(repo):
    git_repo(repo)
    target = history(repo)
    assert CliRunner().invoke(cli.main, ["publish"]).exit_code == 0
    target.unlink()
    result = CliRunner().invoke(cli.main, ["publish"])
    assert result.exit_code != 0
    assert "Refusing to delete" in result.output


def test_checkpoint_export_contains_only_changed_yaml(repo):
    git_repo(repo)
    history(repo)
    result = CliRunner().invoke(cli.main, ["checkpoints"])
    assert result.exit_code == 0, result.output
    assert (
        repo / "build/reports/cache/Example/evidence_claim_match.yaml"
    ).read_text() == "inputs: {}\n"


def test_module_uses_prepared_source_as_cwd_and_separate_runner(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "pins", lambda: {})
    monkeypatch.setattr(cli, "run", lambda args, **kw: calls.append((args, kw)))
    cli.module("audit", ["--dry-run"])
    args, options = calls[0]
    assert args[:4] == ["uv", "run", "--frozen", "--no-dev"]
    assert str(cli.RUNNER) in args
    assert options["cwd"] == cli.SOURCE
    assert "dismech.classifier.audit" in args


def test_collect_rejects_wrong_source_before_writing_history(repo, monkeypatch):
    shards = repo / "shards" / "zero"
    shards.mkdir(parents=True)
    (shards / "manifest.json").write_text(
        json.dumps({"source_revision": "wrong", "evaluation": {}})
    )
    monkeypatch.setattr(cli, "pins", lambda: {"source_revision": "expected"})
    calls = []
    monkeypatch.setattr(cli, "module", lambda *a, **k: calls.append(a))
    result = CliRunner().invoke(
        cli.main, ["collect", "--shards", str(shards.parent), "--run-id", "1"]
    )
    assert result.exit_code != 0
    assert not calls
    assert not (repo / "runs").exists()


def test_collect_retains_partial_results_and_does_not_show_stale_report(
    repo, monkeypatch
):
    git_repo(repo)
    shard = repo / "shards" / "zero"
    shard.mkdir(parents=True)
    expected = {
        "source_revision": "source",
        "classifier_revision": "classifier",
        "source_repository": cli.REPOSITORY,
    }
    manifest = {
        "source_revision": "source",
        "evaluation": {**expected, "evaluation_revision": "eval-original"},
        "complete": False,
    }
    (shard / "manifest.json").write_text(json.dumps(manifest))
    cache = shard / "cache"
    cache.mkdir()
    latest = repo / "reports/latest"
    latest.mkdir(parents=True)
    (latest / "entries.csv").write_text("old report\n")
    monkeypatch.setattr(cli, "pins", lambda: expected)
    calls = []
    monkeypatch.setattr(cli, "module", lambda *a, **k: calls.append(a))
    result = CliRunner().invoke(
        cli.main, ["collect", "--shards", str(shard.parent), "--run-id", "123-1"]
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0] == "cache" and cache.resolve() in calls[0][1]
    record = json.loads((repo / "runs/123-1.json").read_text())
    assert not record["complete"]
    assert record["evaluation_revision"] == "eval-original"
    assert not (latest / "entries.csv").exists()
    assert "Incomplete" in (latest / "summary.md").read_text()


@pytest.mark.parametrize("revision", ["main", "abc", "../other", "a" * 41])
def test_classifier_requires_an_immutable_revision(revision):
    with pytest.raises(Exception, match="40-character"):
        cli.require_sha(revision)


def test_expected_generated_paths():
    assert cli.generated_path(
        Path("analysis/classification/jev/A/evidence_claim_match.yaml")
    )
    assert cli.generated_path(Path("runs/123-1.json"))
    assert cli.generated_path(Path("reports/latest/entries.csv"))
    assert not cli.generated_path(Path("src/code.py"))
    assert not cli.generated_path(Path("analysis/classification/jev/code.py"))
