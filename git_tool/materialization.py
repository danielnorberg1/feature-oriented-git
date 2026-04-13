import re
from pathlib import Path
from typing import Set

from git import Repo

from git_tool.finding_features import _features_from_file_mapping

_BEGIN_RE = re.compile(r".*&begin\[(?P<name>.*?)\].*")
_END_RE = re.compile(r".*&end\[(?P<name>.*?)\].*")


def materialize_file(content: str, selected_features: Set[str]) -> str:
    """Project a single file given a feature selection.

    Uses a stack to handle nested annotations with FaXe union semantics:
    a line inside &begin[A] and &begin[B] belongs to both A and B.
    A line is kept if ALL enclosing features are in selected_features.
    Annotation markers are always stripped.
    Lines outside any annotation block are always kept (platform code).
    """
    lines = content.splitlines(keepends=True)
    result = []
    feature_stack: list[str] = []

    for line in lines:
        begin_match = _BEGIN_RE.match(line)
        if begin_match:
            feature_stack.append(begin_match.group("name"))
            continue  # strip marker

        end_match = _END_RE.match(line)
        if end_match and feature_stack and feature_stack[-1] == end_match.group("name"):
            feature_stack.pop()
            continue  # strip marker

        if feature_stack:
            # Line belongs to all features on the stack (union semantics).
            # Keep only if every enclosing feature is selected.
            if all(f in selected_features for f in feature_stack):
                result.append(line)
        else:
            result.append(line)  # platform / shared code

    return "".join(result)


def should_include_file(file_path: str, selected_features: Set[str]) -> bool:
    """Decide whether a file belongs in the projection.

    - No file-config mapping → platform code → always included
    - Mapped to at least one selected feature → included
    - Mapped only to non-selected features → excluded
    """
    config_features = _features_from_file_mapping(file_path)
    if not config_features:
        return True
    return any(f in selected_features for f in config_features)


def materialize_projection(
    repo: Repo,
    selected_features: Set[str],
    branch_name: str,
) -> str:
    """Create a projected variant branch from the current HEAD.

    Walks all tracked files, applies feature-based projection
    (strip non-selected annotated blocks, remove excluded files,
    remove annotation markers), and commits the result onto a new branch.

    Returns the projected branch name.
    """
    if repo.is_dirty(untracked_files=True):
        raise RuntimeError(
            "Working tree has uncommitted changes. "
            "Commit or stash them before projecting."
        )

    repo_root = Path(repo.working_tree_dir)
    source_branch = repo.active_branch.name
    source_sha = repo.head.commit.hexsha

    # Handle existing branch: overwrite if safe, abort if user has unsynced work
    if branch_name in [ref.name for ref in repo.branches]:
        proj_branch = repo.branches[branch_name]
        proj_tip = proj_branch.commit
        # A projection branch has exactly one commit beyond its parent.
        # If there are extra commits, the user edited the variant → protect it.
        if proj_tip.parents and proj_tip.parents[0].hexsha != proj_tip.parents[0].hexsha:
            pass  # unreachable, but keeps structure clear
        # Check if tip commit is a projection commit (starts with "Project variant:")
        if not proj_tip.message.startswith("Project variant:"):
            raise RuntimeError(
                f"Branch '{branch_name}' has commits beyond the projection. "
                "Sync back or delete it manually before re-projecting."
            )
        repo.git.branch("-D", branch_name)

    repo.git.checkout("-b", branch_name)

    try:
        tracked_files = repo.git.ls_files().splitlines()
        removed = []
        modified = []

        for rel_path in tracked_files:
            abs_path = repo_root / rel_path
            if not abs_path.is_file():
                continue

            # File-config exclusion (whole-file feature ownership)
            if not should_include_file(str(abs_path), selected_features):
                abs_path.unlink()
                removed.append(rel_path)
                continue

            # Read content; skip binary files
            try:
                content = abs_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                continue

            projected = materialize_file(content, selected_features)
            if projected != content:
                abs_path.write_text(projected, encoding="utf-8")
                modified.append(rel_path)

        if removed:
            repo.index.remove(removed)
        if modified:
            repo.index.add(modified)

        feature_list = ", ".join(sorted(selected_features))
        repo.index.commit(
            f"Project variant: [{feature_list}]\n\n"
            f"Source: {source_branch} ({source_sha[:10]})\n"
            f"Selected features: {feature_list}"
        )
    except Exception:
        repo.git.checkout(source_branch)
        try:
            repo.git.branch("-D", branch_name)
        except Exception:
            pass
        raise

    return branch_name
