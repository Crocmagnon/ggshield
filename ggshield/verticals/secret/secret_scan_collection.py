from dataclasses import dataclass, field
from os import PathLike
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from pygitguardian.models import ScanResult

from ggshield.core.errors import UnexpectedError
from ggshield.core.filter import get_ignore_sha, leak_dictionary_by_ignore_sha
from ggshield.core.lines import Line, get_lines_from_content
from ggshield.core.scan.scannable import Scannable
from ggshield.utils.git_shell import Filemode
from ggshield.verticals.secret.extended_match import ExtendedMatch


class Result:
    """
    Return model for a scan which zips the information
    between the Scan result and its input file.
    """

    # TODO: Rename `file` to `scannable`?
    file: Scannable  # filename that was scanned
    scan: ScanResult  # Result of content scan
    sha_policy_break_to_extended_matches: Dict[str, List[ExtendedMatch]] = field(
        default_factory=dict
    )
    sha_lines_to_display: Dict[str, List[Line]] = field(default_factory=dict)

    def __init__(self, file: Scannable, scan: ScanResult):
        self.file = file
        self.scan = scan
        lines = get_lines_from_content(file.content, file.filemode)
        if len(lines) == 0:
            raise UnexpectedError("Parsing of scan result failed.")
        is_patch = file.filemode != Filemode.FILE
        self.sha_policy_break_to_extended_matches = {}
        self.sha_lines_to_display = {}
        for policy_break in self.scan.policy_breaks:
            sha = get_ignore_sha(policy_break)
            self.sha_policy_break_to_extended_matches[sha] = [
                ExtendedMatch.from_match(match, lines, is_patch)
                for match in policy_break.matches
            ]
            set_lines = set()
            for extended_match in self.sha_policy_break_to_extended_matches[sha]:
                for line in extended_match.context_lines:
                    set_lines.add(line)
            self.sha_lines_to_display[sha] = sorted(
                list(set_lines), key=lambda li: li.number
            )

    def __eq__(self, other: "Result") -> bool:
        return self.file == other.file and self.scan == other.scan

    def censor(self) -> None:
        for list_extended_matches in self.sha_policy_break_to_extended_matches.values():
            for extended_match in list_extended_matches:
                extended_match.censor()

    @property
    def filename(self) -> str:
        return self.file.filename

    @property
    def filemode(self) -> Filemode:
        return self.file.filemode

    @property
    def content(self) -> str:
        return self.file.content

    @property
    def has_policy_breaks(self) -> bool:
        return self.scan.has_policy_breaks


class Error(NamedTuple):
    files: List[Tuple[str, Filemode]]
    description: str  # Description of the error


@dataclass
class Results:
    """
    Return model for a scan with the results and errors of the scan

    Not a NamedTuple like the others because it causes mypy 0.961 to crash on the
    `from_exception()` method (!)

    Similar crash: https://github.com/python/mypy/issues/12629
    """

    results: List[Result] = field(default_factory=list)
    errors: List[Error] = field(default_factory=list)

    @staticmethod
    def from_exception(exc: Exception) -> "Results":
        """Create a Results representing a failure"""
        error = Error(files=[], description=str(exc))
        return Results(results=[], errors=[error])

    def extend(self, others: "Results") -> None:
        self.results.extend(others.results)
        self.errors.extend(others.errors)

    @property
    def has_policy_breaks(self) -> bool:
        return any(x.has_policy_breaks for x in self.results)


class SecretScanCollection:
    id: str
    type: str
    results: Optional[Results] = None
    scans: Optional[List["SecretScanCollection"]] = None
    optional_header: Optional[str] = None  # To be printed in Text Output
    extra_info: Optional[Dict[str, str]] = None  # To be included in JSON Output

    def __init__(
        self,
        id: Union[str, PathLike],
        type: str,
        results: Optional[Results] = None,
        scans: Optional[List["SecretScanCollection"]] = None,
        optional_header: Optional[str] = None,
        extra_info: Optional[Dict[str, str]] = None,
    ):
        self.id = str(id)
        self.type = type
        self.results = results
        self.scans = scans
        self.optional_header = optional_header
        self.extra_info = extra_info

        (
            self.known_secrets_count,
            self.new_secrets_count,
        ) = self._get_known_new_secrets_count()

    @property
    def has_new_secrets(self) -> bool:
        return self.new_secrets_count > 0

    @property
    def has_secrets(self) -> bool:
        return (self.new_secrets_count + self.known_secrets_count) > 0

    @property
    def scans_with_results(self) -> List["SecretScanCollection"]:
        if self.scans:
            return [scan for scan in self.scans if scan.results]
        return []

    @property
    def has_results(self) -> bool:
        return bool(self.results and self.results.results)

    def _get_known_new_secrets_count(self) -> Tuple[int, int]:
        policy_breaks = []
        for result in self.get_all_results():
            for policy_break in result.scan.policy_breaks:
                policy_breaks.append(policy_break)

        known_secrets_count = 0
        new_secrets_count = 0
        sha_dict = leak_dictionary_by_ignore_sha(policy_breaks)

        for ignore_sha, policy_breaks in sha_dict.items():
            if policy_breaks[0].known_secret:
                known_secrets_count += 1
            else:
                new_secrets_count += 1

        return known_secrets_count, new_secrets_count

    def get_all_results(self) -> Iterable[Result]:
        """Returns an iterable on all results and sub-scan results"""
        if self.results:
            yield from self.results.results
        if self.scans:
            for scan in self.scans:
                if scan.results:
                    yield from scan.results.results
