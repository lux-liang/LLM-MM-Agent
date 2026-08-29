import ast
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


def _load_executor():
    source_path = Path(__file__).parents[3] / "MMAgent" / "agent" / "task_solving.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    wanted = {
        "EnvException",
        "_drain_capped_output",
        "_create_windows_job",
        "_terminate_process_tree",
        "execute_script",
    }
    body = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted
    ]
    namespace = {
        "os": os,
        "signal": signal,
        "subprocess": subprocess,
        "sys": sys,
        "threading": threading,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["execute_script"], namespace["EnvException"]


execute_script, EnvException = _load_executor()


class GeneratedExecutorTests(unittest.TestCase):
    def test_success_and_bounded_output_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "run.py"
            script.write_text("print('x' * 100000)\nprint('TAIL')\n", encoding="utf-8")
            output = execute_script(script.name, directory, timeout=5)
        self.assertIn("Status: success. Return code: 0", output)
        self.assertIn("TAIL", output)
        self.assertLess(len(output), 70000)

    def test_timeout_kills_descendant_process(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "escaped.txt"
            child_code = (
                "import time; from pathlib import Path; "
                f"time.sleep(2); Path({str(marker)!r}).write_text('escaped')"
            )
            script = Path(directory) / "parent.py"
            script.write_text(
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(EnvException, "timed out"):
                execute_script(script.name, directory, timeout=1)
            time.sleep(2.2)
            self.assertFalse(marker.exists())

    def test_resolved_script_must_stay_inside_work_directory(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            external_script = Path(outside) / "escape.py"
            external_script.write_text("print('escaped')\n", encoding="utf-8")
            link = Path(workspace) / "linked"
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(link), str(Path(outside))],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    link.symlink_to(outside, target_is_directory=True)
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("this host cannot create a link/junction")
            with self.assertRaisesRegex(EnvException, "inside work_dir"):
                execute_script(str(Path("linked") / "escape.py"), workspace, timeout=5)


if __name__ == "__main__":
    unittest.main()
