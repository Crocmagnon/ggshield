import json
import os
from unittest.mock import patch

import jsonschema
import pytest

from tests.conftest import GG_VALID_TOKEN
from tests.functional.utils import recreate_censored_content, run_ggshield_scan
from tests.repository import Repository


LEAK_CONTENT = f"password = {GG_VALID_TOKEN}"


@pytest.fixture(scope="module")
def leaky_repo(tmp_path_factory: pytest.TempPathFactory) -> Repository:
    """
    Create a repository, add a commit with a secret in it, then add 2 clean commits on
    top of it
    """
    repo = Repository.create(tmp_path_factory.mktemp("test_scan_repo"))

    secret_file = repo.path / "secret.conf"
    secret_file.write_text(LEAK_CONTENT)
    repo.add("secret.conf")
    repo.create_commit()

    for _ in range(2):
        repo.create_commit()

    return repo


def test_scan_repo(leaky_repo: Repository) -> None:
    # GIVEN a repository with a past commit containing a leak
    # WHEN scanning the repo
    # THEN the leak is found
    proc = run_ggshield_scan(
        "repo", str(leaky_repo.path), expected_code=1, cwd=leaky_repo.path
    )

    # AND the output contains the line of the leak
    assert recreate_censored_content(LEAK_CONTENT, GG_VALID_TOKEN) in proc.stdout


def test_scan_repo_json(leaky_repo: Repository, secret_json_schema) -> None:
    # GIVEN a repository with a past commit containing a leak
    # WHEN scanning the repo
    # THEN the leak is found
    proc = run_ggshield_scan(
        "repo", "--json", str(leaky_repo.path), expected_code=1, cwd=leaky_repo.path
    )
    # AND the JSON output matches the expected format
    dct = json.loads(proc.stdout)
    jsonschema.validate(dct, secret_json_schema)


def test_scan_repo_quota_limit_reached(
    leaky_repo: Repository, no_quota_gitguardian_api: str, caplog
) -> None:
    # GIVEN a repository with a past commit containing a leak

    # WHEN scanning the repo
    # THEN error code is 128
    with patch.dict(
        os.environ, {**os.environ, "GITGUARDIAN_API_URL": no_quota_gitguardian_api}
    ):
        proc = run_ggshield_scan(
            "repo",
            str(leaky_repo.path),
            "--json",
            expected_code=128,
            cwd=leaky_repo.path,
        )

    # AND stderr contains an error message
    assert (
        "Error: Could not perform the requested action: no more API calls available."
        in proc.stderr
    )
    # AND stdout is empty
    assert proc.stdout.strip() == ""


def test_scan_repo_exclude_patterns(
    leaky_repo: Repository,
) -> None:
    # GIVEN a repository with a past commit containing a leak
    # AND a pattern to exclude the leak
    # WHEN scanning the repo
    # THEN the leak is not found

    result = run_ggshield_scan(
        "repo",
        "--exclude",
        "secret.conf",
        str(leaky_repo.path),
        expected_code=0,
        cwd=leaky_repo.path,
    )

    assert "No secrets have been found" in result.stdout


@pytest.mark.parametrize("ignore_removed_secrets", [True, False])
@pytest.mark.parametrize("file_deleted", [True, False])
def test_scan_repo_ignore_removed_secrets(
    tmp_path_factory: pytest.TempPathFactory,
    ignore_removed_secrets: bool,
    file_deleted: bool,
) -> None:
    # GIVEN a repository with a past commits adding and removing a leak
    # WHEN scanning the repo
    # THEN only the commit adding the leak raises an incident

    repo = Repository.create(
        tmp_path_factory.mktemp("test_scan_repo_ignore_removed_secrets")
    )

    secret_file = repo.path / "secret.conf"
    secret_file.write_text(LEAK_CONTENT)
    repo.add("secret.conf")
    add_sha = repo.create_commit()

    for _ in range(2):
        repo.create_commit()

    if file_deleted:
        os.remove(secret_file)
    else:
        secret_file.write_text("removed")
    repo.add("secret.conf")
    remove_sha = repo.create_commit()

    params = ["repo", "--json", str(repo.path)]
    if ignore_removed_secrets:
        params.append("--ignore-removed-secrets")

    result = run_ggshield_scan(
        *params,
        expected_code=1,
        cwd=repo.path,
    )
    result_json = json.loads(result.stdout)

    assert result_json["total_incidents"] == (1 if ignore_removed_secrets else 2)
    assert result_json["total_occurrences"] == (1 if ignore_removed_secrets else 2)

    leaky_sha = {
        scan["id"] for scan in result_json["scans"] if scan["total_incidents"] > 0
    }
    assert add_sha in leaky_sha
    assert (remove_sha in leaky_sha) is (not ignore_removed_secrets)
