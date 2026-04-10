from collections import namedtuple
from typing import List, TypedDict

from git import Commit, GitCommandError
from git_tool.feature_data.file_based_git_info import get_commits_for_file

from git_tool.feature_data.models_and_context.repo_context import (
    FEATURE_BRANCH_NAME,
    branch_folder_list,
    repo_context,
)


from enum import Enum


class DerivationSource(str, Enum):
    ANNOTATION = "annotation"
    FILE_CONFIG = "file-config"
    HISTORY = "history"


class GitChanges(TypedDict):
    """
    List files by git status
    """

    staged_files: List[str]
    unstaged_files: List[str]
    untracked_files: List[str]


GitStatusEntry = namedtuple("GitStatusEntry", ["status", "file_path"])

# Usages: FEATURE ADD, ADD-FROM-STAGED, PRE-COMMIT, STATUS
def get_files_by_git_change() -> GitChanges:
    """
    Retrieves files sorted by the type of git change (staged, unstaged, untracked).
    Can be used in combination with finding feature annotations for files to help
    figure out which features are already staged.

    Returns:
        Dict[str, List[str]]: A dictionary with keys 'staged_files', 'unstaged_files',
        and 'untracked_files', each containing a list of file paths.
    """

    def convert_to_status_entry(short_status_line: str) -> GitStatusEntry:
        return GitStatusEntry(short_status_line[:2], short_status_line[3:])

    with repo_context() as repo:
        result = repo.git.status("-s")
        lines = list(map(convert_to_status_entry, result.split("\n")))
        changes: GitChanges = {
            "staged_files": [],
            "unstaged_files": [],
            "untracked_files": [],
        }
        for entry in lines:
            if entry.status and entry.status[0] != " " and entry.status != "??":
                changes["staged_files"].append(entry.file_path)
            if entry.status and entry.status[1] != " " and entry.status != "??":
                changes["unstaged_files"].append(entry.file_path)
            if entry.status == "??":
                changes["untracked_files"].append(entry.file_path)

        return dict(changes)


def find_annotations_for_file(file: str) -> List[str]:
    """
    Parse file for comment-based feature hints and search for file and folder annotations.
    This makes use of the feature-annotation system. Not documented further here.
    """
    from git_tool.finding_features import features_for_file_by_annotation
    return features_for_file_by_annotation(file)

# Usage: FEATURE ADD-FROM-STAGED, BLAME, STATUS
def get_features_for_file(
    file_path: str, include_history: bool = False
) -> List[str]:
    """
    Retrieves features for a given file using authority ordering:

    AUTHORITATIVE sources (always used):
        1. Code annotations (&begin[Feature]/&end[Feature])
        2. File-config mappings (.feature-map.json)

    FALLBACK source (only when no authoritative features found, or explicitly requested):
        3. Metadata branch history (commit-to-feature mappings)

    Annotations represent the current truth of the feature model.
    History is a secondary signal — useful for archaeology but noisy
    after refactorings, as it accumulates features from all past commits.

    Args:
        file_path: The path to the file whose features are to be retrieved.
        include_history: If True, always include history-based features
                         alongside authoritative sources.

    Returns:
        A deduplicated list of features associated with the file.
    """
    from git_tool.finding_features import build_feature_mapping_from_file

    features = set()

    # Authoritative: annotations + file-config
    try:
        for m in build_feature_mapping_from_file(file_path):
            features.add(m.feature_id)
    except (FileNotFoundError, OSError):
        pass

    # Fallback: metadata branch history (only if no authoritative features, or forced)
    if not features or include_history:
        commits = get_commits_for_file(file_name=file_path, branch_name=None)
        with branch_folder_list() as (feature_folders, _):
            for commit in commits:
                for feature in feature_folders:
                    feature_name = get_feature_name_from_folder(feature)
                    if commit_in_feature_folder(commit, feature_name):
                        features.add(feature_name)

    return list(features)

# Usages: FEATURE INFO, FEATURE STATUS
def get_commits_for_feature(feature_uuid: str) -> list[Commit]:

    with repo_context() as repo:
        output = repo.git.ls_tree(
            "-d", "--name-only", f"{FEATURE_BRANCH_NAME}:{feature_uuid}"
        )
        return [repo.commit(x) for x in output.split("\n")]


def commit_in_feature_folder(commit: str, feature_folder: str) -> bool:
    """
    Check if a commit is present for a feature.

    Args:
        commit (str): The commit hash.
        feature_folder (str): The path to the feature folder.

    Returns:
        bool: True if the commit is present in the feature folder, False otherwise.
    """
    assert isinstance(
        commit, str
    ), f"Expected commit to be a string, but got {type(commit).__name__}"
    assert isinstance(
        feature_folder, str
    ), f"Expected feature_folder to be a string, but got {type(feature_folder).__name__}"
    with repo_context() as repo:
        commit_obj =repo.commit(commit)
    result = commit_obj.hexsha in [x.hexsha for x in get_commits_for_feature(feature_uuid=feature_folder)]
    return result


def get_feature_for_hunk(file_path: str, hunk: str) -> List[str]:
    """
    Retrieves features for specific changes (hunks) in a given file.

    Args:
        file_path (str): The path to the file.
        hunk (str): The specific changes (hunks) in the file.

    Returns:
        List[str]: A list of features associated with the specific hunks in the file.
    """
    raise NotImplementedError()
    # features = get_features_for_hunk(file_path, hunk)
    # return features


def get_feature_name_from_folder(feature_folder: str) -> str:
    """
    Retrieve the feature name from a feature folder path.

    Args:
        feature_folder (str): The path to the feature folder.

    Returns:
        str: The name of the feature.
    """
    # Extract the feature name from the folder path
    return str(feature_folder)
