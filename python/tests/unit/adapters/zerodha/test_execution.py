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
Test Zerodha execution-report translation.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest

from nautilus_trader.adapters.zerodha.common import instrument_id
from nautilus_trader.adapters.zerodha.execution import ZerodhaExecutionClient
from nautilus_trader.common import Clock
from nautilus_trader.common import OrderFactory
from nautilus_trader.model import AccountId
from nautilus_trader.model import ClientOrderId
from nautilus_trader.model import OrderSide
from nautilus_trader.model import OrderStatus
from nautilus_trader.model import OrderType
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import StrategyId
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import TraderId
from nautilus_trader.model import VenueOrderId


class TestClock:
    """
    Supply a deterministic report initialization timestamp.
    """

    def timestamp_ns(self) -> int:
        """
        Return the fixed timestamp.
        """
        return 1_800_000_000_000_000_000


class TestCache:
    """
    Resolve a venue order to its cached client order ID.
    """

    def client_order_id(self, _venue_order_id: VenueOrderId) -> ClientOrderId:
        """
        Return the fixed client order ID.
        """
        return ClientOrderId("O-19700101-000001")


class ExecutionHarness:
    """
    Supply the dependencies used by the production report parsers.
    """

    def __init__(self) -> None:
        """
        Initialize deterministic report dependencies.
        """
        self.clock = TestClock()
        self.account_id = AccountId("ZERODHA-AB1234")
        self.cache = TestCache()
        self.api = TestApi()
        self.events: list[tuple[str, object]] = []
        self._accepted_venue_order_ids: set[str] = set()

    def _resolve_client_order_id(self, _tag: str | None) -> ClientOrderId:
        return ClientOrderId("O-19700101-000001")

    def _api(self) -> TestApi:
        return self.api

    def _config(self) -> TestConfig:
        return TestConfig()

    def _validate_order(self, order: object) -> str | None:
        return ZerodhaExecutionClient._validate_order(cast("Any", self), cast("Any", order))

    def _to_kite_order_type(self, order_type: OrderType) -> str:
        return ZerodhaExecutionClient._to_kite_order_type(order_type)

    def generate_order_submitted(self, order: object) -> None:
        """
        Capture a submitted event.
        """
        self.events.append(("submitted", order))

    def generate_order_accepted(
        self,
        order: object,
        venue_order_id: VenueOrderId,
        _ts_event: int,
    ) -> None:
        """
        Capture an accepted event.
        """
        self.events.append(("accepted", (order, venue_order_id)))


class TestConfig:
    """
    Supply the configured Zerodha product.
    """

    product = "MIS"


class TestApi:
    """
    Capture a placed order without contacting Kite.
    """

    def __init__(self) -> None:
        """
        Initialize the captured request.
        """
        self.request: dict[str, object] | None = None

    def place_order(self, **kwargs: object) -> str:
        """
        Capture the request and return a deterministic venue order ID.
        """
        self.request = kwargs
        return "260920000123456"


class TestOrder:
    """
    Supply a supported limit order to the production submit path.
    """

    def __init__(self) -> None:
        """
        Initialize exact order fields.
        """
        self.instrument_id = instrument_id("NFO", "NIFTY26SEPFUT")
        self.client_order_id = ClientOrderId("O-19700101-000001")
        self.venue_order_id = None
        self.side = OrderSide.BUY
        self.order_type = OrderType.LIMIT
        self.time_in_force = TimeInForce.DAY
        self.quantity = Quantity.from_int(65)
        self.price = Price.from_str("25001.25")
        self.trigger_price = None
        self.status = OrderStatus.INITIALIZED
        self.is_post_only = False
        self.is_reduce_only = False
        self.is_quote_quantity = False


