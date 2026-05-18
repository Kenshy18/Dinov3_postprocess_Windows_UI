from __future__ import annotations

import base64
import os
import unittest

from dinov3_windows_frontend.path_utils import clean_run_part, windows_path_to_wsl, wsl_path_to_unc
from dinov3_windows_frontend.process_utils import hidden_windows_process_command
from dinov3_windows_frontend.progress import parse_progress_line
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

    def test_wsl_localhost_unc_path_to_wsl(self) -> None:
        self.assertEqual(
            windows_path_to_wsl(r"\\wsl.localhost\Ubuntu\home\kenke\Dinov3_postprocess\input\a.mp4"),
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
    def decoded_bash_script(self, command: list[str]) -> str:
        marker = "printf %s "
        payload = command[-1].split(marker, 1)[1].split(" | base64 -d | bash", 1)[0]
        return base64.b64decode(payload.encode("ascii")).decode("utf-8")

    def test_job_command_sources_runtime_env_and_preserves_args(self) -> None:
        bridge = WslBridge("Ubuntu", "/home/kenke/Dinov3_postprocess")
        command = bridge.job_command(["/venv/bin/python", "script.py", "--input", "/mnt/c/a b.mp4"])
        self.assertEqual(command[:7], ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/kenke/Dinov3_postprocess", "--", "bash"])
        decoded = self.decoded_bash_script(command)
        self.assertIn("source .runtime/gui_runtime.env", decoded)
        self.assertIn("script.py", decoded)
        self.assertIn("'/mnt/c/a b.mp4'", decoded)

    def test_job_script_command_sources_runtime_env(self) -> None:
        bridge = WslBridge("Ubuntu", "/home/kenke/Dinov3_postprocess")
        command = bridge.job_script_command("echo ok")
        self.assertEqual(command[:7], ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/kenke/Dinov3_postprocess", "--", "bash"])
        decoded = self.decoded_bash_script(command)
        self.assertIn("source .runtime/gui_runtime.env", decoded)
        self.assertIn("echo ok", decoded)


class ProgressTests(unittest.TestCase):
    def test_parse_phase_progress_quoted_value(self) -> None:
        self.assertEqual(
            parse_progress_line("[phase-progress] phase=raw_sqlite detail='a b' percent=12.5"),
            {"phase": "raw_sqlite", "detail": "a b", "percent": "12.5"},
        )

    def test_parse_batch_progress_label(self) -> None:
        self.assertEqual(
            parse_progress_line("[batch-progress] inference: current=2 total=10 unit=frames"),
            {"current": "2", "total": "10", "unit": "frames", "phase": "inference"},
        )


class ProcessCommandTests(unittest.TestCase):
    def test_wsl_command_is_wrapped_hidden_on_windows(self) -> None:
        command = hidden_windows_process_command(["wsl.exe", "-l", "-q"])
        if os.name == "nt":
            self.assertEqual(command[0], "powershell.exe")
            self.assertIn("-WindowStyle", command)
            self.assertIn("Hidden", command)
            self.assertIn("-EncodedCommand", command)
        else:
            self.assertEqual(command, ["wsl.exe", "-l", "-q"])


if __name__ == "__main__":
    unittest.main()
