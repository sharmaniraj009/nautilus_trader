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
Stream Zerodha Kite quotes and trades into the Nautilus data engine.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import Any

from nautilus_trader.live.clients import MarketDataClient
from nautilus_trader.model import AggressorSide
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import QuoteTick
from nautilus_trader.model import TradeTick

from .common import datetime_to_ns
from .common import market_trade_id
from .common import price_from_value
from .common import quantity_from_value
from .config import ZerodhaDataClientConfig
from .providers import ZerodhaInstrumentProvider
from .sdk import _KiteTickerClient
from .sdk import new_kite_ticker


if TYPE_CHECKING:
    from nautilus_trader.live import SubscribeQuotes
    from nautilus_trader.live import SubscribeTrades
    from nautilus_trader.live import UnsubscribeQuotes
    from nautilus_trader.live import UnsubscribeTrades


class ZerodhaDataClient(MarketDataClient):
    """
    Provide live top-of-book quotes and trades from Kite WebSocket ticks.
    """

    def __init__(self, **kwargs: Any) -> None:
        """
        Retain subscription state without opening network resources.
        """
        super().__init__(**kwargs)
        self._ticker: _KiteTickerClient | None = None
        self._quote_tokens: set[int] = set()
        self._trade_tokens: set[int] = set()
        self._token_by_id: dict[object, int] = {}
        self._last_trade: dict[int, tuple[Price, Quantity, int]] = {}

    async def _connect(self) -> None:
        provider = self._provider()
        await provider.initialize()
        for instrument in provider.list_all():
            self._handle_instrument(instrument)
        self._token_by_id = {value: token for token, value in provider.ids_by_token.items()}

        config = self.config
        if not isinstance(config, ZerodhaDataClientConfig):
            raise TypeError("Zerodha data client requires ZerodhaDataClientConfig")
        ticker = new_kite_ticker(
            config.api_key,
            config.access_token,
            config.websocket_url,
        )
        ticker.on_connect = self._on_connect
        ticker.on_ticks = self._on_ticks
        ticker.on_reconnect = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect
        ticker.on_error = self._on_error
        ticker.connect(threaded=True)
        self._ticker = ticker

    async def _disconnect(self) -> None:
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            await asyncio.to_thread(ticker.close)

    async def _subscribe_quotes(self, command: SubscribeQuotes) -> None:
        token = self._lookup_token(command.instrument_id)
        self._quote_tokens.add(token)
        self._apply_subscription(token)

    async def _unsubscribe_quotes(self, command: UnsubscribeQuotes) -> None:
        token = self._lookup_token(command.instrument_id)
        self._quote_tokens.discard(token)
        self._apply_subscription(token)

    async def _subscribe_trades(self, command: SubscribeTrades) -> None:
        token = self._lookup_token(command.instrument_id)
        self._trade_tokens.add(token)
        self._apply_subscription(token)

    async def _unsubscribe_trades(self, command: UnsubscribeTrades) -> None:
        token = self._lookup_token(command.instrument_id)
        self._trade_tokens.discard(token)
        self._last_trade.pop(token, None)
        self._apply_subscription(token)

    def _provider(self) -> ZerodhaInstrumentProvider:
        provider = self.instrument_provider
        if not isinstance(provider, ZerodhaInstrumentProvider):
            raise TypeError("Zerodha data client requires its instrument provider")
        return provider

    def _lookup_token(self, value: object) -> int:
        try:
            return self._token_by_id[value]
        except KeyError as e:
            raise ValueError(f"Unknown Zerodha instrument: {value}") from e

    def _apply_subscription(self, token: int) -> None:
        ticker = self._ticker
        if ticker is None:
            raise RuntimeError("Zerodha market data WebSocket is not connected")
        if token not in self._quote_tokens and token not in self._trade_tokens:
            ticker.unsubscribe([token])
            return
        ticker.subscribe([token])
        ticker.set_mode(ticker.MODE_FULL, [token])

    def _on_connect(self, ticker: _KiteTickerClient, _response: object) -> None:
        tokens = sorted(self._quote_tokens | self._trade_tokens)
        if tokens:
            ticker.subscribe(tokens)
            ticker.set_mode(ticker.MODE_FULL, tokens)

    def _on_reconnect(self, _ticker: _KiteTickerClient, _attempts: int) -> None:
        pass

    def _on_noreconnect(self, _ticker: _KiteTickerClient) -> None:
        loop = self.loop
        if loop is not None:
            loop.call_soon_threadsafe(self.create_task, self._raise_disconnect(), "kite-disconnect")

    def _on_error(self, _ticker: _KiteTickerClient, code: int, reason: str) -> None:
        loop = self.loop
        if loop is not None:
            loop.call_soon_threadsafe(
                self.create_task,
                self._raise_stream_error(code, reason),
                "kite-stream-error",
            )

    async def _raise_disconnect(self) -> None:
        raise ConnectionError("Kite WebSocket exhausted its reconnect attempts")

    async def _raise_stream_error(self, code: int, reason: str) -> None:
        raise ConnectionError(f"Kite WebSocket error {code}: {reason}")

    def _on_ticks(self, _ticker: _KiteTickerClient, ticks: list[dict[str, Any]]) -> None:
        loop = self.loop
        if loop is not None:
            loop.call_soon_threadsafe(self._handle_ticks, ticks)

    def _handle_ticks(self, ticks: list[dict[str, Any]]) -> None:
        provider = self._provider()
        ids = provider.ids_by_token
        for tick in ticks:
            token = int(tick["instrument_token"])
            instrument_id = ids.get(token)
            if instrument_id is None:
                continue
            price_precision = provider.price_precision(token)
            now_ns = self.clock.timestamp_ns()
            ts_event = datetime_to_ns(tick.get("exchange_timestamp"), now_ns)
            if token in self._quote_tokens:
                depth = tick.get("depth") or {}
                buys = depth.get("buy") or []
                sells = depth.get("sell") or []
                if buys and sells:
                    self._handle_data(
                        QuoteTick(
                            instrument_id=instrument_id,
                            bid_price=price_from_value(buys[0]["price"], price_precision),
                            ask_price=price_from_value(sells[0]["price"], price_precision),
                            bid_size=quantity_from_value(buys[0]["quantity"]),
                            ask_size=quantity_from_value(sells[0]["quantity"]),
                            ts_event=ts_event,
                            ts_init=now_ns,
                        ),
                    )
            if token in self._trade_tokens and "last_price" in tick:
                price = price_from_value(tick["last_price"], price_precision)
                quantity = quantity_from_value(tick.get("last_quantity") or 0)
                trade_ts = datetime_to_ns(tick.get("last_trade_time"), ts_event)
                identity = (price, quantity, trade_ts)
                if quantity.is_zero() or self._last_trade.get(token) == identity:
                    continue
                self._last_trade[token] = identity
                self._handle_data(
                    TradeTick(
                        instrument_id=instrument_id,
                        price=price,
                        size=quantity,
                        aggressor_side=AggressorSide.NO_AGGRESSOR,
                        trade_id=market_trade_id(token, trade_ts, price, quantity),
                        ts_event=trade_ts,
                        ts_init=now_ns,
                    ),
                )
