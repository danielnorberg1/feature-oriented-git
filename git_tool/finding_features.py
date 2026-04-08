import re
from dataclasses import dataclass

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
        assigned_in_code = extract_features_from_annotation(f.read())
    return assigned_by_file + assigned_by_folder + assigned_in_code



