import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
BASE_TEMPLATE = (ROOT / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8")


class NumericInputWheelGuardTests(unittest.TestCase):
    def test_focused_numeric_input_is_blurred_before_wheel_default(self):
        wheel_guard = BASE_TEMPLATE.split('document.addEventListener("submit"', 1)[0]
        self.assertIn('document.addEventListener("wheel"', BASE_TEMPLATE)
        self.assertIn("input[type=\"number\"]", wheel_guard)
        self.assertIn("document.activeElement === input", wheel_guard)
        self.assertIn("input.blur()", wheel_guard)
        self.assertIn("capture: true, passive: true", wheel_guard)
        self.assertNotIn("preventDefault()", wheel_guard)


if __name__ == "__main__":
    unittest.main()
