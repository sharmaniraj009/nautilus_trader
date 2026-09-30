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
Create official Kite SDK clients without importing the optional dependency at package import.
"""

from collections.abc import Callable
from importlib import import_module
from typing import Any
from typing import Protocol
from typing import cast

from .constants import KITE_HTTP_URL
from .constants import KITE_SANDBOX_HTTP_URL
from .constants import KITE_SANDBOX_WEBSOCKET_URL
from .constants import KITE_WEBSOCKET_URL


# The sandbox serves every SDK route under /oms except the instrument dumps.
_SANDBOX_ROUTE_PASSTHROUGH = frozenset({"market.instruments.all", "market.instruments"})


class _KiteClient(Protocol):
    """
    Describe the official REST SDK surface used by the adapter.
    """

    def set_access_token(self, access_token: str) -> None: ...
    def instruments(self) -> list[dict[str, Any]]: ...
    def margins(self) -> dict[str, dict[str, Any]]: ...
    def orders(self) -> list[dict[str, Any]]: ...
    def trades(self) -> list[dict[str, Any]]: ...
    def positions(self) -> dict[str, list[dict[str, Any]]]: ...
    def order_trades(self, order_id: str) -> list[dict[str, Any]]: ...
    def place_order(self, **kwargs: object) -> str: ...
    def modify_order(self, **kwargs: object) -> str: ...
    def cancel_order(self, **kwargs: object) -> str: ...


class _KiteTickerClient(Protocol):
    """
    Describe the official WebSocket SDK surface used by the adapter.
    """

    MODE_FULL: str
    on_connect: Callable[..., None]
    on_ticks: Callable[..., None]
    on_reconnect: Callable[..., None]
    on_noreconnect: Callable[..., None]
    on_error: Callable[..., None]
    on_order_update: Callable[..., None]

    def connect(self, threaded: bool = False) -> None: ...  # noqa: FBT001, FBT002
    def close(self) -> None: ...
    def subscribe(self, instrument_tokens: list[int]) -> bool: ...
    def unsubscribe(self, instrument_tokens: list[int]) -> bool: ...
    def set_mode(self, mode: str, instrument_tokens: list[int]) -> bool: ...


def new_kite_client(api_key: str, access_token: str, http_url: str) -> _KiteClient:
    """
    Construct an authenticated official Kite REST client.
    """
    try:
        kiteconnect = import_module("kiteconnect")
    except ImportError as e:
        raise RuntimeError(
            "The Zerodha adapter requires the 'kiteconnect' package; install the zerodha extra",
        ) from e
    root = None if http_url == KITE_HTTP_URL else http_url
    client = kiteconnect.KiteConnect(api_key=api_key, access_token=access_token, root=root)
    if http_url == KITE_SANDBOX_HTTP_URL:
        # The SDK has no public route hook; the Kite sandbox guide prescribes this rewrite.
        client._routes = {  # noqa: SLF001
            key: value if key in _SANDBOX_ROUTE_PASSTHROUGH else f"/oms{value}"
            for key, value in client._routes.items()  # noqa: SLF001
        }
    client.set_access_token(access_token)
    return cast("_KiteClient", client)


def new_kite_ticker(
    api_key: str,
    access_token: str,
    websocket_url: str,
    *,
    user_id: str | None = None,
    reconnect: bool = True,
) -> _KiteTickerClient:
    """
    Construct an authenticated official Kite WebSocket client.

    The sandbox WebSocket requires ``user_id`` in its connection URL.
    """
    try:
        kiteconnect = import_module("kiteconnect")
    except ImportError as e:
        raise RuntimeError(
            "The Zerodha adapter requires the 'kiteconnect' package; install the zerodha extra",
        ) from e
    root = None if websocket_url == KITE_WEBSOCKET_URL else websocket_url
    ticker = kiteconnect.KiteTicker(api_key, access_token, root=root, reconnect=reconnect)
    if websocket_url == KITE_SANDBOX_WEBSOCKET_URL:
        if not user_id:
            raise ValueError("The Kite sandbox WebSocket requires user_id")
        ticker.socket_url = f"{ticker.socket_url}&user_id={user_id}"
    return cast("_KiteTickerClient", ticker)
