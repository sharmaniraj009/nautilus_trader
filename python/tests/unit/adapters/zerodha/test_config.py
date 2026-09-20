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
Test Zerodha adapter configuration.
"""

from typing import Any
from typing import cast

import pytest

from nautilus_trader.adapters.zerodha import ZerodhaDataClientConfig
from nautilus_trader.adapters.zerodha import ZerodhaExecutionClientConfig
from nautilus_trader.config import InstrumentProviderConfig


def test_data_config_round_trip_preserves_credentials_and_common_fields() -> None:
    """
    Importable config round trips preserve adapter and provider settings.
    """
    config = ZerodhaDataClientConfig(
        api_key="key",
        access_token="token",
        instrument_provider=InstrumentProviderConfig(
            load_all=True,
            filters={"exchanges": ["NSE", "NFO"]},
        ),
    )

    restored = config.to_importable().create()

    assert type(restored) is ZerodhaDataClientConfig
    assert restored.api_key == "key"
    assert restored.access_token == "token"
    assert restored.instrument_provider.filters == {"exchanges": ["NSE", "NFO"]}


def test_execution_config_rejects_unknown_product() -> None:
    """
    Unsupported product semantics fail during configuration.
    """
    with pytest.raises(ValueError, match="product must be"):
        ZerodhaExecutionClientConfig(
            api_key="key",
            access_token="token",
            account_id="AB1234",
            product=cast("Any", "UNKNOWN"),
        )
