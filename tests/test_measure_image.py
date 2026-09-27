from ai_tools.measure_image import count_vulns


def test_count_vulns_by_severity_and_fixable():
    report = {
        "Results": [
            {"Vulnerabilities": [
                {"Severity": "CRITICAL", "FixedVersion": "1.2"},
                {"Severity": "HIGH"},
                {"Severity": "HIGH", "FixedVersion": "2.0"},
                {"Severity": "WEIRD"},
            ]},
            {"Vulnerabilities": None},
            {},
        ]
    }
    counts = count_vulns(report)
    assert counts["total"] == {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 1}
    assert counts["fixable"] == {"CRITICAL": 1, "HIGH": 1, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}


def test_count_vulns_empty_report():
    assert count_vulns({})["total"]["CRITICAL"] == 0
