"""Guard the packaging metadata against shipping a broken wheel.

A wheel that installs cleanly can still be missing a module the code imports
at runtime. That failure is invisible in the source tree — it only appears
after `pip install` — so the declaration itself is what needs testing.
"""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def read_pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def declared_py_modules() -> list[str]:
    text = read_pyproject()
    block = re.search(r"py-modules\s*=\s*\[(.*?)\]", text, re.S)
    if not block:
        return []
    return re.findall(r'"([^"]+)"', block.group(1))


class PackagingTests(unittest.TestCase):
    def test_bridge_module_is_shipped(self):
        """`dsh_session` imports `dsh_bridge`; without it the wheel is broken.

        The import is wrapped in `except ImportError`, so a wheel missing the
        module does not crash — it silently degrades to env/plain-origin and
        the user never learns why the bridge stopped working.
        """
        self.assertIn("dsh_bridge", declared_py_modules())

    def test_every_declared_py_module_exists(self):
        for name in declared_py_modules():
            with self.subTest(module=name):
                self.assertTrue((ROOT / f"{name}.py").is_file(),
                                f"{name}.py is declared but missing")

    def test_local_imports_are_all_shipped(self):
        """Any `import <local module>` must resolve to a declared module."""
        declared = set(declared_py_modules())
        sources = [ROOT / f"{m}.py" for m in declared]
        sources += sorted((ROOT / "dshstudio").glob("*.py"))
        pattern = re.compile(r"^\s*(?:from|import)\s+(\w+)", re.M)
        for source in sources:
            for name in pattern.findall(source.read_text(encoding="utf-8")):
                root = name.split(".")[0]
                if root in sys.stdlib_module_names or root == "__future__":
                    continue
                with self.subTest(source=source.name, imports=root):
                    self.assertTrue(
                        root in declared or (ROOT / root).is_dir()
                        or (ROOT / f"{root}.py").is_file(),
                        f"{source.name} imports {root!r}, which the wheel does not ship")

    def test_declared_packages_exist(self):
        text = read_pyproject()
        block = re.search(r"packages\s*=\s*\[(.*?)\]", text, re.S)
        self.assertIsNotNone(block, "pyproject declares no packages")
        for name in re.findall(r'"([^"]+)"', block.group(1)):
            with self.subTest(package=name):
                self.assertTrue((ROOT / name.replace(".", "/")).is_dir())

    def test_version_matches_the_changelog(self):
        text = read_pyproject()
        version = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"[{version}]", changelog,
                      f"pyproject says {version} but the CHANGELOG has no entry")

    def test_declared_package_data_exists(self):
        text = read_pyproject()
        # The block runs to the next section header or end of file; it is not
        # brace-delimited, so anchor on the section line.
        block = re.search(r"\[tool\.setuptools\.package-data\]\s*\n(.*?)(?=\n\[|\Z)",
                          text, re.S)
        self.assertIsNotNone(block, "package-data section not found")
        entries = re.findall(r"^(\w+)\s*=\s*\[(.*?)\]", block.group(1), re.S)
        for pkg, patterns in entries:
            for pattern in re.findall(r'"([^"]+)"', patterns):
                with self.subTest(package=pkg, data=pattern):
                    self.assertTrue(
                        (ROOT / pkg.replace(".", "/") / pattern).exists(),
                        f"package-data names {pkg}/{pattern}, which does not exist")

    def test_readme_declared_as_long_description_exists(self):
        text = read_pyproject()
        block = re.search(r'^readme\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(block)
        self.assertTrue((ROOT / block.group(1)).is_file())

    def test_license_file_exists(self):
        text = read_pyproject()
        block = re.search(r'license\s*=\s*\{\s*file\s*=\s*"([^"]+)"', text)
        if block:
            self.assertTrue((ROOT / block.group(1)).is_file())

    def test_console_scripts_resolve(self):
        """Each `module:function` entry point must actually exist."""
        text = read_pyproject()
        scripts = re.search(r"\[project\.scripts\]\n((?:.*\n)*?)\n\[", text)
        self.assertIsNotNone(scripts)
        for line in scripts.group(1).strip().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            name, target = line.split("=", 1)
            module, _, func = target.strip().strip('"').partition(":")
            with self.subTest(script=name.strip()):
                # `module` may be dotted (`dshstudio.cli`), so map it to a path.
                parts = module.split(".")
                path = ROOT.joinpath(*parts[:-1], f"{parts[-1]}.py")
                self.assertTrue(path.is_file(), f"{module}.py missing")
                self.assertIn(f"def {func}", path.read_text(encoding="utf-8"),
                              f"{name} points at {module}:{func}, which is not defined")


if __name__ == "__main__":
    unittest.main()
