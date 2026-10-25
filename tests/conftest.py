"""Project test configuration.

The retired Streamlit UI tests were removed with the legacy UI; Qt Quick is the
only supported desktop entry.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_provider_db(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Keep model settings out of the user's real provider database.

    Every ``GraphOperations`` builds a ``ProviderStore`` from the environment, so
    without this the suite would write hundreds of throwaway scopes into
    ``~/.narrative/provider-settings.sqlite3``.
    """
    path = str(tmp_path_factory.mktemp("provider") / "provider-settings.sqlite3")
    previous = os.environ.get("NARRATIVE_PROVIDER_DB")
    os.environ["NARRATIVE_PROVIDER_DB"] = path
    yield path
    if previous is None:
        os.environ.pop("NARRATIVE_PROVIDER_DB", None)
    else:
        os.environ["NARRATIVE_PROVIDER_DB"] = previous
