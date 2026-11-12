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
Execute and reconcile orders through Zerodha Kite.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from nautilus_trader.live.clients import ExecutionClient
from nautilus_trader.model import AccountBalance
from nautilus_trader.model import ClientOrderId
from nautilus_trader.model import FillReport
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import LiquiditySide
from nautilus_trader.model import Money
from nautilus_trader.model import OrderSide
from nautilus_trader.model import OrderStatus
from nautilus_trader.model import OrderStatusReport
from nautilus_trader.model import OrderType
from nautilus_trader.model import PositionSide
from nautilus_trader.model import PositionStatusReport
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import TradeId
from nautilus_trader.model import VenueOrderId

from .common import datetime_to_ns
from .common import decimal_from_value
from .common import instrument_id
from .common import kite_tag
from .common import map_order_side
from .common import map_order_status
from .common import map_order_type
from .common import map_time_in_force
from .common import price_from_value
from .common import quantity_from_value
from .common import split_instrument_id
from .config import ZerodhaExecutionClientConfig
from .providers import INR
from .providers import ZerodhaInstrumentProvider
from .sdk import _KiteClient
from .sdk import _KiteTickerClient
from .sdk import new_kite_client
from .sdk import new_kite_ticker


if TYPE_CHECKING:
    from nautilus_trader.live import CancelAllOrders
    from nautilus_trader.live import CancelOrder
    from nautilus_trader.live import GenerateFillReports
    from nautilus_trader.live import GenerateOrderStatusReport
    from nautilus_trader.live import GenerateOrderStatusReports
    from nautilus_trader.live import GeneratePositionStatusReports
    from nautilus_trader.live import ModifyOrder
    from nautilus_trader.live import QueryAccount
    from nautilus_trader.live import QueryOrder
    from nautilus_trader.live import SubmitOrder
    from nautilus_trader.live import SubmitOrderList


_OPEN_STATUSES = {
    "AMO REQ RECEIVED",
    "MODIFY VALIDATION PENDING",
    "MODIFY PENDING",
    "NOT MODIFIED",
    "OPEN",
    "OPEN PENDING",
    "PUT ORDER REQ RECEIVED",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
}
_TERMINAL_STATUSES = {"CANCELLED", "COMPLETE", "REJECTED"}


class _Order(Protocol):
    """
    Describe the order fields required by the Kite execution boundary.

    ``price``, ``trigger_price`` and ``is_post_only`` exist only on the order types they
    apply to (a ``MarketOrder`` has none of them), so they are read with ``getattr``.
    """

    instrument_id: InstrumentId
    client_order_id: ClientOrderId
    venue_order_id: VenueOrderId | None
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: Quantity
    status: OrderStatus
    is_reduce_only: bool
    is_quote_quantity: bool


