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
Test Zerodha WebSocket tick translation.
"""

from datetime import datetime
from typing import Any
from typing import ClassVar
from typing import cast

from nautilus_trader.adapters.zerodha.common import instrument_id
from nautilus_trader.adapters.zerodha.data import ZerodhaDataClient
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import QuoteTick
from nautilus_trader.model import TradeTick


class TestClock:
    """
    Supply a deterministic initialization timestamp.
    """

    def timestamp_ns(self) -> int:
        """
        Return the fixed timestamp.
        """
        return 1_800_000_000_000_000_000


class TestProvider:
    """
    Supply a deterministic instrument-token lookup.
    """

    ids_by_token: ClassVar = {408065: instrument_id("NSE", "INFY")}

    def price_precision(self, _token: int) -> int:
        """
        Return the precision declared by the test instrument master.
        """
        return 2


class DataHarness:
    """
    Capture data emitted by the production tick handler.
    """

    def __init__(self) -> None:
        """
        Initialize deterministic subscriptions and captured data.
        """
        self.clock = TestClock()
        self._quote_tokens = {408065}
        self._trade_tokens = {408065}
        self._last_trade: dict[int, tuple[Price, Quantity, int]] = {}
        self.data: list[object] = []

    def _provider(self) -> TestProvider:
        return TestProvider()

    def _handle_data(self, value: object) -> None:
        self.data.append(value)


def test_handle_full_tick_emits_exact_quote_and_trade_once() -> None:
    """
    A full-mode tick becomes exact top-of-book and trade events without duplicate trades.
    """
    client = DataHarness()
    tick = {
        "instrument_token": 408065,
        "last_price": 1_543.25,
        "last_quantity": 7,
        "exchange_timestamp": datetime(2026, 9, 20, 9, 15),  # noqa: DTZ001
        "last_trade_time": datetime(2026, 9, 20, 9, 14, 59),  # noqa: DTZ001
        "depth": {
            "buy": [{"price": 1_543.20, "quantity": 11}],
            "sell": [{"price": 1_543.25, "quantity": 13}],
        },
    }

    production_client = cast("Any", client)
    ZerodhaDataClient._handle_ticks(production_client, [tick])
    later_quote = {**tick, "exchange_timestamp": datetime(2026, 9, 20, 9, 15, 1)}  # noqa: DTZ001
    ZerodhaDataClient._handle_ticks(production_client, [later_quote])

    assert len(client.data) == 3
    quote = client.data[0]
    trade = client.data[1]
    assert isinstance(quote, QuoteTick)
    assert quote.bid_price == Price.from_str("1543.2")
    assert quote.ask_price == Price.from_str("1543.25")
    assert quote.bid_size == Quantity.from_int(11)
    assert quote.ask_size == Quantity.from_int(13)
    assert quote.ts_event == 1_789_875_900_000_000_000
    assert isinstance(trade, TradeTick)
    assert trade.price == Price.from_str("1543.25")
    assert trade.size == Quantity.from_int(7)
    assert trade.ts_event == 1_789_875_899_000_000_000
    assert len(str(trade.trade_id)) == 32
