"""Thin orchestration; all claim extraction and inference stays in dismech."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import click
import yaml

ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = Path("analysis/classification/jev")
OUTCOMES = ROOT / CACHE_PATH
SOURCE = ROOT / "build/dismech"
RUNNER = ROOT / "build/dismech-runner"
PIN_FILE = ROOT / "build/pins.json"
REPOSITORY = "monarch-initiative/dismech"


def run(args, cwd=None, *, capture=False, check=True, env=None):
    result = subprocess.run(
        args, cwd=cwd, text=True, capture_output=capture, check=False, env=env
    )
    if check and result.returncode:
        raise click.ClickException(f"Command failed ({result.returncode}): {args[0]}")
    return result.stdout.strip() if capture and result.returncode == 0 else result


def git(*args, cwd=None, **kwargs):
    return run(["git", *args], cwd, **kwargs)


def config():
    value = yaml.safe_load((ROOT / "config/evidence_claim_match.yaml").read_text())
    if value["source_repository"] != REPOSITORY:
        raise click.ClickException("Only the dismech source repository is supported")
    require_sha(value["classifier_revision"])
    if not 1 <= value["shards"] <= 64:
        raise click.ClickException("shards must be between 1 and 64")
    return value


def require_sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise click.ClickException("Expected an immutable 40-character Git commit SHA")
    return value


def resolve_source(cfg):
    return require_sha(
        run(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/commits/{cfg['source_ref']}",
                "--jq",
                ".sha",
            ],
            capture=True,
        )
    )


def checkout(path, revision, sparse):
    """Read-only sparse checkout at an exact commit; never reset a dirty checkout."""
    require_sha(revision)
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        git("init", str(path))
        git("remote", "add", "origin", f"https://github.com/{REPOSITORY}.git", cwd=path)
    if git("status", "--porcelain", cwd=path, capture=True):
        raise click.ClickException(f"Source checkout has local changes: {path}")
    if (
        git("remote", "get-url", "origin", cwd=path, capture=True)
        != f"https://github.com/{REPOSITORY}.git"
    ):
        raise click.ClickException("Unexpected source checkout remote")
    git("config", "remote.origin.promisor", "true", cwd=path)
    git("config", "remote.origin.partialclonefilter", "blob:none", cwd=path)
    git("sparse-checkout", "init", "--cone", cwd=path)
    git("sparse-checkout", "set", sparse, cwd=path)
    git("fetch", "--filter=blob:none", "--depth", "1", "origin", revision, cwd=path)
    git("checkout", "--detach", revision, cwd=path)


def pins():
    if not PIN_FILE.exists():
        raise click.ClickException("Run just setup before evaluating the corpus")
    value = json.loads(PIN_FILE.read_text())
    for path, field in ((SOURCE, "source_revision"), (RUNNER, "classifier_revision")):
        if git("rev-parse", "HEAD", cwd=path, capture=True) != value[field]:
            raise click.ClickException(
                f"{path} no longer matches the prepared revision"
            )
        if git("status", "--porcelain", cwd=path, capture=True):
            raise click.ClickException(f"Source checkout has local changes: {path}")
    return value


def module(name, args, *, check=True):
    pins()
    return run(
        [
            "uv",
            "run",
            "--frozen",
            "--no-dev",
            "--project",
            str(RUNNER),
            "python",
            "-m",
            "dismech.classifier." + name,
            *map(str, args),
        ],
        cwd=SOURCE,
        check=check,
    )


@click.group()
def main():
    """Run DisMech's classifier and preserve its corpus assessment history."""


@main.command()
@click.option("--github-output", type=click.Path(path_type=Path))
def resolve(github_output):
    """Resolve the source revision once for all workflow jobs."""
    cfg = config()
    values = {
        "source_revision": resolve_source(cfg),
        "classifier_revision": cfg["classifier_revision"],
        "shards": json.dumps(list(range(cfg["shards"]))),
    }
    if github_output:
        with github_output.open("a") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    click.echo(json.dumps(values))


@main.command()
@click.option("--source-revision")
@click.option("--classifier-revision")
def setup(source_revision, classifier_revision):
    """Prepare sparse source/code checkouts and install the pinned classifier."""
    cfg = config()
    source_revision = (
        require_sha(source_revision) if source_revision else resolve_source(cfg)
    )
    classifier_revision = require_sha(classifier_revision or cfg["classifier_revision"])
    checkout(SOURCE, source_revision, "kb/disorders")
    checkout(RUNNER, classifier_revision, "src")
    run(["uv", "sync", "--frozen", "--no-dev", "--project", str(RUNNER)])
    PIN_FILE.write_text(
        json.dumps(
            {
                "source_repository": REPOSITORY,
                "source_revision": source_revision,
                "classifier_revision": classifier_revision,
            },
            indent=2,
        )
        + "\n"
    )


@main.command(context_settings={"ignore_unknown_options": True})
@click.option("--output", type=click.Path(path_type=Path), default="build/reports")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def audit(output, args):
    """Run inference (or --dry-run); additional dismech audit options pass through."""
    cfg = config()
    result = module(
        "audit",
        [
            "--cache",
            OUTCOMES,
            "--output",
            output.resolve(),
            "--model",
            cfg["model"],
            "--workers",
            cfg["workers"],
            "--max-seconds",
            cfg["max_seconds"],
            *args,
        ],
        check=False,
    )
    manifest = output / "manifest.json"
    if manifest.exists():
        value = json.loads(manifest.read_text())
        value["evaluation"] = {
            **pins(),
            "evaluation_revision": git("rev-parse", "HEAD", capture=True),
        }
        manifest.write_text(json.dumps(value, indent=2) + "\n")
    if result.returncode:
        raise click.ClickException(
            "Audit incomplete; successful checkpoints were retained"
        )


