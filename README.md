# DisMech evaluations

Public, corpus-wide model assessments of [DisMech](https://github.com/monarch-initiative/dismech)
assertions and their evidence snippets. These are recuration signals, not curator
labels or judgments of source quality. Historical claims and scores remain available
when the disease entries change.

- **dismech** owns the disease data, LinkML schema, claim extraction, classifier and local audit commands.
- **dismech-evals** owns the assessment history, weekly execution and corpus reports.
- **dismech-bench** remains the separate repository for curated benchmark cases, reviewer judgments and benchmark results.

## Data and reports

[Assessment history](analysis/classification/jev/) contains one YAML file per
disease and task, for example
[Asthma](analysis/classification/jev/Asthma/evidence_claim_match.yaml) and
[Cystic fibrosis](analysis/classification/jev/Cystic_Fibrosis/evidence_claim_match.yaml).
The current task is `evidence_claim_match`, with MATCH / MISMATCH / PARTIAL results
for the complete claim and individual aspects.

The [weekly workflow](.github/workflows/weekly.yaml) publishes small recuration
reports under `reports/latest/` and execution records under `runs/` after a live run.
Detailed CSVs, prediction streams and checkpoint files are downloadable from the
[Actions runs](https://github.com/monarch-initiative/dismech-evals/actions/workflows/weekly.yaml).
Partial runs are explicitly incomplete; their successful assessments are preserved.

## Run locally

Install `uv`, `just` and the GitHub CLI. From this checkout:

```sh
uv sync --frozen
just setup                       # prepares sparse source and classifier checkouts
just inventory --limit 20        # no model calls; no API key needed
just audit --section phenotypes --limit 20
just cache                       # update activity without inference
just report --shards build/shards
just check                       # orchestration tests and lint
```

Live inference uses `TYPESAFE_API_KEY` from the environment. No API key is required
for setup, inventory, history merges or report generation. Additional audit options
are passed to dismech's Click CLI. `--input` paths are relative to the prepared
dismech source checkout (for example `kb/disorders/Asthma.yaml`). Reports default to
`build/reports/`; paid responses persist in `analysis/classification/jev/`.

[Configuration](config/evidence_claim_match.yaml) pins a full classifier commit,
a versioned model and execution limits. `source_ref` is resolved to a full source
commit once per run. The source and classifier use separate checkouts, so the
corpus can advance while the method stays fixed. The run's manifests record both
revisions and the evaluation repository revision. Run `just setup` to resolve a
fresh source snapshot; it refuses to overwrite dirty checkouts. Changing the
classifier pin is an explicit methodology update. The initial pin contains the
implementation proposed in [dismech PR #12552](https://github.com/monarch-initiative/dismech/pull/12552).

You can also use this history from a separate dismech checkout:

```sh
# Run this in dismech, using a sibling checkout of this repository.
just jev-audit --cache ../dismech-evals/analysis/classification/jev
```

## History format

The files are readable YAML, with no compression or binary database. Format
version 2 stores one canonical input snapshot per input hash:

```yaml
format_version: 2
benchmark: evidence_claim_match
source_file: kb/disorders/Example.yaml
inputs:
  INPUT_SHA256:
    claim: {}                  # complete model-visible state, stored once
    first_seen_at: ...         # earliest retained observation, not authoring date
    first_seen_revision: ...
    last_seen_at: ...
    active: true
    active_as_of: ...
    occurrences: []            # current YAML paths and reference IDs
assessments:
  ASSESSMENT_SHA256:
    input_sha256: INPUT_SHA256
    configuration_sha256: ...
    model: jev-1.13.0
    assessed_at: ...
    result: {}                 # aspect labels, probabilities, confidence, usage
```

Each input also retains original pointers and citation metadata outside the
model-visible state. Assessment configurations share that snapshot. Activity and
first/last-seen provenance belong to the input; assessment dates and refresh
history belong to the assessment. Old wording becoming current again reuses its
saved result. Punctuation changes create new input versions; YAML formatting alone
does not. The hash also covers prompt and question semantics when looking up an
assessment. A model change does not itself retire a claim.

Activity is reconciled from complete disease files even when inference is filtered
or time-limited. Invalid extraction never retires unseen claims. The file's
inventory gives the source hash, extraction fingerprint, revision, observation
time and completeness. `active` means present at that observation, not necessarily
at today's moving source branch. Future HTML should match the current input and
assessment configuration before showing a score; downloading history is not a
requirement for ordinary dismech builds.

The format and migration code live in
[dismech's cache module](https://github.com/monarch-initiative/dismech/blob/d07ebcc11fd88a478d97042253931f6985640980/src/dismech/classifier/cache.py).
The [audit guide](https://github.com/monarch-initiative/dismech/blob/d07ebcc11fd88a478d97042253931f6985640980/docs/jev-evidence-audit.md)
describes the inference scope and report semantics. Do not hand-edit predictions.

## Automation and permissions

The workflow runs Monday at **09:17 UTC**. It uses 16 stable filename partitions,
with at most four simultaneous jobs and four inference workers per job. Each shard
stops scheduling requests after five hours. Manual dispatch defaults to an
inventory-only run; choose `dry_run: false` to run inference.

Set this repository's `TYPESAFE_API_KEY` Actions secret before a live run. Only the
publication job receives `contents: write`; it uses this repository's `GITHUB_TOKEN`
to commit generated YAML, run records and small reports directly to this repository's
`main`. **These data updates are automatic and do not require human review.** No
App token or write access to dismech or dismech-bench is used. Other staged paths
and deletion of historical records are rejected. Concurrent movement of `main`
causes publication to fail rather than overwrite another commit.

Successful checkpoints are collected even when some inference or reporting jobs
fail. Shard artifacts are retained for 90 days. If publication fails, recover each
artifact's `cache/` folder **before another paid run**:

```sh
just setup
just cache --merge-cache /path/to/jev-shard-0/cache \
           --merge-cache /path/to/jev-shard-1/cache
# Inspect, then commit the recovered assessment files in this repository.
```

Only changed disease files are uploaded as checkpoints; the committed history is
reused by later runs and does not expire with Actions caches. Run one local writer
per checkout. Source code and test fixtures remain small; the data directory is
expected to grow as claims and evaluation methods evolve.
