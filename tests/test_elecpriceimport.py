import json
from pathlib import Path

import numpy.testing as npt
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_ELECPRICEIMPORT_1_JSON = DIR_TESTDATA.joinpath("import_input_1.json")


@pytest.fixture
def provider(sample_import_1_json, config_eos):
    """Fixture to create a ElecPriceProvider instance."""
    settings = {
        "elecprice": {
            "provider": "ElecPriceImport",
            "elecpriceimport": {
                "import_file_path": str(FILE_TESTDATA_ELECPRICEIMPORT_1_JSON),
                "import_json": json.dumps(sample_import_1_json),
            },
        }
    }
    config_eos.merge_settings_from_dict(settings)
    provider = ElecPriceImport()
    assert provider.enabled()
    return provider


@pytest.fixture
def sample_import_1_json():
    """Fixture that returns sample forecast data report."""
    with FILE_TESTDATA_ELECPRICEIMPORT_1_JSON.open("r", encoding="utf-8", newline=None) as f_res:
        input_data = json.load(f_res)
    return input_data


# ------------------------------------------------
# General forecast
# ------------------------------------------------


def test_singleton_instance(provider):
    """Test that ElecPriceForecast behaves as a singleton."""
    another_instance = ElecPriceImport()
    assert provider is another_instance


def test_invalid_provider(provider, config_eos):
    """Test requesting an unsupported provider."""
    settings = {
        "elecprice": {
            "provider": "<invalid>",
            "elecpriceimport": {
                "import_file_path": str(FILE_TESTDATA_ELECPRICEIMPORT_1_JSON),
            },
        }
    }
    with pytest.raises(ValueError, match="not a valid electricity price provider"):
        config_eos.merge_settings_from_dict(settings)


# ------------------------------------------------
# Import
# ------------------------------------------------


@pytest.mark.parametrize(
    "start_datetime, from_file",
    [
        ("2024-11-10 00:00:00", True),  # No DST in Germany
        ("2024-08-10 00:00:00", True),  # DST in Germany
        ("2024-03-31 00:00:00", True),  # DST change in Germany (23 hours/ day)
        ("2024-10-27 00:00:00", True),  # DST change in Germany (25 hours/ day)
        ("2024-11-10 00:00:00", False),  # No DST in Germany
        ("2024-08-10 00:00:00", False),  # DST in Germany
        ("2024-03-31 00:00:00", False),  # DST change in Germany (23 hours/ day)
        ("2024-10-27 00:00:00", False),  # DST change in Germany (25 hours/ day)
    ],
)
def test_import(provider, sample_import_1_json, start_datetime, from_file, config_eos):
    """Test fetching forecast from Import."""
    key = "elecprice_marketprice_wh"
    ems_eos = get_ems()
    ems_eos.set_start_datetime(to_datetime(start_datetime, in_timezone="Europe/Berlin"))
    if from_file:
        config_eos.elecprice.elecpriceimport.import_json = None
        assert config_eos.elecprice.elecpriceimport.import_json is None
    else:
        config_eos.elecprice.elecpriceimport.import_file_path = None
        assert config_eos.elecprice.elecpriceimport.import_file_path is None
    provider.delete_by_datetime(start_datetime=None, end_datetime=None)

    # Call the method
    provider.update_data()

    # Assert: Verify the result is as expected
    assert provider.ems_start_datetime is not None
    assert provider.total_hours is not None
    assert compare_datetimes(provider.ems_start_datetime, ems_eos.start_datetime).equal

    expected_values = sample_import_1_json[key]
    result_values = provider.key_to_array(
        key=key,
        start_datetime=provider.ems_start_datetime,
        end_datetime=provider.ems_start_datetime + to_duration(f"{len(expected_values)} hours"),
        interval=to_duration("1 hour"),
    )
    # Allow for some difference due to value calculation on DST change
    npt.assert_allclose(result_values, expected_values, rtol=0.001)


def test_import_uses_configured_timezone_not_host_timezone(
    provider, sample_import_1_json, config_eos
):
    """Daily schedules align with the configured timezone, not the host's UTC timezone.

    Regression test: (elecprice) schedules are local wall-clock time, but were previously
    anchored to the host machine's local timezone. On a UTC host this shifted the HT/NT
    schedule by the offset (e.g. 0-5am night rate landed on 0-5am UTC = 2-7am Europe/Berlin).
    Ensure index 0 maps to 00:00 in the configured timezone regardless of host timezone.
    """
    key = "elecprice_marketprice_wh"
    ems_eos = get_ems()
    # Configured location timezone resolves from lat/long to Europe/Berlin.
    # Default config uses Berlin coordinates (52.52, 13.405).
    assert config_eos.general.timezone == "Europe/Berlin"
    config_eos.elecprice.elecpriceimport.import_json = json.dumps(sample_import_1_json)
    config_eos.elecprice.elecpriceimport.import_file_path = None

    # Set the EMS start datetime in UTC, as it would be on a host running UTC:
    # 2024-11-10 22:00 UTC == 2024-11-11 00:00 Europe/Berlin.
    ems_eos.set_start_datetime(to_datetime("2024-11-10 22:00:00", in_timezone="UTC"))
    provider.delete_by_datetime(start_datetime=None, end_datetime=None)

    provider.update_data()
    assert provider.ems_start_datetime is not None

    # Index 0 (value 0.0003384) must land on 00:00 in the configured timezone,
    # i.e. 2024-11-11 00:00 Europe/Berlin = 2024-11-10 23:00 UTC.
    # The local-midnight anchor in Berlin must be used, not UTC midnight.
    berlin_midnight = to_datetime("2024-11-11T00:00:00", in_timezone="Europe/Berlin")
    rec = provider.get_by_datetime(berlin_midnight)
    assert rec is not None
    npt.assert_allclose(rec.elecprice_marketprice_wh, sample_import_1_json[key][0], rtol=0.001)

    # Sanity: the wrong (UTC-midnight) anchor must NOT match the first value.
    utc_midnight = to_datetime("2024-11-11T00:00:00", in_timezone="UTC")
    rec_utc = provider.get_by_datetime(utc_midnight)
    if rec_utc is not None and rec_utc.elecprice_marketprice_wh is not None:
        with pytest.raises(AssertionError):
            npt.assert_allclose(
                rec_utc.elecprice_marketprice_wh,
                sample_import_1_json[key][0],
                rtol=0.001,
                err_msg="UTC anchor must not align with index 0",
            )
