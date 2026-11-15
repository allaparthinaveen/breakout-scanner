"""
Production Secrets & Configuration Management.

Simple, secure credential resolution from local environment variables and .env files.
Zero external AWS SSM dependencies, zero extra cloud cost.
"""

import os
import logging
from typing import Optional, Tuple, List
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


def get_telegram_chat_ids() -> List[str]:
    """
    Resolves list of Telegram chat IDs from environment (supports comma/semicolon separation).
    """
    _, chat_id = get_telegram_credentials()
    if not chat_id:
        return []
    return [cid.strip() for cid in chat_id.replace(";", ",").split(",") if cid.strip()]


def get_telegram_targets() -> List[Tuple[str, str]]:
    """
    Resolves list of (bot_token, chat_id) dispatch targets from environment.
    Supports:
    1. Paired comma/semicolon lists:
       TG_BOT_TOKEN="token1,token2"
       TG_CHAT_ID="chat1,chat2"
       -> [("token1", "chat1"), ("token2", "chat2")]
    2. Single bot token broadcasting to multiple chats:
       TG_BOT_TOKEN="token1"
       TG_CHAT_ID="chat1,chat2"
       -> [("token1", "chat1"), ("token1", "chat2")]
    3. Multi-instance numbered variables:
       TG_BOT_TOKEN_2, TG_CHAT_ID_2...
    """
    targets: List[Tuple[str, str]] = []

    raw_token, raw_chat = get_telegram_credentials()
    if raw_token and raw_chat:
        tokens = [t.strip() for t in raw_token.replace(";", ",").split(",") if t.strip()]
        chats = [c.strip() for c in raw_chat.replace(";", ",").split(",") if c.strip()]

        if len(tokens) == 1 and len(chats) >= 1:
            for c in chats:
                targets.append((tokens[0], c))
        elif len(tokens) == len(chats):
            for t, c in zip(tokens, chats):
                targets.append((t, c))
        elif len(tokens) > 1 and len(chats) == 1:
            for t in tokens:
                targets.append((t, chats[0]))
        else:
            # Lengths differ and both > 1: pair up as much as possible
            for t, c in zip(tokens, chats):
                targets.append((t, c))

    # Check for numbered variables (e.g. TG_BOT_TOKEN_2 / TG_CHAT_ID_2)
    idx = 2
    while True:
        tok_idx = get_secret(f"TG_BOT_TOKEN_{idx}") or get_secret(f"TELEGRAM_BOT_TOKEN_{idx}")
        chat_idx = get_secret(f"TG_CHAT_ID_{idx}") or get_secret(f"TELEGRAM_CHAT_ID_{idx}")
        if tok_idx and chat_idx:
            pair = (tok_idx.strip(), chat_idx.strip())
            if pair not in targets:
                targets.append(pair)
            idx += 1
        else:
            break

    return targets
