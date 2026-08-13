"""Live market-data provider (intentionally not configured).

This class exists so the architecture supports live feeds without touching
the pricing engine. It NEVER fabricates data: until a real integration is
configured (API keys via environment variables / Streamlit secrets, see
``.env.example``), it reports itself as unconfigured and refuses to
return prices.
"""

from __future__ import annotations

from data.base import (DataProvider, DataProviderNotConfiguredError,
                       MarketInputs)


class LiveMarketDataProvider(DataProvider):
    name = "Live API"

    def is_configured(self) -> bool:
        return False

    def get_market_inputs(self) -> MarketInputs:
        raise DataProviderNotConfiguredError(
            "Live data source: Not configured. No live market-data provider "
            "has been set up. Use Manual input or CSV upload, or configure a "
            "provider (see README: Data Providers)."
        )
