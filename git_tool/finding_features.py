import re
from dataclasses import dataclass
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
    # features can also be assigned from the file itself as well as from file and folder mappings
    # skip that for now
    return features


def features_for_file_by_annotation(file_name: str) -> list[str]:
    assigned_by_file = []
    assigned_by_folder = []
    with open(file_name, "r") as f:
        matches = extract_features_from_annotation(f.read())
    assigned_in_code = [m.name for m in matches]
    return assigned_by_file + assigned_by_folder + assigned_in_code


def build_feature_mapping_from_file(
    file_path: str, commit_sha: Optional[str] = None 
) -> List[FeatureMapping]:
    # Read the file as a list of lines so we can track line numbers.
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    mappings: List[FeatureMapping] = []
    active_feature = None
    begin_line = None

    for index, line in enumerate(lines, start=1):
        # Detect the begin marker and remember the feature name.
        begin_match = re.match(r".*&begin\[(?P<FeatureName>.*?)\].*", line)
        if begin_match:
           active_feature = begin_match.group("FeatureName")
           # The annotated code body starts on the next line.
           begin_line = index + 1
           continue
        # Detect the matching end marker for the active feature.
        end_match = re.match(r".*&end\[(?P<FeatureName>.*?)\].*", line)
        if (
            end_match
            and active_feature == end_match.group("FeatureName")
            and begin_line is not None
        ):
            # The annotated body ends on the line before the end marker.
            end_line = index - 1
            mappings.append(
                FeatureMapping(
                    feature_id=active_feature,
                    file_path=str(Path(file_path).resolve()),
                    start_line=begin_line,
                    end_line=end_line,
                    commit_sha=commit_sha,
                )
            )
            # Reset state for the next annotation.
            active_feature = None
            begin_line = None

    return mappings
        
        
        


