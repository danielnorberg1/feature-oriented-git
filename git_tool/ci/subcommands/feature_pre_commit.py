import typer

from git_tool.feature_data.git_status_per_feature import get_files_by_git_change
from git_tool.feature_data.models_and_context.feature_state import (
    read_staged_featureset,
)
from git_tool.feature_data.models_and_context.repo_context import repo_context
from git_tool.materialization import check_projection_staged


app = typer.Typer()


@app.command()
def feature_pre_commit():
    """
    Checks if all staged changes are properly associated with features.
    On a projection branch, also enforces that platform code is not modified.
    Returns an error if any issues are found.
    """
    changes = get_files_by_git_change()

    if len(changes["staged_files"]) == 0:
        typer.echo("Error: No staged changes found.")
        raise typer.Exit(code=1)

    with repo_context() as repo:
        violations = check_projection_staged(repo)

    if violations:
        typer.echo("Error: Staged changes touch platform code on a projection branch.")
        typer.echo("Only code inside feature annotation blocks may be modified.")
        typer.echo("")
        for v in violations:
            typer.echo(f"  {v}")
        raise typer.Exit(code=1)

    staged_features = read_staged_featureset()

    if not staged_features:
        typer.echo("Warning: No features associated with the staged changes. Remember to use git feature-commit to add these information")
        raise typer.Exit(code=0)

    typer.echo("Pre-commit checks passed.")
    raise typer.Exit(code=0)
