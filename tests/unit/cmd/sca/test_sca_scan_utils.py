from datetime import datetime, timedelta

from ggshield.cmd.sca.scan.sca_scan_utils import get_scan_params_from_config
from ggshield.core.config.user_config import SCAConfig, SCAIgnoredVulnerability
from ggshield.verticals.sca.sca_scan_models import SCAScanParameters


def test_get_scan_params_from_config():
    """
    GIVEN an SCAConfig with some ignored vulnerabilities
    WHEN requesting the scan parameters from this config
    THEN we get the minimum severity
    THEN we get only the vuln that are still ignored
    """

    config = SCAConfig(
        minimum_severity="high",
        ignored_vulnerabilities=[
            # Not ignored anymore
            SCAIgnoredVulnerability(
                identifier="GHSA-toto-1234",
                path="Pipfile.lock",
                until=datetime(year=1970, month=1, day=1),
            ),
            # Ignored ones
            SCAIgnoredVulnerability(
                identifier="GHSA-4567-8765",
                path="toto/Pipfile.lock",
                until=datetime.utcnow() + timedelta(days=1),
            ),
            SCAIgnoredVulnerability(
                identifier="GHSA-4567-other",
                path="toto/Pipfile.lock",
            ),
        ],
    )

    params = get_scan_params_from_config(config)

    assert isinstance(params, SCAScanParameters)
    assert params.minimum_severity == "high"

    assert len(params.ignored_vulnerabilities) == 2
    assert {"GHSA-4567-8765", "GHSA-4567-other"} == set(
        ignored.identifier for ignored in params.ignored_vulnerabilities
    )
    for ignored in params.ignored_vulnerabilities:
        assert ignored.path == "toto/Pipfile.lock"
