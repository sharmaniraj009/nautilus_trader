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
Load and translate Zerodha Kite instruments.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any
from typing import override

from nautilus_trader.live.providers import InstrumentProvider
from nautilus_trader.model import AssetClass
from nautilus_trader.model import Currency
from nautilus_trader.model import Equity
from nautilus_trader.model import FuturesContract
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import OptionContract
from nautilus_trader.model import OptionKind
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol

from .common import decimal_from_value
from .common import expiry_to_ns
from .common import instrument_id


if TYPE_CHECKING:
    from collections.abc import Callable

    from nautilus_trader.config import InstrumentProviderConfig


INR = Currency.from_str("INR")
_INDEX_NAMES = {"BANKEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"}


class ZerodhaInstrumentProvider(InstrumentProvider):
    """
    Load the daily Kite instrument master through a supplied blocking loader.
    """

    def __init__(
        self,
        config: InstrumentProviderConfig,
        loader: Callable[[], list[dict[str, Any]]],
    ) -> None:
        """
        Retain the provider configuration and blocking SDK loader.
        """
        super().__init__(config)
        self._loader = loader
        self._rows_by_id: dict[InstrumentId, dict[str, Any]] = {}
        self._ids_by_token: dict[int, InstrumentId] = {}
        self._precisions_by_token: dict[int, int] = {}

    @property
    def ids_by_token(self) -> dict[int, InstrumentId]:
        """
        Return a copy of the instrument-token lookup.
        """
        return self._ids_by_token.copy()

    def row(self, value: InstrumentId) -> dict[str, Any] | None:
        """
        Return a copy of the Kite instrument row for an ID.
        """
        row = self._rows_by_id.get(value)
        return None if row is None else row.copy()

    def price_precision(self, token: int) -> int:
        """
        Return the instrument-master price precision for a token.
        """
        try:
            return self._precisions_by_token[token]
        except KeyError as e:
            raise ValueError(f"Unknown Zerodha instrument token: {token}") from e

    @override
    async def load_all_async(self, filters: dict | None = None) -> None:
        """
        Load all supported instruments, optionally restricted by exchange.
        """
        rows = await asyncio.to_thread(self._loader)
        exchanges = None if not filters else filters.get("exchanges")
        allowed = None if exchanges is None else set(exchanges)
        now_ns = 0
        for row in rows:
            if allowed is not None and row["exchange"] not in allowed:
                continue
            instrument = parse_instrument(row, now_ns)
            if instrument is None:
                continue
            self.add(instrument)
            self._rows_by_id[instrument.id] = row.copy()
            token = int(row["instrument_token"])
            self._ids_by_token[token] = instrument.id
            self._precisions_by_token[token] = instrument.price_precision

    @override
    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        """
        Load only the requested instrument IDs from the daily master.
        """
        if not self._rows_by_id:
            await self.load_all_async(filters)
        missing = [value for value in instrument_ids if value not in self._rows_by_id]
        if missing:
            values = ", ".join(map(str, missing))
            raise ValueError(f"Kite instruments not found: {values}")

    @override
    async def load_async(self, instrument_id: InstrumentId, filters: dict | None = None) -> None:
        """
        Load one requested instrument ID from the daily master.
        """
        await self.load_ids_async([instrument_id], filters)


def parse_instrument(
    row: dict[str, Any],
    ts_init: int,
) -> Equity | FuturesContract | OptionContract | None:
    """
    Parse one Kite instrument-master row into a Nautilus instrument.
    """
    exchange = str(row["exchange"])
    tradingsymbol = str(row["tradingsymbol"])
    instrument_type = str(row["instrument_type"])
    tick_size = decimal_from_value(row["tick_size"])
    lot_size = int(row["lot_size"])
    if tick_size <= 0 or lot_size <= 0:
        return None

    parsed_id = instrument_id(exchange, tradingsymbol)
    raw_symbol = Symbol(tradingsymbol)
    exponent = tick_size.as_tuple().exponent
    if not isinstance(exponent, int):
        return None
    price_precision = max(0, -exponent)
    price_increment = Price.from_decimal(tick_size)
    info = {
        "exchange": exchange,
        "instrument_token": int(row["instrument_token"]),
        "segment": str(row["segment"]),
    }
    if instrument_type == "EQ":
        return Equity(
            instrument_id=parsed_id,
            raw_symbol=raw_symbol,
            currency=INR,
            price_precision=price_precision,
            price_increment=price_increment,
            ts_event=ts_init,
            ts_init=ts_init,
            lot_size=Quantity.from_int(lot_size),
            info=info,
        )

    expiry = row.get("expiry")
    if instrument_type not in {"FUT", "CE", "PE"} or not expiry:
        return None
    underlying = str(row.get("name") or tradingsymbol)
    if underlying.upper() in _INDEX_NAMES:
        asset_class = AssetClass.INDEX
    elif exchange in {"CDS", "BCD"}:
        asset_class = AssetClass.FX
    elif exchange == "MCX":
        asset_class = AssetClass.COMMODITY
    else:
        asset_class = AssetClass.EQUITY
    expiration_ns = expiry_to_ns(expiry, exchange)
    multiplier = Quantity.from_int(1)
    parsed_lot_size = Quantity.from_int(lot_size)
    if instrument_type == "FUT":
        return FuturesContract(
            instrument_id=parsed_id,
            raw_symbol=raw_symbol,
            asset_class=asset_class,
            underlying=underlying,
            activation_ns=0,
            expiration_ns=expiration_ns,
            currency=INR,
            price_precision=price_precision,
            price_increment=price_increment,
            # Kite quantities are underlying units, so lot size constrains order quantity and
            # must not also multiply notional value.
            multiplier=multiplier,
            lot_size=parsed_lot_size,
            ts_event=ts_init,
            ts_init=ts_init,
            exchange=exchange,
            info=info,
        )
    return OptionContract(
        instrument_id=parsed_id,
        raw_symbol=raw_symbol,
        asset_class=asset_class,
        underlying=underlying,
        option_kind=OptionKind.CALL if instrument_type == "CE" else OptionKind.PUT,
        strike_price=Price.from_decimal(Decimal(str(row["strike"]))),
        currency=INR,
        activation_ns=0,
        expiration_ns=expiration_ns,
        price_precision=price_precision,
        price_increment=price_increment,
        multiplier=multiplier,
        lot_size=parsed_lot_size,
        ts_event=ts_init,
        ts_init=ts_init,
        exchange=exchange,
        info=info,
    )
