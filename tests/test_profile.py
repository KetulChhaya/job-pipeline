import json

import pytest

from filters import load_profile


def test_real_profile_loads(prof):
    assert prof["max_yoe"] == 5
    assert prof["per_company_per_day"] == 3
    assert prof["needs_sponsorship"] is True
    assert "family_keep" in prof["_re"]


def test_missing_key_is_reported(tmp_path, prof):
    raw = {k: v for k, v in prof.items() if k not in ("_re", "weights")}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="missing keys: weights"):
        load_profile(str(p))


def test_bad_regex_is_reported(tmp_path, prof):
    raw = {k: v for k, v in prof.items() if k != "_re"}
    raw["level"] = {"drop": ["staff("], "senior": ["senior"]}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="bad pattern"):
        load_profile(str(p))


def test_invalid_json_is_reported(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="cannot read profile"):
        load_profile(str(p))
