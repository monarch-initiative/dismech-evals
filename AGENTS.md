# Working in dismech-evals

This public repository contains corpus evaluation outputs and thin orchestration.
Claim extraction, schemas, prompts, classification and cache serialization belong
in `monarch-initiative/dismech`. Curated benchmarks belong in `dismech-bench`.
Do not copy either implementation or benchmark curation into this repository.

Use Click for CLI commands and expose common tasks through `just`.
Run `just check` and actionlint after changing the workflow.
Keep classifier commits pinned and record exact source/evaluation revisions.
Do not invoke paid inference just to test orchestration; use `--dry-run` or mocks.
Never invent model predictions, edit their values, or remove historical records.
Use dismech's cache command to migrate/merge histories. Keep per-input activity
separate from assessment configuration and original assessment dates.

Generated YAML under `analysis/classification/jev/`, run records under `runs/`, and
small summaries under `reports/latest/` are versioned here. Raw prediction streams,
large CSVs and source checkouts belong under ignored `build/` or in Actions artifacts.
Publication uses this repository's token only and may update these generated paths
automatically. Do not grant it write access to the source or benchmark repositories.

Stage explicit paths. Keep code/configuration changes separate from automated data
updates. Do not force-push or rewrite published history.
