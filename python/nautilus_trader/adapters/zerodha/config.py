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
Define configuration for Zerodha Kite clients.
"""

from typing import Literal

from nautilus_trader.config import DataClientConfig
from nautilus_trader.config import ExecutionClientConfig

from .constants import KITE_HTTP_URL
from .constants import KITE_WEBSOCKET_URL


KiteProduct = Literal["CNC", "MIS", "NRML"]


class ZerodhaDataClientConfig(DataClientConfig):
    """
    Configure a Zerodha Kite market data client.
    """

    api_key: str
    access_token: str
    http_url: str
    websocket_url: str

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        http_url: str = KITE_HTTP_URL,
        websocket_url: str = KITE_WEBSOCKET_URL,
        **_kwargs: object,
    ) -> None:
        """
        Retain credentials and endpoint configuration without opening network resources.
        """
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not access_token:
            raise ValueError("access_token must not be empty")
        self.api_key = api_key
        self.access_token = access_token
        self.http_url = http_url.rstrip("/")
        self.websocket_url = websocket_url


class ZerodhaExecutionClientConfig(ExecutionClientConfig):
    """
    Configure a Zerodha Kite execution client.
    """

    api_key: str
    access_token: str
    account_id: str
    product: KiteProduct
    http_url: str
    websocket_url: str

    def __init__(  # noqa: PLR0913 - Preserve explicit venue configuration fields.
        self,
        *,
        api_key: str,
        access_token: str,
        account_id: str,
        product: KiteProduct = "MIS",
        http_url: str = KITE_HTTP_URL,
        websocket_url: str = KITE_WEBSOCKET_URL,
        **_kwargs: object,
    ) -> None:
        """
        Retain credentials and execution defaults without opening network resources.
        """
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not access_token:
            raise ValueError("access_token must not be empty")
        if not account_id:
            raise ValueError("account_id must not be empty")
        if product not in {"CNC", "MIS", "NRML"}:
            raise ValueError("product must be CNC, MIS, or NRML")
        self.api_key = api_key
        self.access_token = access_token
        self.account_id = account_id
        self.product = product
        self.http_url = http_url.rstrip("/")
        self.websocket_url = websocket_url