@pytest.mark.asyncio
async def test_submit_maps_order_to_exact_kite_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A valid Nautilus order produces integral Kite quantity and decimal-string price fields.
    """
    run_inline = AsyncMock(side_effect=lambda function, **kwargs: function(**kwargs))
    monkeypatch.setattr(asyncio, "to_thread", run_inline)
    harness = ExecutionHarness()

    await ZerodhaExecutionClient._submit(cast("Any", harness), cast("Any", TestOrder()))

    assert harness.api.request == {
        "variety": "regular",
        "exchange": "NFO",
        "tradingsymbol": "NIFTY26SEPFUT",
        "transaction_type": "BUY",
        "quantity": 65,
        "product": "MIS",
        "order_type": "LIMIT",
        "validity": "DAY",
        "tag": "ntb9d0bdb802c17baef2",
        "price": "25001.25",
    }
    assert [event[0] for event in harness.events] == ["submitted", "accepted"]


def _real_orders() -> dict[str, Any]:
    """
    Build one order of each supported type with the production order factory.
    """
    factory = OrderFactory(TraderId.from_str("TESTER-001"), StrategyId("S-001"), Clock.new_test())
    common = {
        "instrument_id": instrument_id("NFO", "NIFTY26SEPFUT"),
        "quantity": Quantity.from_int(65),
        "time_in_force": TimeInForce.DAY,
    }
    price = Price.from_str("25001.25")
    trigger = Price.from_str("24990.00")
    return {
        "MARKET": factory.market(order_side=OrderSide.BUY, **common),
        "LIMIT": factory.limit(order_side=OrderSide.BUY, price=price, **common),
        "SL-M": factory.stop_market(order_side=OrderSide.SELL, trigger_price=trigger, **common),
        "SL": factory.stop_limit(order_side=OrderSide.SELL, price=price, trigger_price=trigger, **common),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kite_type", "expected_prices"),
    [
        ("MARKET", {}),
        ("LIMIT", {"price": "25001.25"}),
        ("SL-M", {"trigger_price": "24990.00"}),
        ("SL", {"price": "25001.25", "trigger_price": "24990.00"}),
    ],
)
async def test_submit_accepts_every_real_order_type(
    monkeypatch: pytest.MonkeyPatch,
    kite_type: str,
    expected_prices: dict[str, str],
) -> None:
    """
    Real orders lack the fields that do not apply to their type; submit must not require them.

    A market order has no ``price``, ``trigger_price`` or ``is_post_only``. Reading them
    directly raised ``AttributeError`` and left the order unsent.
    """
    run_inline = AsyncMock(side_effect=lambda function, **kwargs: function(**kwargs))
    monkeypatch.setattr(asyncio, "to_thread", run_inline)
    harness = ExecutionHarness()

    await ZerodhaExecutionClient._submit(cast("Any", harness), _real_orders()[kite_type])

    request = harness.api.request
    assert request is not None
    assert request["order_type"] == kite_type
    assert request["quantity"] == 65
    assert request["validity"] == "DAY"
    assert {key: request[key] for key in ("price", "trigger_price") if key in request} == expected_prices
    assert [event[0] for event in harness.events] == ["submitted", "accepted"]


def test_validate_denies_the_default_time_in_force_on_a_real_order() -> None:
    """
    The order factory defaults to GTC, which Kite does not offer; the denial must be a message.
    """
    factory = OrderFactory(TraderId.from_str("TESTER-001"), StrategyId("S-001"), Clock.new_test())
    order = factory.market(
        instrument_id=instrument_id("NFO", "NIFTY26SEPFUT"),
        order_side=OrderSide.BUY,
        quantity=Quantity.from_int(65),
    )

    error = ZerodhaExecutionClient._validate_order(cast("Any", ExecutionHarness()), cast("Any", order))

    assert error is not None
    assert "time in force" in error


def test_parse_order_report_preserves_partial_fill_and_stop_price() -> None:
    """
    A partially filled Kite stop-limit order maps to a precise Nautilus report.
    """
    client = cast("Any", ExecutionHarness())
    report = ZerodhaExecutionClient._parse_order_report(
        client,
        {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26SEPFUT",
            "order_id": "260920000123456",
            "tag": "ignored",
            "transaction_type": "BUY",
            "order_type": "SL",
            "validity": "DAY",
            "status": "OPEN",
            "quantity": 65,
            "filled_quantity": 13,
            "price": 25_001.25,
            "trigger_price": 25_000.05,
            "average_price": 24_999.95,
            "order_timestamp": "2026-09-20T09:15:00",
            "exchange_update_timestamp": "2026-09-20T09:16:00",
        },
    )

    assert report.order_status == OrderStatus.PARTIALLY_FILLED
    assert report.order_type == OrderType.STOP_LIMIT
    assert report.time_in_force == TimeInForce.DAY
    assert report.quantity == Quantity.from_int(65)
    assert report.filled_qty == Quantity.from_int(13)
    assert report.price == Price.from_str("25001.25")
    assert report.trigger_price == Price.from_str("25000.05")
    assert report.avg_px == Decimal("24999.95")
    assert report.ts_accepted == 1_789_875_900_000_000_000
    assert report.ts_last == 1_789_875_960_000_000_000


def test_parse_fill_report_preserves_trade_identity_and_values() -> None:
    """
    A Kite trade maps to an exact fill linked to its cached order.
    """
    client = cast("Any", ExecutionHarness())
    report = ZerodhaExecutionClient._parse_fill_report(
        client,
        {
            "exchange": "NSE",
            "tradingsymbol": "INFY",
            "order_id": "260920000123456",
            "trade_id": "9876543",
            "transaction_type": "SELL",
            "quantity": 7,
            "average_price": 1_543.25,
            "fill_timestamp": "2026-09-20T09:15:01",
        },
    )

    assert report.client_order_id == ClientOrderId("O-19700101-000001")
    assert report.venue_order_id == VenueOrderId("260920000123456")
    assert str(report.trade_id) == "9876543"
    assert report.last_qty == Quantity.from_int(7)
    assert report.last_px == Price.from_str("1543.25")
    assert report.commission.is_zero()
    assert report.ts_event == 1_789_875_901_000_000_000
