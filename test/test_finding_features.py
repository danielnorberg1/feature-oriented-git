from contextlib import contextmanager
from pathlib import Path

from git_tool import finding_features
from git_tool.ci.subcommands import feature_commit
from typer.testing import CliRunner

TESTSTRING = """
    Hier ist etwas Code &begin[FEATURE1]
    Code für FEATURE1
    weiterer Code für FEATURE1
    &end[FEATURE1]
    &begin[FEATURE2]
    Code für FEATURE2
    &end[FEATURE2]
    """


def test_extract_features():
    features = finding_features.extract_features_from_annotation(TESTSTRING)
    feature_names = list(map(lambda x: x.name, features))
    assert feature_names == ["FEATURE1", "FEATURE2"]


def test_git_features(git_repo):
    # Dateipfad im Repository
    repo_path = Path(git_repo.working_tree_dir)
    file_path = repo_path / "test.py"
    file_path.write_text("# Initial commit\n")
    git_repo.index.add([str(file_path)])
    git_repo.index.commit("Initial commit")

    # Schreibe in die Datei mit Feature Annotationen
    file_path.write_text(
        file_path.read_text()
        + "# &begin[Feature1]\n"
        + "print('Hello, Feature1!')\n"
        + "# &end[Feature1]\n"
    )
    head_commit = git_repo.head.commit
    diff = head_commit.diff(None, create_patch=True)
    assert len(diff) == 1, "Exactly one diff should be created here"
    mapped_to_feature = [
        (finding_features.get_features_for_diff(d), d.a_path) for d in diff
    ]
    found_features = mapped_to_feature[0][0]
    assert (
        len(found_features) == 1
    ), "Expecting one Feature that is not committed"
    assert (
        found_features[0].name == "Feature1"
    ), "Expecting to find the correct name for feature"


def test_build_feature_mapping_from_file(tmp_path):
    # Create a temporary source file with an annotation block.
    file_path = tmp_path / "sample.py"
    file_path.write_text(
        "# before\n"
        + "# &begin[FeatureA]\n"
        + "print('A')\n"
        + "# &end[FeatureA]\n"
        + "# after\n"
    )

    # Build feature mappings from the file and verify the result.
    mappings = finding_features.build_feature_mapping_from_file(str(file_path))

    assert len(mappings) == 1
    mapping = mappings[0]
    assert mapping.feature_id == "FeatureA"
    assert mapping.file_path == str(file_path.resolve())
    assert mapping.start_line == 3
    assert mapping.end_line == 3


def test_features_for_file_by_annotation_returns_strings(tmp_path):
    """features_for_file_by_annotation should return a list of feature name strings."""
    file_path = tmp_path / "annotated.py"
    file_path.write_text(
        "# &begin[Login]\n"
        + "do_login()\n"
        + "# &end[Login]\n"
        + "# &begin[Signup]\n"
        + "do_signup()\n"
        + "# &end[Signup]\n"
    )

    result = finding_features.features_for_file_by_annotation(str(file_path))

    assert result == ["Login", "Signup"]
    assert all(isinstance(name, str) for name in result)


def test_features_from_file_mapping(tmp_path, monkeypatch):
    """File mapping should match glob patterns from .feature-map.json."""
    import json

    # Create a .feature-map.json in the tmp directory
    feature_map = {
        "mappings": [
            {"pattern": "src/auth/*", "feature": "Login"},
            {"pattern": "src/checkout/*", "feature": "Checkout"},
        ]
    }
    (tmp_path / ".feature-map.json").write_text(json.dumps(feature_map))

    # Create a source file under src/auth/
    auth_dir = tmp_path / "src" / "auth"
    auth_dir.mkdir(parents=True)
    source_file = auth_dir / "handler.py"
    source_file.write_text("# no annotations\n")

    # Set cwd to tmp_path so the feature map is found
    monkeypatch.chdir(tmp_path)

    result = finding_features.features_for_file_by_annotation(str(source_file))
    assert "Login" in result