@main.command()
@click.option(
    "--merge-cache",
    "sources",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def cache(sources):
    """Union checkpoint histories and reconcile against the prepared source commit."""
    args = ["--cache", OUTCOMES]
    for source in sources:
        args += ["--merge-cache", source.resolve()]
    module("cache", args)


@main.command()
@click.option("--output", type=click.Path(path_type=Path), default="build/combined")
@click.option(
    "--shards",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
def report(output, shards):
    """Combine compatible shard results; incomplete runs still write reports."""
    module(
        "audit_report",
        [
            output.resolve(),
            "--merge",
            shards.resolve(),
            "--expected-shards",
            config()["shards"],
        ],
    )


@main.command()
@click.option(
    "--output", type=click.Path(path_type=Path), default="build/reports/cache"
)
def checkpoints(output):
    """Copy only modified/new YAML histories into the shard artifact."""
    files = git(
        "ls-files",
        "--modified",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        str(CACHE_PATH),
        capture=True,
    )
    for name in set(files.split("\0")) - {""}:
        path = Path(name)
        relative = path.relative_to(CACHE_PATH)
        if len(relative.parts) != 2 or relative.name != "evidence_claim_match.yaml":
            raise click.ClickException(f"Unexpected checkpoint file: {path}")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)


@main.command()
@click.option(
    "--shards",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--report", "report_path", type=click.Path(path_type=Path), default="build/combined"
)
@click.option("--run-id", required=True)
def collect(shards, report_path, run_id):
    """Preserve all successful checkpoints, independently of report completeness."""
    if not re.fullmatch(r"[0-9A-Za-z_-]+", run_id):
        raise click.ClickException("Invalid run ID")
    args = ["--cache", OUTCOMES]
    for path in sorted(shards.glob("*/cache")):
        args += ["--merge-cache", path.resolve()]
    manifests = [
        json.loads(path.read_text()) for path in sorted(shards.glob("*/manifest.json"))
    ]
    expected = pins()
    for manifest in manifests:
        if manifest.get("source_revision") != expected["source_revision"] or any(
            manifest.get("evaluation", {}).get(k) != v for k, v in expected.items()
        ):
            raise click.ClickException(
                "Shard provenance does not match prepared checkouts"
            )
    evaluations = {m["evaluation"]["evaluation_revision"] for m in manifests}
    if len(evaluations) > 1:
        raise click.ClickException("Shards used different evaluation revisions")
    for path in shards.glob("*/cache"):
        if any(path.glob("*/*.yaml")) and not (path.parent / "manifest.json").exists():
            raise click.ClickException("Checkpoint artifact lacks its shard manifest")
    module("cache", args)
    run_record = {
        "run_id": run_id,
        **expected,
        "evaluation_revision": next(
            iter(evaluations),
            os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD", capture=True),
        ),
        "complete": False,
        "shards_received": len(manifests),
    }
    latest = ROOT / "reports/latest"
    latest.mkdir(parents=True, exist_ok=True)
    if (report_path / "manifest.json").exists():
        report_manifest = json.loads((report_path / "manifest.json").read_text())
        run_record["complete"] = report_manifest["complete"]
        run_record["pairs"] = report_manifest["pairs"]
        for name in ("entries.csv", "summary.md", "manifest.json"):
            if (report_path / name).exists():
                shutil.copyfile(report_path / name, latest / name)
    else:
        (latest / "entries.csv").unlink(missing_ok=True)
        (latest / "manifest.json").write_text(json.dumps(run_record, indent=2) + "\n")
        (latest / "summary.md").write_text(
            f"# Incomplete corpus evaluation\n\nRun `{run_id}` has no combined report. "
            "Successful checkpoints are retained; this is not a full-corpus result.\n"
        )
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / f"{run_id}.json").write_text(
        json.dumps(run_record, indent=2) + "\n"
    )


def generated_path(path):
    return (
        (
            path.is_relative_to(CACHE_PATH)
            and len(path.parts) == 5
            and path.name == "evidence_claim_match.yaml"
        )
        or (len(path.parts) == 2 and path.parts[0] == "runs" and path.suffix == ".json")
        or (
            path.parent == Path("reports/latest")
            and path.name in {"summary.md", "manifest.json", "entries.csv"}
        )
    )


@main.command()
def publish():
    """Commit only generated data to this repository; never force-push."""
    # Workflow concurrency serializes writers. A human moving main during this
    # final step makes the normal fast-forward push fail; artifacts retain data.
    head = git("rev-parse", "HEAD", capture=True)
    remote = git("ls-remote", "origin", "refs/heads/main", capture=True).split()
    if not remote or remote[0] != head:
        raise click.ClickException(
            "Main moved; merge saved checkpoints into a fresh checkout before publication"
        )
    paths = [str(CACHE_PATH), "runs", "reports/latest"]
    paths = [p for p in paths if (ROOT / p).exists()]
    git("add", "--", *paths)
    changed = git("diff", "--cached", "--name-only", capture=True)
    if not changed:
        click.echo("No generated changes to publish")
        return
    for name in changed.splitlines():
        path = Path(name)
        if not generated_path(path):
            raise click.ClickException(
                f"Refusing to publish unrelated staged path: {name}"
            )
    deleted = git("diff", "--cached", "--name-only", "--diff-filter=D", capture=True)
    if any(
        Path(name).is_relative_to(CACHE_PATH) or Path(name).is_relative_to("runs")
        for name in deleted.splitlines()
    ):
        raise click.ClickException(
            "Refusing to delete historical assessments or run records"
        )
    git("commit", "-m", "Update corpus assessment history")
    git("push", "origin", "HEAD:refs/heads/main")


if __name__ == "__main__":
    main()
