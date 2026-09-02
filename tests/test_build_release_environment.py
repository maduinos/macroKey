import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BuildReleaseEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_dir = Path(self.temp_dir.name) / "project"
        self.project_dir.mkdir()

        script = self.project_dir / "build_release.sh"
        shutil.copy2(REPOSITORY_ROOT / "build_release.sh", script)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.project_dir / "main.py").write_text("print('unused')\n", encoding="utf-8")
        (self.project_dir / "requirements.txt").write_text(
            "pyinstaller>=6\n", encoding="utf-8"
        )

        self.fake_bin = Path(self.temp_dir.name) / "bin"
        self.fake_bin.mkdir()
        self.python_log = Path(self.temp_dir.name) / "python.log"
        fake_python = self.fake_bin / "python3"
        fake_python.write_text(
            textwrap.dedent(
                r"""\
                #!/usr/bin/env bash
                set -euo pipefail
                printf '%s | PYTHONPATH=%s\n' "$*" "${PYTHONPATH:-}" >> "$FAKE_PYTHON_LOG"

                version="${FAKE_PYTHON_VERSION:-3.12.3}"
                if [[ "${1:-}" == "--version" ]]; then
                  printf 'Python %s\n' "$version"
                  exit 0
                fi

                if [[ "${1:-}" == "-c" ]]; then
                  code="${2:-}"
                  if [[ "$code" == *"sys.version_info >="* ]]; then
                    [[ "${FAKE_VERSION_SUPPORTED:-1}" == "1" ]]
                    exit
                  fi
                  if [[ "$code" == *"sys.version_info.major"* ]]; then
                    printf '%s\n' "${version%.*}"
                    exit 0
                  fi
                  if [[ "$code" == *"os.pathsep"* ]]; then
                    printf ':\n'
                    exit 0
                  fi
                  printf '1 MB\n'
                  exit 0
                fi

                if [[ "${1:-}" == "-" ]]; then
                  exit 0
                fi

                if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
                  exit 0
                fi

                if [[ "${1:-}" == "-m" && "${2:-}" == "PyInstaller" ]]; then
                  if [[ "${3:-}" == "--version" ]]; then
                    printf '6.0\n'
                    exit 0
                  fi
                  distpath=""
                  app_name=""
                  shift 2
                  while [[ $# -gt 0 ]]; do
                    case "$1" in
                      --distpath) distpath="$2"; shift 2 ;;
                      --name) app_name="$2"; shift 2 ;;
                      *) shift ;;
                    esac
                  done
                  mkdir -p "$distpath"
                  : > "$distpath/$app_name"
                  exit 0
                fi

                printf 'unexpected fake python invocation: %s\n' "$*" >&2
                exit 2
                """
            ),
            encoding="utf-8",
        )
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

        self.xcb_library = Path(self.temp_dir.name) / "libxcb-cursor.so.0"
        self.xcb_library.touch()

    def run_build(self, *, version="3.12.3", supported=True):
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "FAKE_PYTHON_LOG": str(self.python_log),
                "FAKE_PYTHON_VERSION": version,
                "FAKE_VERSION_SUPPORTED": "1" if supported else "0",
                "XCB_CURSOR_LIB": str(self.xcb_library),
            }
        )
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            [str(self.project_dir / "build_release.sh"), "--skip-tests"],
            cwd=self.project_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_system_python_uses_project_local_dependencies(self):
        result = self.run_build()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        dependency_dir = self.project_dir / ".build-deps" / "python3.12"
        invocations = self.python_log.read_text(encoding="utf-8")
        self.assertIn(f"--target {dependency_dir}", invocations)
        self.assertIn(f"PYTHONPATH={dependency_dir}", invocations)
        self.assertTrue((self.project_dir / "releases" / "linux" / "macrokey").is_file())

    def test_python_older_than_3_10_is_rejected_before_install(self):
        result = self.run_build(version="3.9.18", supported=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.10", result.stdout + result.stderr)
        invocations = self.python_log.read_text(encoding="utf-8")
        self.assertNotIn("-m pip", invocations)


if __name__ == "__main__":
    unittest.main()
