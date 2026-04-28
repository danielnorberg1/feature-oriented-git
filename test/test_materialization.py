import json
import shutil
from pathlib import Path

import pytest
from git import Repo

from git_tool.materialization import (
    materialize_file,
    materialize_projection,
    should_include_file,
    sync_projection_back,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def proj_repo(tmp_path):
    """Fresh git repo with an initial commit (function-scoped)."""
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()

    init = tmp_path / "README.md"
    init.write_text("# project\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")
    return repo


# ---------------------------------------------------------------------------
# materialize_file – unit tests
# ---------------------------------------------------------------------------

def test_materialize_keeps_selected_feature():
    content = (
        "shared\n"
        "# &begin[Login]\n"
        "login_code()\n"
        "# &end[Login]\n"
        "more shared\n"
    )
    result = materialize_file(content, {"Login"})
    assert "login_code()" in result
    assert "&begin" not in result
    assert "&end" not in result
    assert "shared" in result


def test_materialize_strips_unselected_feature():
    content = (
        "shared\n"
        "# &begin[Signup]\n"
        "signup_code()\n"
        "# &end[Signup]\n"
        "more shared\n"
    )
    result = materialize_file(content, {"Login"})
    assert "signup_code()" not in result
    assert "shared" in result


def test_materialize_mixed_features():
    content = (
        "platform()\n"
        "# &begin[Login]\n"
        "login()\n"
        "# &end[Login]\n"
        "# &begin[Signup]\n"
        "signup()\n"
        "# &end[Signup]\n"
        "end()\n"
    )
    result = materialize_file(content, {"Login"})
    assert "login()" in result
    assert "signup()" not in result
    assert "platform()" in result
    assert "end()" in result
    assert "&begin" not in result


def test_materialize_no_annotations():
    content = "just plain code\nno features\n"
    result = materialize_file(content, {"Login"})
    assert result == content


def test_materialize_nested_keep_outer_only():
    """Selecting outer feature but not inner: inner block stripped."""
    content = (
        "# &begin[Auth]\n"
        "authenticate()\n"
        "# &begin[OAuth]\n"
        "oauth_flow()\n"
        "# &end[OAuth]\n"
        "validate_token()\n"
        "# &end[Auth]\n"
    )
    result = materialize_file(content, {"Auth"})
    assert "authenticate()" in result
    assert "validate_token()" in result
    assert "oauth_flow()" not in result
    assert "&begin" not in result


def test_materialize_nested_keep_both():
    """Selecting both features: all code kept."""
    content = (
        "# &begin[Auth]\n"
        "authenticate()\n"
        "# &begin[OAuth]\n"
        "oauth_flow()\n"
        "# &end[OAuth]\n"
        "validate_token()\n"
        "# &end[Auth]\n"
    )
    result = materialize_file(content, {"Auth", "OAuth"})
    assert "authenticate()" in result
    assert "oauth_flow()" in result
    assert "validate_token()" in result


def test_materialize_nested_inner_only_not_kept():
    """Selecting only inner feature without outer: nothing kept (parent not selected)."""
    content = (
        "# &begin[Auth]\n"
        "authenticate()\n"
        "# &begin[OAuth]\n"
        "oauth_flow()\n"
        "# &end[OAuth]\n"
        "# &end[Auth]\n"
    )
    result = materialize_file(content, {"OAuth"})
    assert "oauth_flow()" not in result
    assert "authenticate()" not in result


# ---------------------------------------------------------------------------
# should_include_file – unit tests
# ---------------------------------------------------------------------------

def test_include_platform_file(tmp_path, monkeypatch):
    """Files without file-config mapping are platform code → always included."""
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "app.py"
    f.write_text("code\n")
    assert should_include_file(str(f), {"Login"}) is True


def test_exclude_file_mapped_to_unselected_feature(tmp_path, monkeypatch):
    feature_map = {"mappings": [{"pattern": "src/checkout/*", "feature": "Checkout"}]}
    (tmp_path / ".feature-map.json").write_text(json.dumps(feature_map))
    (tmp_path / "src" / "checkout").mkdir(parents=True)
    f = tmp_path / "src" / "checkout" / "pay.py"
    f.write_text("pay()\n")
    monkeypatch.chdir(tmp_path)
    assert should_include_file(str(f), {"Login"}) is False


def test_include_file_mapped_to_selected_feature(tmp_path, monkeypatch):
    feature_map = {"mappings": [{"pattern": "src/auth/*", "feature": "Login"}]}
    (tmp_path / ".feature-map.json").write_text(json.dumps(feature_map))
    (tmp_path / "src" / "auth").mkdir(parents=True)
    f = tmp_path / "src" / "auth" / "login.py"
    f.write_text("login()\n")
    monkeypatch.chdir(tmp_path)
    assert should_include_file(str(f), {"Login"}) is True


# ---------------------------------------------------------------------------
# materialize_projection – integration tests
# ---------------------------------------------------------------------------

def test_projection_strips_annotations_and_unselected(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    # Create a multi-feature file
    src = root / "app.py"
    src.write_text(
        "# shared\n"
        "# &begin[Login]\n"
        "login()\n"
        "# &end[Login]\n"
        "# &begin[Signup]\n"
        "signup()\n"
        "# &end[Signup]\n"
        "# end shared\n"
    )
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add multi-feature app")

    materialize_projection(proj_repo, {"Login"}, "project/Login")

    assert proj_repo.active_branch.name == "project/Login"
    projected = src.read_text()
    assert "login()" in projected
    assert "signup()" not in projected
    assert "&begin" not in projected
    assert "&end" not in projected
    assert "# shared" in projected


def test_projection_removes_excluded_files(proj_repo, monkeypatch):
    root = Path(proj_repo.working_tree_dir)

    # Setup file-config mapping
    feature_map = {"mappings": [{"pattern": "checkout/*", "feature": "Checkout"}]}
    (root / ".feature-map.json").write_text(json.dumps(feature_map))
    checkout_dir = root / "checkout"
    checkout_dir.mkdir()
    (checkout_dir / "pay.py").write_text("pay()\n")

    # Also add a platform file
    (root / "main.py").write_text("main()\n")

    proj_repo.index.add([
        ".feature-map.json", "checkout/pay.py", "main.py"
    ])
    proj_repo.index.commit("Add checkout + main")

    monkeypatch.chdir(root)
    materialize_projection(proj_repo, {"Login"}, "project/Login")

    assert not (checkout_dir / "pay.py").exists()
    assert (root / "main.py").exists()


def test_projection_creates_commit(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text("# &begin[A]\na()\n# &end[A]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add A")

    materialize_projection(proj_repo, {"A"}, "project/A")

    commit_msg = proj_repo.head.commit.message
    assert "Project variant" in commit_msg
    assert "A" in commit_msg


def test_projection_refuses_dirty_tree(proj_repo):
    root = Path(proj_repo.working_tree_dir)
    (root / "dirty.txt").write_text("uncommitted\n")

    with pytest.raises(RuntimeError, match="uncommitted"):
        materialize_projection(proj_repo, {"X"}, "project/X")


def test_projection_overwrites_existing_branch(proj_repo):
    """Re-projecting the same feature selection should overwrite the branch."""
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text("# &begin[Login]\nv1()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add Login v1")

    # First projection
    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"Login"}, "project/Login")
    first_sha = proj_repo.head.commit.hexsha
    proj_repo.git.checkout(source_branch)

    # Update the source
    src.write_text("# &begin[Login]\nv2()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Update Login v2")

    # Re-project same branch name → should overwrite
    materialize_projection(proj_repo, {"Login"}, "project/Login")
    assert proj_repo.active_branch.name == "project/Login"
    assert proj_repo.head.commit.hexsha != first_sha
    assert "v2()" in src.read_text()


def test_projection_refuses_overwrite_with_user_commits(proj_repo):
    """Should refuse to overwrite if user added commits beyond the projection."""
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text("# &begin[Login]\nlogin()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add Login")

    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"Login"}, "project/Login")

    # Simulate user editing the projected branch
    src.write_text("login()\nmy_fix()\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("User fix on projected branch")

    proj_repo.git.checkout(source_branch)

    with pytest.raises(RuntimeError, match="beyond the projection"):
        materialize_projection(proj_repo, {"Login"}, "project/Login")


def test_sync_projection_back_applies_projection_edits(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text("# shared\n# &begin[Login]\nlogin_v1()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add Login v1")

    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"Login"}, "project/Login")
    proj_repo.git.checkout("project/Login")

    # Edit the projected variant
    src.write_text("# shared\n# &begin[Login]\nlogin_v2()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Update projected login")

    proj_repo.git.checkout(source_branch)
    sync_projection_back(proj_repo, "project/Login", source_branch)

    assert "login_v2()" in src.read_text()


