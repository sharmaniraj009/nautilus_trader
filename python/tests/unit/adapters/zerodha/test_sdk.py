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
Test Kite SDK client construction for the sandbox endpoints.
"""

import pytest

from nautilus_trader.adapters.zerodha.constants import KITE_HTTP_URL
from nautilus_trader.adapters.zerodha.constants import KITE_SANDBOX_HTTP_URL
from nautilus_trader.adapters.zerodha.constants import KITE_SANDBOX_WEBSOCKET_URL
from nautilus_trader.adapters.zerodha.constants import KITE_WEBSOCKET_URL
from nautilus_trader.adapters.zerodha.sdk import new_kite_client
from nautilus_trader.adapters.zerodha.sdk import new_kite_ticker


pytest.importorskip("kiteconnect")


def test_sandbox_client_prefixes_routes_except_instrument_dumps() -> None:
    """
    The sandbox serves SDK routes under /oms, apart from the instrument dumps.
    """
    sandbox = new_kite_client("key", "token", KITE_SANDBOX_HTTP_URL)
    production = new_kite_client("key", "token", KITE_HTTP_URL)

    assert sandbox.root == KITE_SANDBOX_HTTP_URL
    assert sandbox._routes["order.place"] == "/oms/orders/{variety}"
    assert sandbox._routes["market.instruments.all"] == "/instruments"
    assert production._routes["order.place"] == "/orders/{variety}"


def test_sandbox_ticker_requires_and_appends_user_id() -> None:
    """
    The sandbox WebSocket URL must carry the user ID; production is left untouched.
    """
    with pytest.raises(ValueError, match="user_id"):
        new_kite_ticker("key", "token", KITE_SANDBOX_WEBSOCKET_URL)

    sandbox = new_kite_ticker("key", "token", KITE_SANDBOX_WEBSOCKET_URL, user_id="AB1234")
    production = new_kite_ticker("key", "token", KITE_WEBSOCKET_URL, user_id="AB1234")

    assert sandbox.socket_url.startswith(KITE_SANDBOX_WEBSOCKET_URL)
    assert sandbox.socket_url.endswith("&user_id=AB1234")
    assert "user_id" not in production.socket_url
