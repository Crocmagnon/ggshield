import re
from pathlib import Path
from typing import List, Set

from pygitguardian.models import Detail

from ggshield.core.errors import APIKeyCheckError, UnexpectedError
from ggshield.core.git_shell import (
    INDEX_REF,
    get_filepaths_from_ref,
    get_staged_filepaths,
    tar_from_ref_and_filepaths,
)
from ggshield.core.text_utils import display_error
from ggshield.sca.client import ComputeSCAFilesResult, SCAClient
from ggshield.scan import Scannable
from ggshield.scan.file import get_files_from_paths


# List of filepaths to ignore for SCA scans

SCA_IGNORE_LIST = (
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "site-packages",
    ".idea",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".hypothesis",
)


def get_all_files_from_sca_paths(
    path: Path,
    exclusion_regexes: Set[re.Pattern],
    verbose: bool,
    ignore_git: bool = False,
) -> List[str]:
    """
    Create a Files object from a path, recursively, ignoring non SCA files

    :param path: path to scan
    :param exclusion_regexes: list of regexes, used to exclude some filepaths
    :param verbose: Option that displays filepaths as they are scanned
    :param ignore_git: Ignore that the folder is a git repository. If False, only files tracked by git are scanned
    """
    files = get_files_from_paths(
        paths=[str(path)],
        exclusion_regexes=exclusion_regexes,
        recursive=True,
        yes=True,
        verbose=verbose,
        ignore_git=ignore_git,
    ).apply_filter(is_not_excluded_from_sca)

    return [str(x.relative_to(path)) for x in files.paths]


def is_not_excluded_from_sca(scannable: Scannable) -> bool:
    """
    Returns True if file is in an SCA accepted path, which means that none of
    the directories of the path appear in SCA_IGNORE_LIST
    """
    return not any(part in SCA_IGNORE_LIST for part in scannable.path.parts)


def tar_sca_files_from_git_repo(directory: Path, ref: str, client: SCAClient) -> bytes:
    """Builds a tar containing SCA files from the git repository at
    the given directory, for the given ref. Empty string denotes selection
    from staging area."""
    # TODO: add exclusion patterns
    if ref == INDEX_REF:
        all_files = get_staged_filepaths(wd=str(directory))
    else:
        all_files = get_filepaths_from_ref(ref, wd=str(directory))

    sca_files_result = client.compute_sca_files(files=[str(path) for path in all_files])
    if isinstance(sca_files_result, Detail):
        raise UnexpectedError("Failed to select SCA files")

    return tar_from_ref_and_filepaths(
        ref=ref, filepaths=map(Path, sca_files_result.sca_files), wd=str(directory)
    )


def get_sca_scan_all_filepaths(
    directory: Path,
    exclusion_regexes: Set[re.Pattern],
    verbose: bool,
    client: SCAClient,
) -> List[str]:
    """
    Retrieve SCA related files of a directory.
    First get all filenames that are not in blacklisted directories, then calls SCA compute files
    API to filter SCA related files.
    """
    all_filepaths = get_all_files_from_sca_paths(
        path=directory,
        exclusion_regexes=exclusion_regexes,
        verbose=verbose,
        # If the repository is a git repository, ignore untracked files
        ignore_git=True,
    )

    # API Call to filter SCA files
    response = client.compute_sca_files(files=all_filepaths)

    if not isinstance(response, ComputeSCAFilesResult):
        if response.status_code == 401:
            raise APIKeyCheckError(client.base_uri, "Invalid API key.")
        display_error("Error while filtering SCA related files.")
        display_error(str(response))
        raise UnexpectedError("Unexpected error while filtering SCA related files.")

    # Only sca_files field is useful in the case of a full_scan,
    # all the potential files already exist in `all_filepaths`
    return response.sca_files
