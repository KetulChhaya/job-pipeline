import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from filters import load_profile  # noqa: E402


@pytest.fixture(scope="session")
def prof():
    return load_profile(os.path.join(ROOT, "config", "profile.json"))
