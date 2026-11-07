"""Copy the canonical frozen schemas into distributions without editing their bytes."""
from pathlib import Path
from shutil import copyfile

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

APP = Path(__file__).resolve().parent
CANONICAL = APP.parents[1] / "packages/bridge-protocol/schemas"


def copy_schemas(destination):
    # An unpacked sdist already carries the exact resources; no checkout needed.
    source = CANONICAL if CANONICAL.is_dir() else APP / "benchlink_daemon/schemas"
    schemas = sorted(source.glob("*.schema.json"))
    if not schemas:
        raise RuntimeError("frozen bridge schemas missing from checkout or source distribution")
    destination.mkdir(parents=True, exist_ok=True)
    for schema in schemas:
        copyfile(schema, destination / schema.name)


class BuildPy(build_py):
    def run(self):
        super().run()
        copy_schemas(Path(self.build_lib) / "benchlink_daemon/schemas")


class Sdist(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        copy_schemas(Path(base_dir) / "benchlink_daemon/schemas")
