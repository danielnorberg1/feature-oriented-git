import typer

from git_tool.feature_data.models_and_context.repo_context import repo_context
from git_tool.materialization import materialize_projection, sync_projection_back


app = typer.Typer(
    help="Create a projected product variant from a feature selection",
    no_args_is_help=True,
)


@app.command(
    name="project",
    help="Materialize a product variant by selecting features to include.",
)
def feature_project(
    include: list[str] = typer.Option(
        ...,
        help="Features to include. Repeat for each feature: --include F1 --include F2",
    ),
    branch: str = typer.Option(
        None,
        help="Name for the projected branch. Default: project/<features>",
    ),
):
    """Create a new branch containing only code for the selected features.

    Annotated blocks for non-selected features are stripped.
    Files mapped exclusively to non-selected features are removed.
    Annotation markers are removed, producing clean product code.
    """
    selected = set(include)

    if branch is None:
        branch = "project/" + "-".join(sorted(selected))

    with repo_context() as repo:
        try:
            result = materialize_projection(repo, selected, branch)
            typer.echo(f"Projected variant created on branch '{result}'")
            typer.echo(f"Selected features: {', '.join(sorted(selected))}")
        except RuntimeError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)


@app.command(
    name="sync",
    help="Synchronize changes from a projected branch back into the target branch.",
)
def feature_project_sync(
    projection: str = typer.Argument(
        ..., help="The projection branch to sync back."
    ),
    target: str = typer.Option(
        None,
        help="Target branch to sync into. Defaults to current branch.",
    ),
):
    """Sync a modified projected variant back into the original branch."""
    with repo_context() as repo:
        try:
            result = sync_projection_back(repo, projection, target)
            typer.echo(f"Projection '{projection}' synced back into '{result}'")
        except (RuntimeError, ValueError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
