import re
from pathlib import Path
from typing import Set

from git import Repo

from git_tool.finding_features import _features_from_file_mapping

_BEGIN_RE = re.compile(r".*&begin\[(?P<name>.*?)\].*")
_END_RE = re.compile(r".*&end\[(?P<name>.*?)\].*")


def parse_annotated_blocks(content: str) -> list[tuple[str, str | None, str]]:
    """Split file content into shared and annotated feature blocks."""
    lines = content.splitlines(keepends=True)
    blocks = []
    i = 0
    while i < len(lines):
        begin_match = _BEGIN_RE.match(lines[i])
        if begin_match:
            feature_name = begin_match.group("name")
            start = i
            i += 1
            while i < len(lines):
                end_match = _END_RE.match(lines[i])
                if end_match and end_match.group("name") == feature_name:
                    i += 1
                    break
                i += 1
            blocks.append(("feature", feature_name, "".join(lines[start:i])))
        else:
            start = i
            while i < len(lines) and not _BEGIN_RE.match(lines[i]):
                i += 1
            blocks.append(("shared", None, "".join(lines[start:i])))
    return blocks


def merge_projected_changes_into_target(
    target_content: str, projected_content: str
) -> str:
    """Update target content with changes from projected content.

    Only replace shared blocks and matching feature blocks. Keep any extra
    feature blocks that exist in target but not in the projection.
    """
    target_blocks = parse_annotated_blocks(target_content)
    projected_blocks = parse_annotated_blocks(projected_content)

    merged_blocks = []
    target_index = 0

    for projected_kind, projected_name, projected_text in projected_blocks:
        if projected_kind == "feature":
            # Advance until we find the matching feature block in target.
            while target_index < len(target_blocks):
                target_kind, target_name, target_text = target_blocks[target_index]
                if target_kind == "feature" and target_name == projected_name:
                    merged_blocks.append((target_kind, target_name, projected_text))
                    target_index += 1
                    break
                merged_blocks.append((target_kind, target_name, target_text))
                target_index += 1
        else:
            # Shared block: replace the next shared block in target.
            while target_index < len(target_blocks):
                target_kind, target_name, target_text = target_blocks[target_index]
                if target_kind == "shared":
                    merged_blocks.append((target_kind, target_name, projected_text))
                    target_index += 1
                    break
                merged_blocks.append((target_kind, target_name, target_text))
                target_index += 1

    # Append remaining target blocks unchanged.
    while target_index < len(target_blocks):
        merged_blocks.append(target_blocks[target_index])
        target_index += 1

    return "".join(text for _, _, text in merged_blocks)


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


def should_include_file(
    file_path: str, selected_features: Set[str], repo_root: Path | None = None
) -> bool:
    """Decide whether a file belongs in the projection.

    - No file-config mapping → platform code → always included
    - Mapped to at least one selected feature → included
    - Mapped only to non-selected features → excluded
    """
    config_features = _features_from_file_mapping(file_path, repo_root=repo_root)
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
            if not should_include_file(str(abs_path), selected_features, repo_root=repo_root):
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


def sync_projection_back(
    repo: Repo,
    projection_branch: str,
    target_branch: str | None = None,
) -> str:
    """Synchronize changes from a projected branch back into the target branch.

    This copies only edits made on the projected branch after the initial
    projection commit. Deletions caused by the projection step are ignored.
    """
    if repo.is_dirty(untracked_files=True):
        raise RuntimeError(
            "Working tree has uncommitted changes. "
            "Commit or stash them before syncing the projection back."
        )

    if projection_branch not in [ref.name for ref in repo.branches]:
        raise ValueError(f"Projection branch '{projection_branch}' does not exist.")

    if target_branch is None:
        target_branch = repo.active_branch.name

    if target_branch not in [ref.name for ref in repo.branches]:
        raise ValueError(f"Target branch '{target_branch}' does not exist.")

    source_branch = repo.active_branch.name
    if source_branch != target_branch:
        repo.git.checkout(target_branch)

    projection_commit = None
    for commit in repo.iter_commits(projection_branch):
        if commit.message.startswith("Project variant:"):
            projection_commit = commit
            break

    if projection_commit is None:
        raise ValueError(
            f"Projection branch '{projection_branch}' does not appear to be a projection branch."
        )

    diff_files = repo.git.diff(
        "--name-only",
        f"{projection_commit.hexsha}..{projection_branch}",
        "--diff-filter=ACMRTUXB",
    ).splitlines()
    if not diff_files:
        return target_branch

    changed_files = []
    for file_path in diff_files:
        target_file = Path(repo.working_tree_dir) / file_path
        projected_content = repo.git.show(f"{projection_branch}:{file_path}")

        if target_file.exists():
            target_content = target_file.read_text(encoding="utf-8")
            merged_content = merge_projected_changes_into_target(
                target_content, projected_content
            )
            if merged_content != target_content:
                target_file.write_text(merged_content, encoding="utf-8")
                changed_files.append(file_path)
        else:
            # New file in projection branch: add it directly.
            target_file.write_text(projected_content, encoding="utf-8")
            changed_files.append(file_path)

    if not changed_files:
        return target_branch

    repo.index.add(changed_files)
    repo.index.commit(
        f"Sync projection '{projection_branch}' back into {target_branch}\n\n"
        f"Copied changes from projected variant {projection_branch}."
    )
    return target_branch
