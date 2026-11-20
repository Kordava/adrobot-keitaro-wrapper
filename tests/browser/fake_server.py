"""Local-only browser fixture backed by httpx.MockTransport, NEVER live Keitaro."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from create_fakes import FakeCreateKeitaro  # noqa: E402

from adrobot.config import Settings  # noqa: E402
from adrobot.main import create_app  # noqa: E402

fake = FakeCreateKeitaro()
app = create_app(
    Settings(
        keitaro_url="https://tracker.example",
        keitaro_api_key="SYNTHETIC-BROWSER-KEY",
        create_domain_id=10,
        create_group_id=20,
        create_traffic_source_id=30,
        create_offer_action_type="http",
        create_mapping_verified=True,
        create_writes_enabled=True,
        _env_file=None,
    ),
    keitaro_transport=httpx.MockTransport(fake),
)


@app.get("/__test__/state")
def test_state() -> dict[str, object]:
    return {
        "writes": len(fake.posts),
        "campaign_writes": sum(path.endswith("/campaigns") for path, _ in fake.posts),
        "stream_writes": sum(path.endswith("/streams") for path, _ in fake.posts),
        "campaign_exists": fake.campaign is not None,
    }


@app.post("/__test__/reset")
def test_reset() -> dict[str, str]:
    fake.requests.clear()
    fake.posts.clear()
    fake.campaign = None
    fake.streams.clear()
    fake.fail_post_at = None
    fake.fail_post_status = 422
    fake.fail_transport_at = None
    return {"status": "reset"}


@app.post("/__test__/scenario/partial_fallback")
def set_partial_fallback() -> dict[str, str]:
    """Synthetic-only known failure of the third write for browser display."""
    fake.fail_post_at = 3
    fake.fail_post_status = 422
    return {"status": "configured"}


if __name__ == "__main__":
    import os

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("ADROBOT_BROWSER_PORT", "18765")))
