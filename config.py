from typing import List
"""Configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Alpaca
    ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET: str = os.getenv("ALPACA_SECRET", "")
    ALPACA_BASE_URL: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    # Webhook
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

    # Permitted symbols
    PERMITTED_SYMBOLS: List[str] = [
        s.strip().upper()
        for s in os.getenv("PERMITTED_SYMBOLS", "SPY").split(",")
        if s.strip()
    ]

    # Server
    PORT: int = int(os.getenv("PORT", "8888"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Safety
    TRADING_MODE: str = os.getenv("TRADING_MODE", "paper")

    @classmethod
    def is_paper(cls) -> bool:
        return cls.TRADING_MODE.lower() == "paper"

    @classmethod
    def is_live_blocked(cls) -> bool:
        """Block if configured with live endpoint."""
        return "paper" not in cls.ALPACA_BASE_URL.lower()

    @classmethod
    def validate(cls) -> List[str]:
        errors = []
        if not cls.ALPACA_API_KEY:
            errors.append("ALPACA_API_KEY not set")
        if not cls.ALPACA_SECRET:
            errors.append("ALPACA_SECRET not set")
        if not cls.WEBHOOK_SECRET:
            errors.append("WEBHOOK_SECRET not set")
        if not cls.is_paper():
            errors.append("TRADING_MODE must be 'paper'")
        if cls.is_live_blocked():
            errors.append("ALPACA_BASE_URL must contain 'paper'")
        return errors
