"""Block new regressions; document unchanged failures reproduced on the hotfix."""
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
def cases(path):
    tests = ET.parse(path).getroot().findall(".//testcase")
    result = {}
    for test in tests:
        name = test.attrib["classname"] + "." + test.attrib["name"]
        if name in result:
            raise RuntimeError(f"Duplicate test result: {name}")
        if test.find("skipped") is not None or test.get("status") != "run" or test.get("result") != "completed":
            raise RuntimeError(f"Test did not execute completely: {name}")
        result[name] = test
    return result
def failed(tests):
    return {name for name, test in tests.items() if test.find("failure") is not None or test.find("error") is not None}
def compare(candidate_path, baseline_path):
    known = json.loads((ROOT / "ci/hotfix-known-regressions.json").read_text())
    required = json.loads((ROOT / "ci/protocol-regressions.json").read_text())
    if len(required) != len(set(required)):
        raise RuntimeError("Duplicate required protocol test")
    candidate, baseline = cases(candidate_path), cases(baseline_path)
    subprocess.run(["git", "diff", "--exit-code", known["base"], "HEAD", "--", *known["unchanged_sources"]], cwd=ROOT, check=True)
    # Keep every baseline test and pairing fixture, plus the exact reviewed
    # protocol inventory. Equal totals alone cannot detect substituted tests.
    expected_candidate = 202 + 9 + len(required)
    if len(baseline) != 202 or len(candidate) != expected_candidate:
        raise RuntimeError(f"Unexpected headless test inventory: {len(baseline)} baseline tests (expected 202), {len(candidate)} candidate tests (expected {expected_candidate})")
    additions = {name for name in candidate if name.startswith(("RtspSecurity.", "PairingHttp.", "LaunchProtocol.", "StreamControlSecurity."))}
    if additions != set(required):
        raise RuntimeError("The complete reviewed protocol/control inventory must execute")
    repaired_fixtures = {name for name in candidate if name.startswith("PairingTest.") or "/PairingTest." in name}
    if len(repaired_fixtures) != 9 or any(candidate[name].find("skipped") is not None for name in repaired_fixtures):
        raise RuntimeError("All nine existing pairing helper tests must execute")
    if set(candidate) - additions - repaired_fixtures != set(baseline):
        raise RuntimeError("Existing baseline and candidate test inventories differ")
    if failed(candidate) != failed(baseline) or failed(baseline) != set(known["failures"]):
        raise RuntimeError("New or changed failures detected; compare the XML reports")
    passed = len(candidate) - len(failed(candidate))
    summary = f"{passed} candidate tests passed; {len(additions)} new checks passed; {len(failed(candidate))} inherited failures reproduced unchanged on the hotfix."
    print(summary)
    (ROOT / "validation/regression-comparison.txt").write_text(summary + "\n")

if __name__ == "__main__":
    compare(sys.argv[1], sys.argv[2])
