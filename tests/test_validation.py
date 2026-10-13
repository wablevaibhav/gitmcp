from __future__ import annotations

import pytest
from pydantic import ValidationError

from gitmcp.config import Settings
from gitmcp.schemas import inputs as i


def test_valid_repo_input() -> None:
    p = i.GetRepositoryInput(owner="octocat", repo="Hello-World")
    assert (p.owner, p.repo) == ("octocat", "Hello-World")


@pytest.mark.parametrize("owner", ["", "-bad", "has space", "a" * 40, "../x", "a/b"])
def test_bad_owner(owner: str) -> None:
    with pytest.raises(ValidationError):
        i.GetRepositoryInput(owner=owner, repo="r")


@pytest.mark.parametrize("repo", ["", ".", "..", "a/b", "sp ace", "x" * 101])
def test_bad_repo(repo: str) -> None:
    with pytest.raises(ValidationError):
        i.GetRepositoryInput(owner="o", repo=repo)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        i.GetRepositoryInput(owner="o", repo="r", token="x")  # type: ignore[call-arg]


@pytest.mark.parametrize("page", [0, -1, 1001])
def test_bad_page(page: int) -> None:
    with pytest.raises(ValidationError):
        i.ListIssuesInput(owner="o", repo="r", page=page)


@pytest.mark.parametrize("per_page", [0, 101])
def test_bad_per_page(per_page: int) -> None:
    with pytest.raises(ValidationError):
        i.ListCommitsInput(owner="o", repo="r", per_page=per_page)


def test_bad_state() -> None:
    with pytest.raises(ValidationError):
        i.ListIssuesInput(owner="o", repo="r", state="merged")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ref", ["a..b", "a b", "-x", "x/", "x.lock", "a~1", "a^", "a:b", "a?", "a\\b", "@{u}", "a//b"]
)
def test_bad_refs(ref: str) -> None:
    with pytest.raises(ValidationError):
        i.GetCommitInput(owner="o", repo="r", ref=ref)


@pytest.mark.parametrize("ref", ["main", "feature/x-1", "v1.2.3", "a" * 40])
def test_good_refs(ref: str) -> None:
    assert i.GetCommitInput(owner="o", repo="r", ref=ref).ref == ref


def test_path_traversal_rejected() -> None:
    with pytest.raises(ValidationError):
        i.GetFileInput(owner="o", repo="r", path="a/../../etc/passwd")


def test_path_leading_slash_normalised() -> None:
    assert i.GetFileInput(owner="o", repo="r", path="/src/x.py").path == "src/x.py"


def test_query_length_limit() -> None:
    with pytest.raises(ValidationError):
        i.SearchCodeInput(query="x" * 257)
    with pytest.raises(ValidationError):
        i.SearchRepositoriesInput(query="")


def test_language_must_be_single_word() -> None:
    with pytest.raises(ValidationError):
        i.SearchRepositoriesInput(query="x", language="c plus plus stars:>1")


def test_issue_number_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        i.GetIssueInput(owner="o", repo="r", issue_number=0)


def test_write_body_limit() -> None:
    with pytest.raises(ValidationError):
        i.AddIssueCommentInput(owner="o", repo="r", issue_number=1, body="x" * 65_537)
    with pytest.raises(ValidationError):
        i.CreateIssueInput(owner="o", repo="r", title=" ")


def test_settings_defaults_are_safe() -> None:
    s = Settings(_env_file=None)
    assert s.github_enable_write_tools is False
    assert s.github_token is None


def test_settings_blank_token_is_none_and_secret_hidden() -> None:
    assert Settings(_env_file=None, github_token="  ").github_token is None
    s = Settings(_env_file=None, github_token="ghp_" + "B" * 36)
    assert "ghp_" not in repr(s)
    assert "ghp_" not in str(s)


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ENABLE_WRITE_TOOLS", "true")
    monkeypatch.setenv("GITHUB_MAX_RETRIES", "5")
    monkeypatch.setenv("GITMCP_LOG_LEVEL", "debug")
    s = Settings(_env_file=None)
    assert s.github_enable_write_tools is True
    assert s.github_max_retries == 5
    assert s.gitmcp_log_level == "DEBUG"


def test_settings_reject_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REQUEST_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