def test_sync_projection_back_refreshes_missing_provenance(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text("# shared\n# &begin[Login]\nlogin_v1()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add Login v1")

    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"Login"}, "project/Login-old")
    proj_repo.git.checkout(source_branch)

    # Update source annotated file before syncing.
    src.write_text("# shared\n# &begin[Login]\nlogin_v1_updated()\n# &end[Login]\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Update source annotated login")

    proj_repo.git.checkout("project/Login-old")
    (root / ".feature-provenance.json").unlink()
    proj_repo.index.remove([".feature-provenance.json"])
    src.write_text("# shared\nlogin_v2()\n")
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Update projected login without provenance")

    proj_repo.git.checkout(source_branch)
    sync_projection_back(proj_repo, "project/Login-old", source_branch)

    assert "login_v2()" in src.read_text()


def test_sync_projection_back_preserves_unselected_features(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text(
        "# &begin[FeatureA]\n"
        "feature_a()\n"
        "# &end[FeatureA]\n"
        "# &begin[FeatureB]\n"
        "feature_b()\n"
        "# &end[FeatureB]\n"
        "# &begin[FeatureC]\n"
        "feature_c()\n"
        "# &end[FeatureC]\n"
    )
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add A,B,C")

    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"FeatureA", "FeatureC"}, "project/A-C")
    proj_repo.git.checkout("project/A-C")

    # Change only FeatureA in the projected branch
    src.write_text(
        "# &begin[FeatureA]\n"
        "feature_a_updated()\n"
        "# &end[FeatureA]\n"
        "# &begin[FeatureC]\n"
        "feature_c()\n"
        "# &end[FeatureC]\n"
    )
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Update projected FeatureA")

    proj_repo.git.checkout(source_branch)
    sync_projection_back(proj_repo, "project/A-C", source_branch)

    text = src.read_text()
    assert "feature_a_updated()" in text
    assert "feature_b()" in text
    assert "feature_c()" in text


