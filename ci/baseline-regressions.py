"""Compile the SAME regression harness against the immutable pre-fix handlers."""
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = "8d789be463ef72cadb1ba16e202896b11ea7664b"
BACKUP = ROOT / "build-tests" / "baseline-backup"

def source(path):
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT).decode("utf-8")

def block(text, marker):
    match = re.search(r"#ifdef SUNSHINE_TESTS // " + marker + r"\n.*?#endif // " + marker, text, re.S)
    if not match:
        raise RuntimeError(f"Missing harness block: {marker}")
    return match.group()

def apply():
    BACKUP.mkdir(parents=True, exist_ok=False)
    for name in ("rtsp.cpp", "nvhttp.cpp"):
        path = ROOT / "src" / name
        fixed_bytes = path.read_bytes()
        fixed = fixed_bytes.decode("utf-8").replace("\r\n", "\n")
        (BACKUP / name).write_bytes(fixed_bytes)
        old = source(f"src/{name}")
        if name == "rtsp.cpp":
            methods = block(fixed, "Security regression harness methods")
            old = old.replace("  private:\n    std::unordered_map<std::string_view, cmd_func_t>", methods + "\n  private:\n    std::unordered_map<std::string_view, cmd_func_t>")
            old = old.replace('#include "rtsp.h"', '#include "rtsp.h"\n#include "protocol_test.h"')
            old += "\n" + block(fixed, "Security regression harness")
        else:
            old = '#include <mutex>\n#include "protocol_test.h"\n' + old
            old += """
namespace nvhttp {
  nlohmann::json pending_pairings() { return nlohmann::json::array(); }
  bool pin(std::string, std::string, std::string) { return false; }
}
namespace nvhttp::test {
  std::recursive_mutex harness_mutex;
  void pair_http(std::shared_ptr<SimpleWeb::ServerBase<SimpleWeb::HTTP>::Response> response,
                 std::shared_ptr<SimpleWeb::ServerBase<SimpleWeb::HTTP>::Request> request) {
    std::lock_guard<std::recursive_mutex> lock {harness_mutex};
    pair<SimpleWeb::HTTP>(std::move(response), std::move(request));
  }
  std::size_t pending() {
    std::lock_guard<std::recursive_mutex> lock {harness_mutex};
    return map_id_sess.size();
  }
  std::size_t rate_sources() { return 0; }
  void expire() {}
  void reset() {
    std::lock_guard<std::recursive_mutex> lock {harness_mutex};
    map_id_sess.clear();
    client_root.named_devices.clear();
    one_time_pin.clear();
    otp_passphrase.clear();
    otp_device_name.clear();
  }
}
"""
        path.write_bytes(old.encode("utf-8"))

def restore():
    if not BACKUP.exists():
        return
    for name in ("rtsp.cpp", "nvhttp.cpp"):
        saved = BACKUP / name
        if saved.exists():
            (ROOT / "src" / name).write_bytes(saved.read_bytes())
            saved.unlink()
    BACKUP.rmdir()

def verify(path):
    expected = {
        "RtspSecurity.PlaintextLaunchCannotBeTakenOver",
        "RtspSecurity.InvalidHighSequenceDoesNotReserveCounter",
        "PairingHttp.InvalidOtpRetainsNoState",
        "PairingHttp.GlobalStateCap",
    }
    tests = ET.parse(path).getroot().findall(".//testcase")
    actual = {t.attrib["classname"] + "." + t.attrib["name"] for t in tests}
    if actual != expected or any(t.find("failure") is None for t in tests):
        raise RuntimeError("Baseline must execute and fail every selected security regression")
    print("All four regressions detect the vulnerable hotfix handlers.")

if __name__ == "__main__":
    if sys.argv[1] == "apply": apply()
    elif sys.argv[1] == "restore": restore()
    elif sys.argv[1] == "verify": verify(sys.argv[2])
    else: raise ValueError("Expected apply, restore or verify")
