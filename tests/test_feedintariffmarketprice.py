from unittest.mock import PropertyMock, patch

import pytest

from akkudoktoreos.prediction import feedintariffmarketprice as fitm
from akkudoktoreos.prediction.feedintariffmarketprice import FeedInTariffMarketPrice
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


class FakeElecPriceProvider:
    def __init__(self, values):
        self.values = values
        self.update_calls = []

    def update_data(self, force_enable=False, force_update=False):
        self.update_calls.append((force_enable, force_update))

    def key_to_array(self, key, start_datetime=None, end_datetime=None, interval=None, fill_method=None):
        if key != "elecprice_marketprice_wh":
            raise KeyError(key)
        return self.values


class FakePrediction:
    def __init__(self, provider):
        self.provider = provider
        self.requested_provider_id = None

    def provider_by_id(self, provider_id):
        self.requested_provider_id = provider_id
        return self.provider


@pytest.fixture
def provider(config_eos):
    settings = {
        "elecprice": {
            "provider": "ElecPriceImport",
        },
        "feedintariff": {
            "provider": "FeedInTariffMarketPrice",
            "provider_settings": {
                "FeedInTariffMarketPrice": {
                    "feed_in_tariff_kwh": 0.082,
                    "market_divisor": 50.0,
                },
            },
        },
    }
    config_eos.merge_settings_from_dict(settings)
    instance = FeedInTariffMarketPrice()
    assert instance.enabled()
    return instance


def test_market_price_provider_default(config_eos):
    settings = {
        "feedintariff": {
            "provider": "FeedInTariffMarketPrice",
            "provider_settings": {
                "FeedInTariffMarketPrice": {
                    "feed_in_tariff_kwh": 0.082,
                    "market_divisor": 50.0,
                },
            },
        }
    }
    config_eos.merge_settings_from_dict(settings)
    market_settings = config_eos.feedintariff.provider_settings.FeedInTariffMarketPrice
    assert market_settings is not None
    assert market_settings.market_price_provider == "ElecPriceEnergyCharts"


def test_market_price_provider_invalid_rejected(config_eos):
    settings = {
        "feedintariff": {
            "provider": "FeedInTariffMarketPrice",
            "provider_settings": {
                "FeedInTariffMarketPrice": {
                    "feed_in_tariff_kwh": 0.082,
                    "market_divisor": 50.0,
                    "market_price_provider": "NoSuchProvider",
                },
            },
        }
    }
    with pytest.raises(ValueError, match="Market price provider"):
        config_eos.merge_settings_from_dict(settings)


def test_market_price_provider_current_provider_supported(config_eos):
    settings = {
        "feedintariff": {
            "provider": "FeedInTariffMarketPrice",
            "provider_settings": {
                "FeedInTariffMarketPrice": {
                    "feed_in_tariff_kwh": 0.082,
                    "market_divisor": 50.0,
                    "market_price_provider": "CurrentElecPriceProvider",
                },
            },
        }
    }
    config_eos.merge_settings_from_dict(settings)
    market_settings = config_eos.feedintariff.provider_settings.FeedInTariffMarketPrice
    assert market_settings is not None
    assert market_settings.market_price_provider == "CurrentElecPriceProvider"


def test_update_uses_selected_market_provider_and_forces_update(provider, monkeypatch):
    fake_market_provider = FakeElecPriceProvider([0.10, 0.20])
    fake_prediction = FakePrediction(fake_market_provider)
    fixed_start = to_datetime("2026-03-06T00:00:00+01:00")

    # Patch runtime dependencies so the test is deterministic and offline-safe.
    monkeypatch.setattr(fitm, "ElecPriceProvider", FakeElecPriceProvider)
    monkeypatch.setattr(fitm, "get_prediction", lambda: fake_prediction)

    with patch.object(FeedInTariffMarketPrice, "ems_start_datetime", new_callable=PropertyMock) as p:
        p.return_value = fixed_start
        provider._update_data(force_update=True)

    assert fake_prediction.requested_provider_id == "ElecPriceEnergyCharts"
    assert fake_market_provider.update_calls == [(True, True)]

    result = provider.key_to_array(
        key="feed_in_tariff_wh",
        start_datetime=fixed_start,
        end_datetime=fixed_start.add(hours=2),
        interval=to_duration("1 hour"),
        fill_method="ffill",
    )
    assert result[0] == pytest.approx(0.082 / 1000 + 0.10 / 50.0)
    assert result[1] == pytest.approx(0.082 / 1000 + 0.20 / 50.0)


def test_update_uses_current_elecprice_provider(provider, config_eos, monkeypatch):
    settings = {
        "feedintariff": {
            "provider_settings": {
                "FeedInTariffMarketPrice": {
                    "feed_in_tariff_kwh": 0.082,
                    "market_divisor": 50.0,
                    "market_price_provider": "CurrentElecPriceProvider",
                },
            },
        },
        "elecprice": {
            "provider": "ElecPriceImport",
        },
    }
    config_eos.merge_settings_from_dict(settings)

    fake_market_provider = FakeElecPriceProvider([0.30])
    fake_prediction = FakePrediction(fake_market_provider)
    fixed_start = to_datetime("2026-03-06T00:00:00+01:00")

    monkeypatch.setattr(fitm, "ElecPriceProvider", FakeElecPriceProvider)
    monkeypatch.setattr(fitm, "get_prediction", lambda: fake_prediction)

    with patch.object(FeedInTariffMarketPrice, "ems_start_datetime", new_callable=PropertyMock) as p:
        p.return_value = fixed_start
        provider._update_data(force_update=False)

    assert fake_prediction.requested_provider_id == "ElecPriceImport"
    assert fake_market_provider.update_calls == [(True, False)]
