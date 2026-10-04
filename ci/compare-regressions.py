"""Block new regressions; document unchanged failures reproduced on the hotfix."""
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
def cases(path):
    tests = ET.parse(path).getroot().findall(".//testcase")
    return {t.attrib["classname"] + "." + t.attrib["name"]: t for t in tests}
def failed(tests):
    return {name for name, test in tests.items() if test.find("failure") is not None or test.find("error") is not None}
def compare(candidate_path, baseline_path):
    known = json.loads((ROOT / "ci/hotfix-known-regressions.json").read_text())
    candidate, baseline = cases(candidate_path), cases(baseline_path)
    subprocess.run(["git", "diff", "--exit-code", known["base"], "HEAD", "--", *known["unchanged_sources"]], cwd=ROOT, check=True)
    if len(baseline) != 202 or len(candidate) != 242:
        raise RuntimeError("Unexpected headless test inventory; baseline must contain 202 unchanged tests")
    additions = {name for name in candidate if name.startswith(("RtspSecurity.", "PairingHttp.", "LaunchProtocol.", "StreamControlSecurity."))}
    if len(additions) != 31 or any(candidate[name].find("skipped") is not None for name in additions):
        raise RuntimeError("Every new protocol/control regression must execute")
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
