# Resolve and install the configured classifier and source revisions.
[positional-arguments]
setup *args:
    uv run dismech-evals setup "$@"

# Pass dismech options such as --section, --limit, --workers or --dry-run.
[positional-arguments]
audit *args:
    uv run dismech-evals audit "$@"

[positional-arguments]
inventory *args:
    uv run dismech-evals audit --dry-run "$@"

[positional-arguments]
cache *args:
    uv run dismech-evals cache "$@"

[positional-arguments]
report *args:
    uv run dismech-evals report "$@"

check:
    uv run ruff check .
    uv run pytest -q
