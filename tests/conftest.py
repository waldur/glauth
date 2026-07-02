"""Test fixtures for the GLAuth config refresher.

The refresher is a hyphenated, non-importable script that pulls in the ``stomp``
and ``waldur_api_client`` client stacks at module load. Those are runtime-only
dependencies, irrelevant to the pure config helpers under test, so we register a
lightweight import stub for them and load the script by path. This keeps the
test job free of the full client dependency tree.
"""

import importlib.abc
import importlib.machinery
import importlib.util
import pathlib
import sys
import types

import pytest

# Top-level packages faked by the stub finder below.
_STUB_ROOTS = ("stomp", "waldur_api_client")


class _StubModule(types.ModuleType):
    """Module whose every attribute is a fresh, reusable dummy class.

    Dummy attributes are real classes so they work as base classes — the script
    subclasses ``stomp.ConnectionListener`` at import time.
    """

    def __getattr__(self, name):
        stub = type(name, (), {})
        setattr(self, name, stub)
        return stub


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve any ``stomp`` / ``waldur_api_client`` (sub)module to a stub."""

    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in _STUB_ROOTS:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _StubFinder())

REFRESHER_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "refresher"
    / "refresh-glauth-config.py"
)


@pytest.fixture(scope="session")
def refresher():
    """Load the refresher script by path, with its network deps stubbed."""
    spec = importlib.util.spec_from_file_location(
        "refresh_glauth_config", REFRESHER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
