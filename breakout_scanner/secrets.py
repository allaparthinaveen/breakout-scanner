"""
Production Secrets & Configuration Management.

Simple, secure credential resolution from local environment variables and .env files.
Zero external AWS SSM dependencies, zero extra cloud cost.
"""

import os
import logging
from typing import Optional, Tuple
from dotenv import load_dotenv

logger = logging.getLogger("secrets_manager")

# Load local .env
load_dotenv()


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Resolves secret from environment variables or .env file with alias fallback.
    """
    val = os.getenv(key)
    if val:
        return val

    # Check common aliases
    aliases = {
        "TG_BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN": "TG_BOT_TOKEN",
        "TG_CHAT_ID": "TELEGRAM_CHAT_ID",
        "TELEGRAM_CHAT_ID": "TG_CHAT_ID",
    }
    alt_key = aliases.get(key)
    if alt_key:
        alt_val = os.getenv(alt_key)
        if alt_val:
            return alt_val

    return default


def get_telegram_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Resolves Telegram bot token and chat ID from environment/.env.
    Returns: (bot_token, chat_id)
    """
    token = get_secret("TG_BOT_TOKEN")
    chat_id = get_secret("TG_CHAT_ID")
    return token, chat_id
