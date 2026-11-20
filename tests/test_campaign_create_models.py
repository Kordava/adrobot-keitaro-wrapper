"""Safe public DTO validation and GEO canonicalization."""

import pytest
from pydantic import ValidationError

from adrobot.api.create_models import CampaignCreateInput
from adrobot.keitaro.read_models import JS_SAFE_INTEGER


def test_canonical_name_country_dedup_and_offer_id() -> None:
    form = CampaignCreateInput(name="  Hello  ", geo="mx, AU, MX, ro", offer_id=42)
    assert form.name == "Hello"
    assert form.geo == "MX,AU,RO"
    assert form.countries == ["MX", "AU", "RO"]
    assert form.model_dump() == {"name": "Hello", "geo": "MX,AU,RO", "offer_id": 42}


@pytest.mark.parametrize("name", ["", " ", "a" * 121])
def test_reject_empty_or_oversized_names(name: str) -> None:
    with pytest.raises(ValidationError):
        CampaignCreateInput(name=name, geo="AU", offer_id=42)


@pytest.mark.parametrize("geo", ["", "A", "XX", "ZZ", "AU,", ",AU", "AU;;MX", "АU"])
def test_reject_invalid_or_unsupported_geo(geo: str) -> None:
    with pytest.raises(ValidationError):
        CampaignCreateInput(name="X", geo=geo, offer_id=42)


@pytest.mark.parametrize("offer_id", [0, -1, JS_SAFE_INTEGER + 1, True, "42", 1.5])
def test_reject_invalid_offer_id(offer_id: object) -> None:
    with pytest.raises(ValidationError):
        CampaignCreateInput.model_validate({"name": "X", "geo": "AU", "offer_id": offer_id})


def test_reject_unknown_request_fields_and_excessive_geo_count() -> None:
    with pytest.raises(ValidationError):
        CampaignCreateInput.model_validate(
            {"name": "X", "geo": "AU", "offer_id": 42, "api_key": "SYNTHETIC"}
        )
    from adrobot.campaigns.countries import ISO_ALPHA2

    with pytest.raises(ValidationError):
        CampaignCreateInput(name="X", geo=",".join(sorted(ISO_ALPHA2)[:31]), offer_id=42)
