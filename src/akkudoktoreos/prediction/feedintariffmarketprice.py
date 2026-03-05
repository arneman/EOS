"""Provides feed in tariff data derived from EPEX spot market prices.

The feed-in tariff is calculated per hour as:
    feed_in_tariff_wh = eet_wh + elecprice_marketprice_wh / market_divisor

This gives the optimizer an incentive to feed in more electricity when market
prices are high and to retain energy in storage when prices are low.
"""

from typing import Iterable, Optional, cast
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.elecprice import elecprice_provider_ids
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


CURRENT_ELECPRICE_PROVIDER = "CurrentElecPriceProvider"
DEFAULT_MARKET_PRICE_PROVIDER = "ElecPriceEnergyCharts"


def market_price_provider_ids() -> list[str]:
    """Valid market price providers for FeedInTariffMarketPrice."""
    providers = set(elecprice_provider_ids())
    # Include built-in defaults when prediction is not initialized yet.
    providers.update(["ElecPriceAkkudoktor", "ElecPriceEnergyCharts", "ElecPriceImport"])
    providers.add(CURRENT_ELECPRICE_PROVIDER)
    return sorted(providers)


class FeedInTariffMarketPriceCommonSettings(SettingsBaseModel):
    """Common settings for market-price-linked feed-in tariff."""

    feed_in_tariff_kwh: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Base feed-in tariff (EEG remuneration) [€/kWh]. "
                "The market price component is added on top of this base."
            ),
            "examples": [0.082],
        },
    )
    market_divisor: Optional[float] = Field(
        default=50.0,
        gt=0,
        json_schema_extra={
            "description": (
                "Divisor applied to the EPEX market price before adding it to the base tariff. "
                "A smaller divisor increases the market price influence. "
                "Example: divisor=50 → 10 ct/kWh EPEX adds 0.2 ct/kWh to the feed-in tariff."
            ),
            "examples": [50.0],
        },
    )
    market_price_provider: Optional[str] = Field(
        default=DEFAULT_MARKET_PRICE_PROVIDER,
        json_schema_extra={
            "description": (
                "Elecprice provider used as market price source for FeedInTariffMarketPrice. "
                "Use 'CurrentElecPriceProvider' to use the active elecprice.provider."
            ),
            "examples": ["ElecPriceEnergyCharts", "CurrentElecPriceProvider"],
        },
    )

    @field_validator("market_price_provider", mode="after")
    @classmethod
    def validate_market_price_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in market_price_provider_ids():
            return value
        raise ValueError(
            f"Market price provider '{value}' is not valid: {market_price_provider_ids()}."
        )


class FeedInTariffMarketPrice(FeedInTariffProvider):
    """Hourly feed-in tariff coupled to EPEX spot market prices.

    FeedInTariffMarketPrice is a singleton-based provider that computes per-hour
    feed-in tariff values as:

        feed_in_tariff_wh = eet_kwh / 1000 + elecprice_marketprice_wh / market_divisor

    The elecprice provider must appear before this provider in the prediction
    provider list so that market price data is already available when this
    provider runs.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the FeedInTariffMarketPrice provider."""
        return "FeedInTariffMarketPrice"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        error_msg = "FeedInTariffMarketPrice: configuration not provided"
        try:
            settings = self.config.feedintariff.provider_settings.FeedInTariffMarketPrice
        except Exception:
            logger.exception(error_msg)
            raise ValueError(error_msg)
        if settings is None or settings.feed_in_tariff_kwh is None:
            logger.error(error_msg)
            raise ValueError(error_msg)

        eet_wh = settings.feed_in_tariff_kwh / 1000.0
        market_divisor = settings.market_divisor if settings.market_divisor is not None else 50.0

        configured_market_provider = (
            settings.market_price_provider
            if settings.market_price_provider is not None
            else DEFAULT_MARKET_PRICE_PROVIDER
        )
        selected_market_provider = configured_market_provider
        if configured_market_provider == CURRENT_ELECPRICE_PROVIDER:
            selected_market_provider = self.config.elecprice.provider
            if selected_market_provider is None:
                raise ValueError(
                    "FeedInTariffMarketPrice: elecprice.provider must be set when "
                    "market_price_provider is 'CurrentElecPriceProvider'."
                )

        prediction = get_prediction()
        try:
            market_provider = prediction.provider_by_id(selected_market_provider)
        except ValueError as ex:
            raise ValueError(
                "FeedInTariffMarketPrice: unknown market price provider "
                f"'{selected_market_provider}' configured via '{configured_market_provider}'."
            ) from ex

        if not isinstance(market_provider, ElecPriceProvider):
            raise ValueError(
                "FeedInTariffMarketPrice: configured market price provider "
                f"'{selected_market_provider}' is not an ElecPrice provider."
            )

        market_provider.update_data(force_enable=True, force_update=force_update)

        start_datetime = self.ems_start_datetime
        end_datetime = start_datetime.add(hours=self.config.prediction.hours)
        interval = to_duration("1 hour")

        try:
            marketprice_wh_array = market_provider.key_to_array(
                key="elecprice_marketprice_wh",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                interval=interval,
                fill_method="ffill",
            )
            marketprice_wh_values = [
                float(value) for value in cast(Iterable[object], marketprice_wh_array)
            ]
        except Exception as ex:
            logger.error(
                "FeedInTariffMarketPrice: failed to load 'elecprice_marketprice_wh' from "
                "provider '{}': {}",
                selected_market_provider,
                ex,
            )
            raise ValueError(
                "FeedInTariffMarketPrice: market price data unavailable from "
                f"provider '{selected_market_provider}'."
            ) from ex

        date = start_datetime
        for epex_wh in marketprice_wh_values:
            feed_in_tariff_wh = eet_wh + epex_wh / market_divisor
            self.update_value(date, "feed_in_tariff_wh", feed_in_tariff_wh)
            date = date.add(hours=1)

        logger.debug(
            "FeedInTariffMarketPrice: updated {} hourly values (eet={} €/kWh, divisor={}, "
            "market_provider={}).",
            len(marketprice_wh_values),
            settings.feed_in_tariff_kwh,
            market_divisor,
            selected_market_provider,
        )
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
