import json

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Keep tests independent of a developer's private settings.json."""
    from config import runtime

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"auth": {"admin_user": "zym", "admin_password": "test-password"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "CONFIG_PATH", settings_path)
