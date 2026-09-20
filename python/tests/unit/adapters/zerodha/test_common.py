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
Test Zerodha conversion and identity helpers.
"""

from datetime import datetime

import pytest

from nautilus_trader.adapters.zerodha.common import datetime_to_ns
from nautilus_trader.adapters.zerodha.common import instrument_id
from nautilus_trader.adapters.zerodha.common import kite_tag
from nautilus_trader.adapters.zerodha.common import map_order_status
from nautilus_trader.adapters.zerodha.common import quantity_from_value
from nautilus_trader.adapters.zerodha.common import split_instrument_id
from nautilus_trader.model import ClientOrderId
from nautilus_trader.model import OrderStatus
from nautilus_trader.model import Quantity


def test_instrument_id_round_trip_preserves_exchange_and_symbol() -> None:
    """
    Instrument IDs distinguish the same symbol on different exchanges.
    """
    value = instrument_id("NSE", "INFY")

    assert str(value) == "NSE:INFY.ZERODHA"
    assert split_instrument_id(value) == ("NSE", "INFY")
    assert instrument_id("BSE", "INFY") != value


def test_kite_tag_is_stable_and_within_venue_limit() -> None:
    """
    Client IDs map to stable tags that fit Kite's 20-character limit.
    """
    client_order_id = ClientOrderId("O-20260920-12345678901234567890")

    assert kite_tag(client_order_id) == kite_tag(client_order_id)
    assert len(kite_tag(client_order_id)) == 20
    assert kite_tag(client_order_id) != kite_tag(ClientOrderId("O-OTHER"))


def test_naive_kite_timestamp_is_interpreted_as_india_time() -> None:
    """
    Naive SDK timestamps retain Kite's documented India timezone.
    """
    value = datetime_to_ns(datetime(2026, 9, 20, 9, 15), 0)  # noqa: DTZ001 - Kite SDK is naive.

    assert value == 1_789_875_900_000_000_000


@pytest.mark.parametrize(
    ("status", "filled", "expected"),
    [
        ("OPEN", 0, OrderStatus.ACCEPTED),
        ("OPEN", 1, OrderStatus.PARTIALLY_FILLED),
        ("COMPLETE", 1, OrderStatus.FILLED),
        ("CANCELLED", 0, OrderStatus.CANCELED),
        ("REJECTED", 0, OrderStatus.REJECTED),
        ("CANCEL PENDING", 0, OrderStatus.PENDING_CANCEL),
    ],
)
def test_order_status_mapping(status, filled, expected) -> None:
    """
    Kite status mapping preserves pending and terminal states.
    """
    assert map_order_status(status, filled) == expected


def test_fractional_quantity_fails_closed() -> None:
    """
    Fractional venue quantities are rejected instead of rounded.
    """
    with pytest.raises(ValueError, match="must be integral"):
        quantity_from_value("1.5")

    assert quantity_from_value("25") == Quantity.from_int(25)
