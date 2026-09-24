# DisMech evaluations

**[Open the evaluation dashboard](https://monarch-initiative.github.io/dismech-evals/)** ·
[Overall agent queue](https://monarch-initiative.github.io/dismech-evals/downloads/top.jsonl) ·
[Queues by category](https://monarch-initiative.github.io/dismech-evals/agents.html)

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
just products --report build/combined  # top N overall and by category, without inference
just site                        # build the static dashboard from published products
just serve                       # preview at http://127.0.0.1:8000
just check                       # orchestration, ranking and publication tests
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

## Dashboard and agent handoff

The site is a custom static HTML/CSS/JavaScript dashboard. It has an overview,
searchable entry rankings, category and aspect-group breakdowns, exact claim/snippet
review pages, and agent downloads. It works on GitHub Pages and from a local
`build/site/index.html`; it loads no external assets. The README stays hand-maintained.

The weekly report job generates the recuration product, and the publication job
commits it under [`reports/latest/recuration/`](reports/latest/recuration/):

| File | Contents |
| --- | --- |
| `top.jsonl` | Up to N highest-priority disease entries overall |
| `categories/{section}.jsonl` | Up to N entries, ranked using only that claim category |
| `rankings.csv` | All entry/category rankings and denominators |
| `dashboard.json` | Aggregate counts and rankings used by the site |
| `manifest.json` | Scope, source/model revisions, run identity, queue sizes and checksums |
| `schema.json` | JSON Schema for each work-item record |

Each JSONL line is a self-contained disease review assignment: source file and YAML
pointers, exact structured claim and snippet, aspect labels and probabilities,
assessment dates, source revision links, and review instructions. Jev supplies no
written rationale. A packet carries a bounded set of evidence examples plus
`findings_total` and `findings_truncated`; it links to the complete assessment history.
The same disease may appear in several category queues.

Defaults in [configuration](config/evidence_claim_match.yaml) are **25 entries per
queue** and **10 evidence pairs per entry**. Use `just products --top-n 50
--max-findings 20` to override them. Categories are the original top-level dismech
sections, such as `phenotypes`, `pathophysiology`, `inheritance` and `treatments`.

Ranking follows the dismech report: distinct assertions with MISMATCH, maximum
mismatch probability when a mismatch exists, distinct assertions with PARTIAL,
missing evidence/snippets, then source filename. This orders review burden, not
accuracy or disease-entry truth; large entries can rank higher. Repeated fields or
snippets do not inflate distinct-assertion counts. MISMATCH and PARTIAL counts can
overlap. Only entries with a model MISMATCH or PARTIAL enter the agent queues;
missing evidence, invalid input and failed calls remain separate statuses.

The site displays limited, incomplete and inventory-only runs explicitly. Before
a corpus run exists, `just products --history` can build a saved-history snapshot
from inputs marked active at their last recorded observation. It selects the most
recent assessment per input for the configured model, retaining configuration hashes.
That mode has **unknown full-corpus coverage**, and it does not claim to reflect
current source files. Weekly products instead use the exact run's saved results.
Agents must compare the current YAML with the snapshot before editing.

Products remain in Git history; each weekly Actions artifact also contains that
run's complete report and product. Build a site from any downloaded product with
`just site --products /path/to/recuration`. No inference is needed.

[Pages deployment](.github/workflows/pages.yaml) runs on site/product changes and
when the weekly workflow completes. The latter trigger also handles data commits
made with `GITHUB_TOKEN`, which do not start push-triggered workflows. It builds
from committed `main`, with no classifier installation or API secret, and gives
only the deployment job `pages: write` and `id-token: write`.

Run `just setup-browser` once, then `just test-browser` to check navigation, direct
file URLs, mobile layout, packet drill-downs and downloads in Chromium. The checked-in
[work-item schema](schemas/recuration.schema.json) is tested against generated queues.

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
