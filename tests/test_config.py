"""Configuration errors must stay safe across both real CLI entrypoints."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from adrobot.config import Settings

SECRET = "SYNTHETIC-CREDENTIAL-SENTINEL"


@pytest.mark.parametrize(
    "url",
    [
        f"https://user:{SECRET}@tracker.example",
        f"https://tracker.example:{SECRET}",
        "https://tracker.example:99999",
        "https://tracker.example:0",
        f"https://[{SECRET}",
        f"http://{SECRET}.example",
        f"https://tracker.example/{SECRET}",
    ],
)
def test_invalid_origin_has_safe_validation_message(url) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(keitaro_url=url, _env_file=None)
    assert "clean HTTPS origin" in str(error.value)
    assert SECRET not in str(error.value)
    assert "input_value" not in str(error.value)


@pytest.mark.parametrize(
    "origin",
    ["https://tracker.example", "https://tracker.example/", "https://tracker.example:8443"],
)
def test_valid_origin(origin) -> None:
    assert Settings(keitaro_url=origin, _env_file=None).keitaro_url == origin.rstrip("/")


def test_log_level_is_validated_before_logging_startup() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(log_level=SECRET, _env_file=None)
    assert SECRET not in str(error.value)
    assert Settings(log_level="debug", _env_file=None).log_level == "DEBUG"


@pytest.mark.parametrize("module", ["live_fetch", "inspect"])
@pytest.mark.parametrize(
    "config",
    [
        {"ADROBOT_KEITARO_URL": f"https://user:{SECRET}@tracker.example"},
        {"ADROBOT_KEITARO_URL": f"https://tracker.example:{SECRET}"},
        {"ADROBOT_LOG_LEVEL": SECRET},
        {},
        {"ADROBOT_KEITARO_URL": "https://tracker.example"},
        {"ADROBOT_KEITARO_API_KEY": SECRET},
    ],
)
def test_cli_configuration_failures_have_no_traceback(module, config, tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ADROBOT_")}
    env.update(config)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", f"adrobot.keitaro.{module}", "501"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "configur" in result.stderr


@pytest.mark.parametrize("module", ["live_fetch", "inspect"])
@pytest.mark.parametrize("campaign", ["0", "-1", "9007199254740992"])
def test_cli_rejects_unsafe_campaign_before_settings(module, campaign, tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ADROBOT_")}
    env["ADROBOT_LOG_LEVEL"] = SECRET
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", f"adrobot.keitaro.{module}", campaign],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "positive safe integer" in result.stderr
    assert SECRET not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("level", ["WARN", "warn", "FATAL"])
def test_standard_logging_aliases_remain_supported(level) -> None:
    assert Settings(log_level=level, _env_file=None).log_level == level.upper()
