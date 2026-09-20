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
Define Zerodha Kite identity and endpoint constants.
"""

from nautilus_trader.model import Venue


ZERODHA = "ZERODHA"
ZERODHA_VENUE = Venue(ZERODHA)
KITE_HTTP_URL = "https://api.kite.trade"
KITE_WEBSOCKET_URL = "wss://ws.kite.trade"
KITE_SANDBOX_HTTP_URL = "https://api-sandbox.kite.trade"
KITE_SANDBOX_WEBSOCKET_URL = "wss://ws-sandbox.kite.trade"
