import json
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional

from git import Diff


@dataclass
class FeatureMatches:
    name: str
    code: str

@dataclass
class FeatureMapping:
    feature_id: str
    file_path: str
    start_line: int
    end_line: int
    commit_sha: str | None = None


def extract_features_from_annotation(text: str) -> list[FeatureMatches]:
    """Extract Features as well as information about their location in code depending on
    &begin[] und &end[] Tags.

    @param text: content from which features are extracted
    """
    # regex for regex101.com
    # &begin\[(?<FeatureName>.*?)\](?<FeatureCode>.*?)&end\[\1\]
    # with flags gms
    feature_pattern = r"&begin\[(?P<FeatureName>.*?)\](?P<FeatureCode>.*?)&end\[(?P=FeatureName)\]"
    feature_matches = re.finditer(
        pattern=feature_pattern, string=text, flags=re.DOTALL
    )
    feature_list = [
        FeatureMatches(
            name=match.group("FeatureName"),
            code=match.group("FeatureCode").strip(),
        )
        for match in feature_matches
    ]

    return feature_list


def get_features_for_diff(diff: Diff) -> list[FeatureMatches]:
    str_diff = (
        diff.diff.decode("utf-8") if isinstance(diff.diff, bytes) else diff.diff
    )
    features = extract_features_from_annotation(str_diff)
    # Also include features from file-config mappings
    file_path = diff.b_path or diff.a_path
    if file_path:
        for name in _features_from_file_mapping(file_path):
            features.append(FeatureMatches(name=name, code=""))
    return features


FEATURE_MAP_FILENAME = ".feature-map.json"


def _find_feature_map() -> Path | None:
    """Walk up from cwd to find a .feature-map.json file."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / FEATURE_MAP_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _load_feature_map() -> list[dict]:
    """Load mappings from .feature-map.json. Returns empty list if not found."""
    path = _find_feature_map()
    if path is None:
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("mappings", [])


def _features_from_file_mapping(file_name: str) -> list[str]:
    """Match a file path against glob patterns in .feature-map.json."""
    mappings = _load_feature_map()
    try:
        rel_path = str(Path(file_name).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        rel_path = file_name
    features = []
    for entry in mappings:
        if fnmatch(rel_path, entry["pattern"]):
            features.append(entry["feature"])
    return features


def build_feature_mapping_from_file(
    file_path: str, commit_sha: Optional[str] = None 
) -> List[FeatureMapping]:
    """Core derivation: returns structured FeatureMappings from all file-level sources.
    
    Sources:
        1. Inline annotations (&begin[Feature]/&end[Feature])
        2. File-to-feature config (.feature-map.json glob patterns)
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    resolved_path = str(Path(file_path).resolve())
    line_count = len(lines)
    mappings: List[FeatureMapping] = []

    # Source 1: inline annotations (line-level precision, stack-based for nesting)
    feature_stack: list[tuple[str, int]] = []  # (feature_name, begin_content_line)

    for index, line in enumerate(lines, start=1):
        begin_match = re.match(r".*&begin\[(?P<FeatureName>.*?)\].*", line)
        if begin_match:
            feature_stack.append((begin_match.group("FeatureName"), index + 1))
            continue
        end_match = re.match(r".*&end\[(?P<FeatureName>.*?)\].*", line)
        if (
            end_match
            and feature_stack
            and feature_stack[-1][0] == end_match.group("FeatureName")
        ):
            feature_name, begin_line = feature_stack.pop()
            end_line = index - 1
            mappings.append(
                FeatureMapping(
                    feature_id=feature_name,
                    file_path=resolved_path,
                    start_line=begin_line,
                    end_line=end_line,
                    commit_sha=commit_sha,
                )
            )

    # Source 2: file-config mappings (whole-file scope)
    for feature_name in _features_from_file_mapping(file_path):
        mappings.append(
            FeatureMapping(
                feature_id=feature_name,
                file_path=resolved_path,
                start_line=1,
                end_line=line_count,
                commit_sha=commit_sha,
            )
        )

    return mappings


def features_for_file_by_annotation(file_name: str) -> list[str]:
    """Convenience: return deduplicated feature names from all file-level sources."""
    mappings = build_feature_mapping_from_file(file_name)
    return list(dict.fromkeys(m.feature_id for m in mappings))


def derive_features_from_commit(commit_obj) -> list[str]:
    """Auto-derive feature names from files changed in a commit.

    Uses the authoritative derivation engine (annotations + file-config).
    Falls back gracefully if files can't be read (e.g., deleted files).
    """
    features = set()
    repo_root = Path(commit_obj.repo.working_tree_dir)
    changed_files = commit_obj.stats.files.keys()
    for file_path in changed_files:
        abs_path = str(repo_root / file_path)
        try:
            for m in build_feature_mapping_from_file(abs_path):
                features.add(m.feature_id)
        except (FileNotFoundError, OSError):
            continue
    return list(features)