class ZerodhaExecutionClient(ExecutionClient):
    """
    Route regular Kite orders and reconcile the broker's daily order book.
    """

    def __init__(self, **kwargs: Any) -> None:
        """
        Retain execution state without opening network resources.
        """
        super().__init__(**kwargs)
        self._kite: _KiteClient | None = None
        self._ticker: _KiteTickerClient | None = None
        self._accepted_venue_order_ids: set[str] = set()
        self._seen_trade_ids: set[str] = set()

    async def _connect(self) -> None:
        config = self.config
        if not isinstance(config, ZerodhaExecutionClientConfig):
            raise TypeError("Zerodha execution client requires ZerodhaExecutionClientConfig")
        self._kite = new_kite_client(config.api_key, config.access_token, config.http_url)
        provider = self._provider()
        await provider.initialize()
        for instrument in provider.list_all():
            self._handle_instrument(instrument)

        ticker = new_kite_ticker(
            config.api_key,
            config.access_token,
            config.websocket_url,
            user_id=config.account_id,
        )
        ticker.on_order_update = self._on_order_update
        ticker.on_noreconnect = self._on_noreconnect
        ticker.on_error = self._on_error
        ticker.connect(threaded=True)
        self._ticker = ticker
        await self._refresh_account()

    async def _disconnect(self) -> None:
        ticker, self._ticker = self._ticker, None
        self._kite = None
        if ticker is not None:
            await asyncio.to_thread(ticker.close)

    async def _submit_order(self, command: SubmitOrder) -> None:
        await self._submit(command.order)

    async def _submit(self, order: _Order) -> None:
        error = self._validate_order(order)
        if error is not None:
            self.generate_order_denied(order, error)
            return
        exchange, tradingsymbol = split_instrument_id(order.instrument_id)
        params: dict[str, Any] = {
            "variety": "regular",
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": "BUY" if order.side == OrderSide.BUY else "SELL",
            "quantity": int(order.quantity.as_decimal()),
            "product": self._config().product,
            "order_type": self._to_kite_order_type(order.order_type),
            "validity": "IOC" if order.time_in_force == TimeInForce.IOC else "DAY",
            "tag": kite_tag(order.client_order_id),
        }
        price = getattr(order, "price", None)
        trigger_price = getattr(order, "trigger_price", None)
        if price is not None:
            params["price"] = str(price.as_decimal())
        if trigger_price is not None:
            params["trigger_price"] = str(trigger_price.as_decimal())
        self.generate_order_submitted(order)
        try:
            venue_order_id = await asyncio.to_thread(self._api().place_order, **params)
        except Exception as e:  # noqa: BLE001 - Translate all SDK request failures to venue events.
            self.generate_order_rejected(
                order,
                str(e),
                self.clock.timestamp_ns(),
                due_post_only=False,
            )
            return
        venue_order_key = str(venue_order_id)
        if venue_order_key in self._accepted_venue_order_ids:
            return
        self._accepted_venue_order_ids.add(venue_order_key)
        self.generate_order_accepted(
            order,
            VenueOrderId(str(venue_order_id)),
            self.clock.timestamp_ns(),
        )

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        for initialized in command.order_inits:
            order = self.cache.order(initialized.client_order_id)
            if order is None:
                raise RuntimeError(f"Order {initialized.client_order_id} is missing from the cache")
            await self._submit(order)

    async def _modify_order(self, command: ModifyOrder) -> None:
        order = self.cache.order(command.client_order_id)
        if order is None:
            raise RuntimeError(f"Order {command.client_order_id} is missing from the cache")
        venue_order_id = command.venue_order_id or order.venue_order_id
        if venue_order_id is None:
            self.generate_order_modify_rejected(
                order,
                None,
                "Order has no Zerodha venue order ID",
                self.clock.timestamp_ns(),
            )
            return
        params: dict[str, Any] = {}
        if command.quantity is not None:
            params["quantity"] = int(command.quantity.as_decimal())
        if command.price is not None:
            params["price"] = str(command.price.as_decimal())
        if command.trigger_price is not None:
            params["trigger_price"] = str(command.trigger_price.as_decimal())
        try:
            await asyncio.to_thread(
                self._api().modify_order,
                variety="regular",
                order_id=str(venue_order_id),
                **params,
            )
        except Exception as e:  # noqa: BLE001 - Translate all SDK request failures to venue events.
            self.generate_order_modify_rejected(
                order,
                venue_order_id,
                str(e),
                self.clock.timestamp_ns(),
            )

    async def _cancel_order(self, command: CancelOrder) -> None:
        order = self.cache.order(command.client_order_id)
        if order is None:
            raise RuntimeError(f"Order {command.client_order_id} is missing from the cache")
        venue_order_id = command.venue_order_id or order.venue_order_id
        if venue_order_id is None:
            self.generate_order_cancel_rejected(
                order,
                None,
                "Order has no Zerodha venue order ID",
                self.clock.timestamp_ns(),
            )
            return
        try:
            await asyncio.to_thread(
                self._api().cancel_order,
                variety="regular",
                order_id=str(venue_order_id),
            )
        except Exception as e:  # noqa: BLE001 - Translate all SDK request failures to venue events.
            self.generate_order_cancel_rejected(
                order,
                venue_order_id,
                str(e),
                self.clock.timestamp_ns(),
            )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        orders = await asyncio.to_thread(self._api().orders)
        for row in orders:
            if row["status"] not in _OPEN_STATUSES:
                continue
            row_id = instrument_id(row["exchange"], row["tradingsymbol"])
            if row_id != command.instrument_id:
                continue
            if (
                command.order_side is not None
                and map_order_side(row["transaction_type"]) != command.order_side
            ):
                continue
            await asyncio.to_thread(
                self._api().cancel_order,
                variety=row.get("variety") or "regular",
                order_id=str(row["order_id"]),
            )

    async def _query_account(self, command: QueryAccount) -> None:  # noqa: ARG002
        await self._refresh_account()

    async def _query_order(self, command: QueryOrder) -> None:
        report = await self._find_order_report(
            command.venue_order_id,
            command.client_order_id,
        )
        if report is not None:
            self._handle_report(report)

    async def _generate_order_status_report(
        self,
        command: GenerateOrderStatusReport,
    ) -> OrderStatusReport | None:
        return await self._find_order_report(command.venue_order_id, command.client_order_id)

    async def _generate_order_status_reports(
        self,
        command: GenerateOrderStatusReports,
    ) -> list[OrderStatusReport]:
        rows = await asyncio.to_thread(self._api().orders)
        reports = [self._parse_order_report(row) for row in rows]
        if command.open_only:
            reports = [
                report
                for report in reports
                if report.order_status
                not in {
                    OrderStatus.CANCELED,
                    OrderStatus.FILLED,
                    OrderStatus.REJECTED,
                }
            ]
        if command.instrument_id is not None:
            reports = [
                report for report in reports if report.instrument_id == command.instrument_id
            ]
        return reports

    async def _generate_fill_reports(self, command: GenerateFillReports) -> list[FillReport]:
        rows = await asyncio.to_thread(self._api().trades)
        reports = [self._parse_fill_report(row) for row in rows]
        self._seen_trade_ids.update(str(report.trade_id) for report in reports)
        if command.instrument_id is not None:
            reports = [
                report for report in reports if report.instrument_id == command.instrument_id
            ]
        if command.venue_order_id is not None:
            reports = [
                report for report in reports if report.venue_order_id == command.venue_order_id
            ]
        return reports

    async def _generate_position_status_reports(
        self,
        command: GeneratePositionStatusReports,
    ) -> list[PositionStatusReport]:
        positions = await asyncio.to_thread(self._api().positions)
        now_ns = self.clock.timestamp_ns()
        reports = []
        for row in positions["net"]:
            quantity = int(row["quantity"])
            row_id = instrument_id(row["exchange"], row["tradingsymbol"])
            if command.instrument_id is not None and row_id != command.instrument_id:
                continue
            reports.append(
                PositionStatusReport(
                    account_id=self.account_id,
                    instrument_id=row_id,
                    position_side=(
                        PositionSide.LONG
                        if quantity > 0
                        else PositionSide.SHORT
                        if quantity < 0
                        else PositionSide.FLAT
                    ),
                    quantity=Quantity.from_int(abs(quantity)),
                    ts_last=now_ns,
                    ts_init=now_ns,
                    avg_px_open=decimal_from_value(row["average_price"]),
                ),
            )
        return reports

    def _api(self) -> _KiteClient:
        if self._kite is None:
            raise RuntimeError("Zerodha REST client is not connected")
        return self._kite

    def _config(self) -> ZerodhaExecutionClientConfig:
        config = self.config
        if not isinstance(config, ZerodhaExecutionClientConfig):
            raise TypeError("Zerodha execution client requires ZerodhaExecutionClientConfig")
        return config

    def _provider(self) -> ZerodhaInstrumentProvider:
        provider = self.instrument_provider
        if not isinstance(provider, ZerodhaInstrumentProvider):
            raise TypeError("Zerodha execution client requires its instrument provider")
        return provider

    def _validate_order(self, order: _Order) -> str | None:
        checks = [
            (
                order.order_type
                not in {
                    OrderType.MARKET,
                    OrderType.LIMIT,
                    OrderType.STOP_MARKET,
                    OrderType.STOP_LIMIT,
                },
                f"Unsupported Zerodha order type: {order.order_type}",
            ),
            (
                order.time_in_force not in {TimeInForce.DAY, TimeInForce.IOC},
                f"Unsupported Zerodha time in force: {order.time_in_force}",
            ),
            (
                order.quantity.as_decimal() != order.quantity.as_decimal().to_integral_value(),
                "Zerodha requires an integral order quantity",
            ),
            (getattr(order, "is_post_only", False), "Zerodha does not support post-only orders"),
            (order.is_reduce_only, "Zerodha does not support atomic reduce-only orders"),
            (order.is_quote_quantity, "Zerodha requires orders sized in instrument units"),
        ]
        return next((message for invalid, message in checks if invalid), None)

    @staticmethod
    def _to_kite_order_type(order_type: OrderType) -> str:
        return {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.STOP_MARKET: "SL-M",
            OrderType.STOP_LIMIT: "SL",
        }[order_type]

    async def _refresh_account(self) -> None:
        margins = await asyncio.to_thread(self._api().margins)
        free = Decimal(0)
        locked = Decimal(0)
        for segment in margins.values():
            if not segment.get("enabled", True):
                continue
            free += decimal_from_value(segment.get("net", 0))
            locked += decimal_from_value(segment.get("utilised", {}).get("debits", 0))
        self.generate_account_state(
            balances=[
                AccountBalance(
                    Money.from_decimal(free + locked, INR),
                    Money.from_decimal(locked, INR),
                    Money.from_decimal(free, INR),
                ),
            ],
            margins=[],
            reported=True,
            ts_event=self.clock.timestamp_ns(),
            info={"segments": margins},
        )

    async def _find_order_report(
        self,
        venue_order_id: VenueOrderId | None,
        client_order_id: ClientOrderId | None,
    ) -> OrderStatusReport | None:
        rows = await asyncio.to_thread(self._api().orders)
        for row in rows:
            if venue_order_id is not None and str(row["order_id"]) == str(venue_order_id):
                return self._parse_order_report(row)
            if client_order_id is not None and row.get("tag") == kite_tag(client_order_id):
                return self._parse_order_report(row)
        return None

    def _parse_order_report(self, row: dict[str, Any]) -> OrderStatusReport:
        now_ns = self.clock.timestamp_ns()
        accepted_ns = datetime_to_ns(row.get("order_timestamp"), now_ns)
        last_ns = datetime_to_ns(
            row.get("exchange_update_timestamp") or row.get("exchange_timestamp"),
            accepted_ns,
        )
        client_order_id = self._resolve_client_order_id(row.get("tag"))
        price = decimal_from_value(row.get("price") or 0)
        trigger = decimal_from_value(row.get("trigger_price") or 0)
        average = decimal_from_value(row.get("average_price") or 0)
        return OrderStatusReport(
            account_id=self.account_id,
            instrument_id=instrument_id(row["exchange"], row["tradingsymbol"]),
            venue_order_id=VenueOrderId(str(row["order_id"])),
            order_side=map_order_side(row["transaction_type"]),
            order_type=map_order_type(row["order_type"]),
            time_in_force=map_time_in_force(row.get("validity") or "DAY"),
            order_status=map_order_status(row["status"], int(row.get("filled_quantity") or 0)),
            quantity=quantity_from_value(row["quantity"]),
            filled_qty=quantity_from_value(row.get("filled_quantity") or 0),
            ts_accepted=accepted_ns,
            ts_last=last_ns,
            ts_init=now_ns,
            client_order_id=client_order_id,
            price=None if price.is_zero() else Price.from_decimal(price),
            trigger_price=None if trigger.is_zero() else Price.from_decimal(trigger),
            avg_px=None if average.is_zero() else average,
            cancel_reason=row.get("status_message") or row.get("status_message_raw"),
        )

    def _parse_fill_report(self, row: dict[str, Any]) -> FillReport:
        now_ns = self.clock.timestamp_ns()
        ts_event = datetime_to_ns(row.get("fill_timestamp"), now_ns)
        venue_order_id = VenueOrderId(str(row["order_id"]))
        client_order_id = self.cache.client_order_id(venue_order_id)
        price = row.get("average_price") or row.get("price")
        if price is None:
            raise ValueError(f"Kite trade {row.get('trade_id')} has no fill price")
        return FillReport(
            account_id=self.account_id,
            instrument_id=instrument_id(row["exchange"], row["tradingsymbol"]),
            venue_order_id=venue_order_id,
            trade_id=TradeId(str(row["trade_id"])),
            order_side=map_order_side(row["transaction_type"]),
            last_qty=quantity_from_value(row.get("quantity") or row.get("filled") or 0),
            last_px=price_from_value(price),
            commission=Money.zero(INR),
            liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
            ts_event=ts_event,
            ts_init=now_ns,
            client_order_id=client_order_id,
        )

    def _resolve_client_order_id(self, tag: str | None) -> ClientOrderId | None:
        if not tag:
            return None
        for order in self.cache.orders(venue=self.venue):
            if kite_tag(order.client_order_id) == tag:
                return order.client_order_id
        return None

    def _on_order_update(self, _ticker: _KiteTickerClient, row: dict[str, Any]) -> None:
        loop = self.loop
        if loop is not None:
            loop.call_soon_threadsafe(
                self.create_task,
                self._handle_order_update(row),
                "kite-order-update",
            )

    async def _handle_order_update(self, row: dict[str, Any]) -> None:
        client_order_id = self._resolve_client_order_id(row.get("tag"))
        if client_order_id is None:
            return
        order = self.cache.order(client_order_id)
        if order is None:
            return
        venue_order_id = VenueOrderId(str(row["order_id"]))
        now_ns = self.clock.timestamp_ns()
        ts_event = datetime_to_ns(
            row.get("exchange_update_timestamp") or row.get("exchange_timestamp"),
            now_ns,
        )
        status = str(row["status"])
        if status == "REJECTED":
            self._accepted_venue_order_ids.add(str(venue_order_id))
            if order.status not in {OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.FILLED}:
                reason = row.get("status_message") or row.get("status_message_raw") or "Rejected"
                self.generate_order_rejected(order, reason, ts_event, due_post_only=False)
            return
        if str(venue_order_id) not in self._accepted_venue_order_ids:
            self._accepted_venue_order_ids.add(str(venue_order_id))
            self.generate_order_accepted(order, venue_order_id, ts_event)
        if order.status == OrderStatus.PENDING_UPDATE and status in _OPEN_STATUSES:
            price = decimal_from_value(row.get("price") or 0)
            trigger = decimal_from_value(row.get("trigger_price") or 0)
            self.generate_order_updated(
                order=order,
                venue_order_id=venue_order_id,
                quantity=quantity_from_value(row["quantity"]),
                price=None if price.is_zero() else Price.from_decimal(price),
                trigger_price=None if trigger.is_zero() else Price.from_decimal(trigger),
                protection_price=None,
                ts_event=ts_event,
            )
        if int(row.get("filled_quantity") or 0) > 0:
            await self._emit_new_fills(order, venue_order_id, ts_event)
        if status == "CANCELLED" and order.status != OrderStatus.CANCELED:
            self.generate_order_canceled(order, venue_order_id, ts_event)

    async def _emit_new_fills(
        self,
        order: _Order,
        venue_order_id: VenueOrderId,
        fallback_ns: int,
    ) -> None:
        trades = await asyncio.to_thread(self._api().order_trades, str(venue_order_id))
        for trade in trades:
            trade_id = str(trade["trade_id"])
            if trade_id in self._seen_trade_ids:
                continue
            self._seen_trade_ids.add(trade_id)
            instrument = self.cache.instrument(order.instrument_id)
            currency = instrument.quote_currency if instrument is not None else INR
            quantity = trade.get("quantity") or trade.get("filled")
            price = trade.get("average_price") or trade.get("price")
            if quantity is None or price is None:
                raise ValueError(f"Kite trade {trade_id} has incomplete fill values")
            self.generate_order_filled(
                order=order,
                venue_order_id=venue_order_id,
                venue_position_id=None,
                trade_id=TradeId(trade_id),
                last_qty=quantity_from_value(quantity),
                last_px=price_from_value(price),
                quote_currency=currency,
                commission=None,
                liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
                ts_event=datetime_to_ns(trade.get("fill_timestamp"), fallback_ns),
            )

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
        raise ConnectionError("Kite execution WebSocket exhausted its reconnect attempts")

    async def _raise_stream_error(self, code: int, reason: str) -> None:
        raise ConnectionError(f"Kite execution WebSocket error {code}: {reason}")
