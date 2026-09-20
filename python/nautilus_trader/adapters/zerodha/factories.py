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
Construct Zerodha Kite clients for a live node.
"""

from typing import Any

from nautilus_trader.common import Clock
from nautilus_trader.config import DataClientConfig
from nautilus_trader.config import ExecutionClientConfig
from nautilus_trader.live import ClientCache
from nautilus_trader.live.clients import DataClientFactory
from nautilus_trader.live.clients import ExecutionClientFactory
from nautilus_trader.model import AccountId
from nautilus_trader.model import AccountType
from nautilus_trader.model import OmsType
from nautilus_trader.model import TraderId

from .config import ZerodhaDataClientConfig
from .config import ZerodhaExecutionClientConfig
from .constants import ZERODHA_VENUE
from .data import ZerodhaDataClient
from .execution import ZerodhaExecutionClient
from .providers import INR
from .providers import ZerodhaInstrumentProvider
from .sdk import new_kite_client


class ZerodhaDataClientFactory(DataClientFactory):
    """
    Construct Zerodha Kite data clients.
    """

    @staticmethod
    def create(
        *,
        name: str,
        config: DataClientConfig,
        cache: ClientCache,
        clock: Clock,
    ) -> ZerodhaDataClient:
        """
        Construct a client without opening network resources.
        """
        if not isinstance(config, ZerodhaDataClientConfig):
            raise TypeError("Expected ZerodhaDataClientConfig")

        def load_instruments() -> list[dict[str, Any]]:
            client = new_kite_client(config.api_key, config.access_token, config.http_url)
            return client.instruments()

        provider = ZerodhaInstrumentProvider(config.instrument_provider, load_instruments)
        return ZerodhaDataClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            venue=ZERODHA_VENUE,
            instrument_provider=provider,
        )


class ZerodhaExecutionClientFactory(ExecutionClientFactory):
    """
    Construct Zerodha Kite execution clients.
    """

    @staticmethod
    def create(
        *,
        name: str,
        config: ExecutionClientConfig,
        cache: ClientCache,
        clock: Clock,
        trader_id: TraderId,
    ) -> ZerodhaExecutionClient:
        """
        Construct a client without opening network resources.
        """
        if not isinstance(config, ZerodhaExecutionClientConfig):
            raise TypeError("Expected ZerodhaExecutionClientConfig")

        def load_instruments() -> list[dict[str, Any]]:
            client = new_kite_client(config.api_key, config.access_token, config.http_url)
            return client.instruments()

        provider = ZerodhaInstrumentProvider(config.instrument_provider, load_instruments)
        return ZerodhaExecutionClient(
            name=name,
            config=config,
            cache=cache,
            clock=clock,
            trader_id=trader_id,
            venue=ZERODHA_VENUE,
            account_id=AccountId(f"ZERODHA-{config.account_id}"),
            account_type=AccountType.MARGIN,
            oms_type=OmsType.NETTING,
            base_currency=INR,
            instrument_provider=provider,
        )