def test_features_from_file_mapping_combined_with_annotations(tmp_path, monkeypatch):
    """File mapping and annotations should be combined."""
    import json

    feature_map = {
        "mappings": [
            {"pattern": "src/*", "feature": "MappedFeature"},
        ]
    }
    (tmp_path / ".feature-map.json").write_text(json.dumps(feature_map))

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source_file = src_dir / "app.py"
    source_file.write_text(
        "# &begin[AnnotatedFeature]\n"
        + "code()\n"
        + "# &end[AnnotatedFeature]\n"
    )

    monkeypatch.chdir(tmp_path)

    result = finding_features.features_for_file_by_annotation(str(source_file))
    assert "MappedFeature" in result
    assert "AnnotatedFeature" in result


def test_build_feature_mapping_includes_file_config(tmp_path, monkeypatch):
    """build_feature_mapping_from_file should return FeatureMappings from both annotations and file config."""
    import json

    feature_map = {
        "mappings": [
            {"pattern": "src/*", "feature": "ConfigFeature"},
        ]
    }
    (tmp_path / ".feature-map.json").write_text(json.dumps(feature_map))

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source_file = src_dir / "module.py"
    source_file.write_text(
        "# preamble\n"
        "# &begin[InlineFeature]\n"
        "do_stuff()\n"
        "# &end[InlineFeature]\n"
        "# end\n"
    )

    monkeypatch.chdir(tmp_path)

    mappings = finding_features.build_feature_mapping_from_file(str(source_file))

    feature_ids = [m.feature_id for m in mappings]
    assert "InlineFeature" in feature_ids
    assert "ConfigFeature" in feature_ids

    # Inline annotation should have precise line range
    inline = [m for m in mappings if m.feature_id == "InlineFeature"][0]
    assert inline.start_line == 3
    assert inline.end_line == 3

    # File-config mapping should cover the whole file
    config = [m for m in mappings if m.feature_id == "ConfigFeature"][0]
    assert config.start_line == 1
    assert config.end_line == 5  # 5 lines total


def test_derive_features_from_commit(git_repo):
    """Auto-derivation should extract feature names from annotated files in a commit."""
    from git_tool.finding_features import derive_features_from_commit

    repo_path = Path(git_repo.working_tree_dir)

    # Initial commit so HEAD exists
    init_file = repo_path / "init.txt"
    init_file.write_text("init\n")
    git_repo.index.add([str(init_file)])
    git_repo.index.commit("Initial commit")

    # Create a file with feature annotations
    feature_file = repo_path / "auth.py"
    feature_file.write_text(
        "# &begin[Login]\n"
        + "def login(): pass\n"
        + "# &end[Login]\n"
    )
    git_repo.index.add([str(feature_file)])
    commit = git_repo.index.commit("Add login feature")

    result = derive_features_from_commit(commit)
    assert "Login" in result


def test_derive_features_skips_deleted_files(git_repo):
    """Auto-derivation should gracefully skip files that were deleted in the commit."""
    from git_tool.finding_features import derive_features_from_commit

    repo_path = Path(git_repo.working_tree_dir)

    # Create and commit a file
    temp_file = repo_path / "temp.py"
    temp_file.write_text("# &begin[TempFeature]\ncode()\n# &end[TempFeature]\n")
    git_repo.index.add([str(temp_file)])
    git_repo.index.commit("Add temp file")

    # Delete and commit
    temp_file.unlink()
    git_repo.index.remove([str(temp_file)])
    delete_commit = git_repo.index.commit("Remove temp file")

    # Should not crash, should return empty (file no longer exists to read annotations from)
    result = derive_features_from_commit(delete_commit)
    assert isinstance(result, list)


def test_feature_sync_command_calls_sync_feature_branch(monkeypatch):
    """The sync command should call the feature branch synchronizer."""
    @contextmanager
    def dummy_context():
        yield None

    monkeypatch.setattr(feature_commit, "repo_context", dummy_context)

    sync_called = []

    def fake_sync():
        sync_called.append(True)

    monkeypatch.setattr(feature_commit, "sync_feature_branch", fake_sync)

    feature_commit.sync_feature_metadata()

    assert sync_called == [True]

