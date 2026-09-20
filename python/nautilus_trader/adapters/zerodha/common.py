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
Provide exact conversion and identity helpers for Zerodha Kite.
"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from hashlib import blake2b
from zoneinfo import ZoneInfo

from nautilus_trader.core import dt_to_unix_nanos
from nautilus_trader.model import ClientOrderId
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import OrderSide
from nautilus_trader.model import OrderStatus
from nautilus_trader.model import OrderType
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import TradeId

from .constants import ZERODHA_VENUE


INDIA = ZoneInfo("Asia/Kolkata")
Scalar = Decimal | int | float | str


def decimal_from_value(value: Scalar) -> Decimal:
    """
    Convert a Kite scalar without introducing an additional float round trip.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def price_from_value(value: Scalar, precision: int | None = None) -> Price:
    """
    Convert a Kite price to an exact Nautilus price.
    """
    decimal = decimal_from_value(value)
    if precision is None:
        return Price.from_decimal(decimal)
    return Price.from_decimal_dp(decimal, precision)


def quantity_from_value(value: Scalar) -> Quantity:
    """
    Convert an integral Kite quantity to a Nautilus quantity.
    """
    quantity = decimal_from_value(value)
    if quantity != quantity.to_integral_value():
        raise ValueError(f"Kite quantity must be integral, received {quantity}")
    return Quantity.from_int(int(quantity))


def instrument_id(exchange: str, tradingsymbol: str) -> InstrumentId:
    """
    Return an unambiguous broker-routed instrument ID.
    """
    return InstrumentId(Symbol(f"{exchange}:{tradingsymbol}"), ZERODHA_VENUE)


def split_instrument_id(value: InstrumentId) -> tuple[str, str]:
    """
    Return the Kite exchange and trading symbol encoded in an instrument ID.
    """
    symbol = str(value.symbol)
    exchange, separator, tradingsymbol = symbol.partition(":")
    if not separator or not exchange or not tradingsymbol:
        raise ValueError(
            f"Expected Zerodha instrument symbol EXCHANGE:TRADINGSYMBOL, received {symbol}",
        )
    return exchange, tradingsymbol


def kite_tag(client_order_id: ClientOrderId) -> str:
    """
    Return a deterministic Kite-compatible tag for a client order ID.

    Kite limits tags to 20 characters. The 72-bit digest keeps the tag stable across restarts
    while the cache remains the source of truth for resolving it back to a client order ID.
    """
    digest = blake2b(str(client_order_id).encode(), digest_size=9).hexdigest()
    return f"nt{digest}"


def market_trade_id(token: int, ts_event: int, price: Price, quantity: Quantity) -> TradeId:
    """
    Return a deterministic, length-safe identity for a Kite market-data trade.

    Kite full-mode ticks do not contain exchange trade IDs, so the identity uses all available
    trade fields and a 128-bit digest to remain within Nautilus's 36-character limit.
    """
    value = f"{token}:{ts_event}:{price.raw}:{quantity.raw}"
    return TradeId(blake2b(value.encode(), digest_size=16).hexdigest())


def datetime_to_ns(value: datetime | str | None, fallback_ns: int) -> int:
    """
    Convert Kite timestamps to Unix nanoseconds.
    """
    if value is None or value == "":
        return fallback_ns
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise TypeError(f"Expected datetime or ISO timestamp, received {type(value).__name__}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=INDIA)
    return dt_to_unix_nanos(value.astimezone(UTC))


def expiry_to_ns(value: date | datetime | str, exchange: str) -> int:
    """
    Convert the date-only instrument expiry into the scheduled local session close.
    """
    if isinstance(value, str):
        value = date.fromisoformat(value)
    expiry_date = value.date() if isinstance(value, datetime) else value
    close = time(23, 30) if exchange == "MCX" else time(15, 30)
    local = datetime.combine(expiry_date, close, INDIA)
    return dt_to_unix_nanos(local.astimezone(UTC))


def map_order_side(value: str) -> OrderSide:
    """
    Map a Kite transaction type to Nautilus.
    """
    return {"BUY": OrderSide.BUY, "SELL": OrderSide.SELL}[value]


def map_order_type(value: str) -> OrderType:
    """
    Map a Kite order type to Nautilus.
    """
    return {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "SL": OrderType.STOP_LIMIT,
        "SL-M": OrderType.STOP_MARKET,
    }[value]


def map_time_in_force(value: str) -> TimeInForce:
    """
    Map a Kite validity to Nautilus.
    """
    return {"DAY": TimeInForce.DAY, "IOC": TimeInForce.IOC}[value]


def map_order_status(status: str, filled_quantity: int = 0) -> OrderStatus:
    """
    Map a Kite order status to Nautilus without treating pending states as final.
    """
    terminal = {
        "CANCELLED": OrderStatus.CANCELED,
        "COMPLETE": OrderStatus.FILLED,
        "REJECTED": OrderStatus.REJECTED,
    }
    if status in terminal:
        return terminal[status]
    if filled_quantity > 0:
        return OrderStatus.PARTIALLY_FILLED
    if status == "CANCEL PENDING":
        return OrderStatus.PENDING_CANCEL
    if status in {"MODIFY PENDING", "VALIDATION PENDING"}:
        return OrderStatus.PENDING_UPDATE
    return OrderStatus.ACCEPTED
