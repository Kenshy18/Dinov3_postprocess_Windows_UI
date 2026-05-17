from __future__ import annotations

import unittest

from dinov3_windows_frontend.path_utils import clean_run_part, windows_path_to_wsl, wsl_path_to_unc
from dinov3_windows_frontend.wsl_bridge import WslBridge


class PathUtilsTests(unittest.TestCase):
    def test_windows_drive_path_to_wsl(self) -> None:
        self.assertEqual(
            windows_path_to_wsl(r"C:\Users\alice\Videos\a file.mp4"),
            "/mnt/c/Users/alice/Videos/a file.mp4",
        )

    def test_wsl_unc_path_to_wsl(self) -> None:
        self.assertEqual(
            windows_path_to_wsl(r"\\wsl$\Ubuntu\home\kenke\Dinov3_postprocess\input\a.mp4"),
            "/home/kenke/Dinov3_postprocess/input/a.mp4",
        )

    def test_wsl_path_to_unc(self) -> None:
        self.assertEqual(
            wsl_path_to_unc("Ubuntu", "/home/kenke/Dinov3_postprocess/output"),
            r"\\wsl$\Ubuntu\home\kenke\Dinov3_postprocess\output",
        )

    def test_clean_run_part(self) -> None:
        self.assertEqual(clean_run_part("foo bar/動画"), "foo_bar")


class WslBridgeTests(unittest.TestCase):
    def test_job_command_sources_runtime_env_and_preserves_args(self) -> None:
        bridge = WslBridge("Ubuntu", "/home/kenke/Dinov3_postprocess")
        command = bridge.job_command(["/venv/bin/python", "script.py", "--input", "/mnt/c/a b.mp4"])
        self.assertEqual(command[:7], ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/kenke/Dinov3_postprocess", "--", "bash"])
        self.assertIn("source .runtime/gui_runtime.env", command[-1])
        self.assertIn("script.py", command[-1])
        self.assertIn("'/mnt/c/a b.mp4'", command[-1])


if __name__ == "__main__":
    unittest.main()
