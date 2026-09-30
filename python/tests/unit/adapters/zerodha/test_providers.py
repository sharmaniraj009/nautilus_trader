# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Test Zerodha instrument-master parsing.
"""

import asyncio
from datetime import date

from nautilus_trader.adapters.zerodha.providers import ZerodhaInstrumentProvider
from nautilus_trader.adapters.zerodha.providers import parse_instrument
from nautilus_trader.live import InstrumentProviderConfig
from nautilus_trader.model import AssetClass
from nautilus_trader.model import Equity
from nautilus_trader.model import FuturesContract
from nautilus_trader.model import IndexInstrument
from nautilus_trader.model import OptionContract
from nautilus_trader.model import OptionKind
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity


def instrument_row(**updates: object) -> dict:
    """
    Return a representative instrument-master row.
    """
    values = {
        "instrument_token": 408065,
        "exchange_token": "1594",
        "tradingsymbol": "INFY",
        "name": "INFOSYS",
        "last_price": 0.0,
        "expiry": None,
        "strike": 0.0,
        "tick_size": 0.05,
        "lot_size": 1,
        "instrument_type": "EQ",
        "segment": "NSE",
        "exchange": "NSE",
    }
    values.update(updates)
    return values


def test_parse_equity_preserves_tick_and_lot() -> None:
    """
    Equity rows become INR instruments with exact increments.
    """
    instrument = parse_instrument(instrument_row(), 17)

    assert isinstance(instrument, Equity)
    assert str(instrument.id) == "NSE:INFY.ZERODHA"
    assert instrument.price_increment == Price.from_str("0.05")
    assert instrument.lot_size == Quantity.from_int(1)
    assert instrument.info["instrument_token"] == 408065


def test_parse_index_future_preserves_contract_multiplier() -> None:
    """
    NFO futures become index contracts with the venue lot multiplier.
    """
    instrument = parse_instrument(
        instrument_row(
            instrument_token=5720322,
            tradingsymbol="NIFTY26SEPFUT",
            name="NIFTY",
            expiry=date(2026, 9, 24),
            tick_size=0.05,
            lot_size=65,
            instrument_type="FUT",
            segment="NFO-FUT",
            exchange="NFO",
        ),
        19,
    )

    assert isinstance(instrument, FuturesContract)
    assert instrument.asset_class == AssetClass.INDEX
    assert instrument.multiplier == Quantity.from_int(1)
    assert instrument.lot_size == Quantity.from_int(65)
    assert instrument.expiration_ns == 1_790_244_000_000_000_000


def test_parse_option_preserves_kind_and_strike() -> None:
    """
    NFO option rows preserve put/call kind and strike exactly.
    """
    instrument = parse_instrument(
        instrument_row(
            instrument_token=123,
            tradingsymbol="NIFTY26SEP25000PE",
            name="NIFTY",
            expiry=date(2026, 9, 24),
            strike=25000.0,
            tick_size=0.05,
            lot_size=65,
            instrument_type="PE",
            segment="NFO-OPT",
            exchange="NFO",
        ),
        23,
    )

    assert isinstance(instrument, OptionContract)
    assert instrument.option_kind == OptionKind.PUT
    assert instrument.strike_price == Price.from_str("25000.0")


def test_parse_currency_future_uses_fx_asset_class() -> None:
    """
    Currency derivatives use the FX asset class rather than commodity.
    """
    instrument = parse_instrument(
        instrument_row(
            instrument_token=456,
            tradingsymbol="USDINR26SEPFUT",
            name="USDINR",
            expiry=date(2026, 9, 24),
            tick_size=0.0025,
            lot_size=1_000,
            instrument_type="FUT",
            segment="CDS-FUT",
            exchange="CDS",
        ),
        29,
    )

    assert isinstance(instrument, FuturesContract)
    assert instrument.asset_class == AssetClass.FX


def test_parse_index_row_without_lot_or_tick_size() -> None:
    """
    Index rows carry no lot size and sometimes no tick size, yet must still load.
    """
    for instrument_type, tick_size in (("INDEX", 0.05), ("EQ", 0.0)):
        instrument = parse_instrument(
            instrument_row(
                instrument_token=256265,
                tradingsymbol="NIFTY 50",
                name="NIFTY",
                tick_size=tick_size,
                lot_size=0,
                instrument_type=instrument_type,
                segment="INDICES",
            ),
            17,
        )

        assert isinstance(instrument, IndexInstrument)
        assert str(instrument.id) == "NSE:NIFTY 50.ZERODHA"
        assert instrument.price_increment == Price.from_str("0.05")
        assert instrument.price_precision == 2


def test_load_all_filters_by_exchange_and_name() -> None:
    """
    The exchange and name filters keep startup from loading the whole instrument master.
    """
    rows = [
        instrument_row(),
        instrument_row(instrument_token=1, tradingsymbol="RELIANCE", name="RELIANCE"),
        instrument_row(instrument_token=2, tradingsymbol="INFY", name="INFOSYS", exchange="BSE"),
    ]
    provider = ZerodhaInstrumentProvider(InstrumentProviderConfig(), lambda: rows)

    asyncio.run(provider.load_all_async({"exchanges": ["NSE"], "names": ["INFOSYS"]}))

    assert [str(instrument.id) for instrument in provider.list_all()] == ["NSE:INFY.ZERODHA"]
    assert provider.ids_by_token == {408065: provider.list_all()[0].id}
