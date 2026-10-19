import pytest


@pytest.fixture(autouse=True)
def stable_code_revision(monkeypatch):
    """Manifests embed `git rev-parse HEAD`; a commit made while the suite runs must not change them.

    A test that checks the real lookup can call monkeypatch.undo() or import the function from a subprocess.
    """
    monkeypatch.setattr("tuning_factory.manifest.code_revision", lambda: "0" * 40)
