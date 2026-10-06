"""Read-only MCP tools for the Nifty Intraday Agent's stored research data."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from database import (
    equity_signal_performance,
    get_market_status as read_market_status,
    latest_signal,
    list_candles,
    list_equity_signals as read_equity_signals,
    list_news as read_news,
    list_spot_candles,
    list_signals as read_signals,
    performance,
)

mcp = FastMCP("Nifty Intraday Agent")


def _validated_limit(limit: int) -> int:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    return limit


@mcp.tool()
def get_market_status() -> dict[str, Any] | None:
    """Get the latest market-feed and analysis status."""
    return read_market_status()


@mcp.tool()
def get_latest_nifty_signal() -> dict[str, Any] | None:
    """Get the most recently stored NIFTY signal."""
    return latest_signal()


@mcp.tool()
def list_nifty_signals(limit: int = 20) -> list[dict[str, Any]]:
    """List recent NIFTY signals, newest first."""
    return read_signals(_validated_limit(limit))


@mcp.tool()
def get_nifty_signal_performance() -> dict[str, int | float | None]:
    """Get stored NIFTY signal outcome counts and resolved accuracy."""
    return performance()


@mcp.tool()
def list_equity_signals(limit: int = 20) -> list[dict[str, Any]]:
    """List recent equity scanner signals, newest first."""
    return read_equity_signals(_validated_limit(limit))


@mcp.tool()
def get_equity_signal_performance() -> dict[str, int | float | None]:
    """Get stored equity signal outcome counts and resolved accuracy."""
    return equity_signal_performance()


@mcp.tool()
def list_recent_news(limit: int = 20) -> list[dict[str, Any]]:
    """List stored market-news headlines, newest first."""
    return read_news(_validated_limit(limit))


@mcp.tool()
def list_recent_nifty_futures_candles(limit: int = 20) -> list[dict[str, Any]]:
    """List NIFTY futures candles used internally for volume context."""
    return list_candles(_validated_limit(limit))


@mcp.tool()
def list_recent_nifty_spot_candles(limit: int = 20) -> list[dict[str, Any]]:
    """List recent NIFTY spot OHLC candles."""
    return list_spot_candles(_validated_limit(limit))


if __name__ == "__main__":
    mcp.run(transport="stdio")