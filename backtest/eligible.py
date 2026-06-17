#!/usr/bin/env python3
"""
Backtest token universe = tokens eligible for the BNB Hack competition (149 BEP-20
on CoinMarketCap) that have enough 1H OHLCV history to validate over 2 years.

Eligible list source: DoraHacks competition detail (fixed list of 149 tokens).
Stablecoins and tokens without sufficient history are dropped from the backtest
(still tradable live, but there is no breakout edge to measure).
"""

# Eligible tokens with adequate 1H OHLCV history (subset of the 149 official list).
BACKTEST_UNIVERSE = [
    "0G", "1INCH", "AAVE", "ACH", "ADA", "APE", "APR", "ASTER", "ATOM", "AVAX",
    "AXS", "BARD", "BAT", "BCH", "BEAT", "BILL", "BONK", "BRETT", "BSB", "COAI",
    "COMP", "DOGE", "DOT", "EDGE", "ETC", "ETH", "FIL", "FLOKI", "H", "HOME",
    "HUMA", "INJ", "IP", "IRYS", "KITE", "LAB", "LDO", "LINK", "LTC", "NIGHT",
    "PENDLE", "PENGU", "PIEVERSE", "PLUME", "RAVE", "RAY", "RIVER", "SAHARA",
    "SHIB", "SLX", "SNX", "SUSHI", "TRIA", "TRX", "UB", "UNI",
    "USDC", "WLFI", "XPL", "XRP", "YFI", "ZAMA", "ZEC", "ZETA", "ZIL", "ZRO",
]

# Liquid subset likely to have >=2 years of data (priority for the initial backtest).
# New/exotic tokens are deferred; the stablecoin (USDC) is dropped.
LIQUID_CORE = [
    "ETH", "XRP", "TRX", "DOGE", "ADA", "LINK", "BCH", "LTC", "AVAX", "DOT",
    "UNI", "ETC", "AAVE", "ATOM", "FIL", "INJ", "SHIB", "COMP", "SNX", "SUSHI",
    "YFI", "1INCH", "ZIL", "APE", "LDO", "ZEC", "BAT", "FLOKI", "ACH",
]


if __name__ == "__main__":
    print(f"Backtest universe: {len(BACKTEST_UNIVERSE)}")
    print(f"Liquid core (initial backtest): {len(LIQUID_CORE)}")
