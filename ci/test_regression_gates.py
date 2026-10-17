"""Regression tests for the XML release gate, independent of the C++ build."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("compare", Path(__file__).with_name("compare-regressions.py"))
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class RegressionGateTests(unittest.TestCase):
    def read_cases(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.xml"
            path.write_text("<testsuites><testsuite>" + content + "</testsuite></testsuites>")
            return GATE.cases(path)

    def test_completed_cases_preserve_real_failures(self):
        cases = self.read_cases('<testcase classname="Inherited" name="Pass" status="run" result="completed"/>'
                                '<testcase classname="Inherited" name="Fail" status="run" result="completed"><failure/></testcase>')
        self.assertEqual(GATE.failed(cases), {"Inherited.Fail"})

    def test_skipped_inherited_case_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "did not execute"):
            self.read_cases('<testcase classname="Inherited" name="Pass" status="run" result="skipped"><skipped/></testcase>')

    def test_disabled_inherited_case_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "did not execute"):
            self.read_cases('<testcase classname="Inherited" name="Pass" status="notrun" result="suppressed"/>')

    def test_duplicate_case_cannot_hide_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            self.read_cases('<testcase classname="Inherited" name="Pass" status="run" result="completed"><failure/></testcase>'
                            '<testcase classname="Inherited" name="Pass" status="run" result="completed"/>')


if __name__ == "__main__":
    unittest.main()