def test_sync_projection_back_preserves_annotation_markers_after_line_deletion(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    src = root / "app.py"
    src.write_text(
        "shared\n"
        "# &begin[Login]\n"
        "login1()\n"
        "login2()\n"
        "# &end[Login]\n"
        "shared2\n"
    )
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Add annotated login sequence")

    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"Login"}, "project/Login")
    proj_repo.git.checkout("project/Login")

    projected_text = src.read_text().splitlines(keepends=True)
    filtered = [line for line in projected_text if line.strip() != "login2()"]
    src.write_text("".join(filtered))
    proj_repo.index.add(["app.py"])
    proj_repo.index.commit("Delete projected login line")

    proj_repo.git.checkout(source_branch)
    sync_projection_back(proj_repo, "project/Login", source_branch)

    merged = src.read_text()
    assert "login2()" not in merged
    assert "# &end[Login]" in merged
    assert merged.index("# &end[Login]") > merged.index("login1()")
    assert "shared2" in merged


def test_sync_projection_back_ignores_projection_deletions(proj_repo):
    root = Path(proj_repo.working_tree_dir)

    src = root / "main.py"
    src.write_text("main()\n")
    src1 = root / "checkout.py"
    src1.write_text("checkout()\n")
    proj_repo.index.add(["main.py", "checkout.py"])
    proj_repo.index.commit("Add files")

    feature_map = {"mappings": [{"pattern": "checkout.py", "feature": "Checkout"}]}
    (root / ".feature-map.json").write_text(json.dumps(feature_map))
    proj_repo.index.add([".feature-map.json"])
    proj_repo.index.commit("Add feature map")

    source_branch = proj_repo.active_branch.name
    materialize_projection(proj_repo, {"Login"}, "project/Login")
    proj_repo.git.checkout("project/Login")

    # projected branch should remove checkout.py
    assert not (root / "checkout.py").exists()

    # make a change to main.py on the projection branch
    src.write_text("main()\nupdated()\n")
    proj_repo.index.add(["main.py"])
    proj_repo.index.commit("Update projected main")

    proj_repo.git.checkout(source_branch)
    sync_projection_back(proj_repo, "project/Login", source_branch)

    assert (root / "checkout.py").exists()
    assert "updated()" in src.read_text()
