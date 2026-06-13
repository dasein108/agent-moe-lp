"""
Telegram notification utilities using requests for synchronous operations.

Provides functions for sending messages to Telegram channels/chats
with proper error handling and timeout management for farming bot notifications.
"""

import logging
import os
from typing import Optional
from urllib.parse import quote

import requests


# Configuration constants - set your bot token and channel ID here or via environment variables
# Default values provided for convenience - replace with your actual bot credentials
TELEGRAM_BOT_TOKEN: Optional[str] = '1065120410:AAHkYR6eWf00BdccQSdrJs6Re2P7zAwP89I'
TELEGRAM_CHANNEL_ID: Optional[str] = '-869981089'
TG_BOT_PREFIX: str = "MoeBot"

# Telegram API endpoints
TELEGRAM_API_BASE_URL = "https://api.telegram.org/bot{token}"
TELEGRAM_API_SEND_MESSAGE_URL = TELEGRAM_API_BASE_URL + "/sendMessage"

# Default timeout for requests
DEFAULT_TIMEOUT = 10.0

logger = logging.getLogger(__name__)


def configure_telegram_from_env() -> None:
    """Configure Telegram settings from environment variables."""
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TG_BOT_PREFIX
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    TG_BOT_PREFIX = os.getenv("TELEGRAM_BOT_PREFIX", "MoeBot")


def configure_telegram(bot_token: str, channel_id: str, bot_prefix: str = "MoeBot"):
    """
    Configure Telegram settings manually.
    
    Args:
        bot_token: Telegram bot token
        channel_id: Default channel/chat ID to send messages to
        bot_prefix: Prefix for all messages
    """
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TG_BOT_PREFIX
    TELEGRAM_BOT_TOKEN = bot_token
    TELEGRAM_CHANNEL_ID = channel_id
    TG_BOT_PREFIX = bot_prefix


def send_to_telegram(
    message: str, 
    channel_id: Optional[str] = None,
    parse_mode: str = "HTML",
    timeout: float = DEFAULT_TIMEOUT,
    silent: bool = False
) -> bool:
    """
    Send message to Telegram channel using requests.
    
    Args:
        message: Message text to send
        channel_id: Target channel/chat ID (uses default if None)
        parse_mode: Message parsing mode ('HTML', 'Markdown', or None)
        timeout: Request timeout in seconds
        silent: If True, suppress console logging on errors
        
    Returns:
        True if message was sent successfully, False otherwise
    """
    # Auto-configure from environment if not already configured
    if not TELEGRAM_BOT_TOKEN:
        configure_telegram_from_env()
    
    if not TELEGRAM_BOT_TOKEN:
        if not silent:
            logger.debug("Telegram bot token not configured")
        return False
    
    target_channel = channel_id or TELEGRAM_CHANNEL_ID
    if not target_channel:
        if not silent:
            logger.debug("No Telegram channel ID provided")
        return False
    
    try:
        # Format message with prefix
        formatted_message = f"#{TG_BOT_PREFIX}\n{message}"
        
        # Prepare request data
        url = TELEGRAM_API_SEND_MESSAGE_URL.format(token=TELEGRAM_BOT_TOKEN)
        data = {
            'chat_id': target_channel,
            'text': formatted_message,
        }
        
        if parse_mode:
            data['parse_mode'] = parse_mode
        
        # Send message
        response = requests.post(url, data=data, timeout=timeout)
        
        if response.status_code == 200:
            logger.debug(f"Telegram message sent successfully to {target_channel}")
            return True
        else:
            if not silent:
                logger.warning(f"Telegram API returned status {response.status_code}: {response.text}")
            return False
                
    except requests.exceptions.Timeout:
        if not silent:
            logger.warning(f"Telegram request timeout after {timeout}s")
        return False
    except requests.exceptions.RequestException as e:
        if not silent:
            logger.warning(f"Telegram request error: {e}")
        return False
    except Exception as e:
        if not silent:
            logger.debug(f"Telegram error: {e}")
        return False


def send_to_telegram_batch(
    messages: list[str],
    channel_id: Optional[str] = None,
    parse_mode: str = "HTML",
    timeout: float = DEFAULT_TIMEOUT,
    delay_between_messages: float = 0.1
) -> int:
    """
    Send multiple messages to Telegram with rate limiting.
    
    Args:
        messages: List of message texts to send
        channel_id: Target channel/chat ID (uses default if None)
        parse_mode: Message parsing mode
        timeout: Request timeout per message
        delay_between_messages: Delay between messages to avoid rate limits
        
    Returns:
        Number of messages sent successfully
    """
    if not messages:
        return 0
    
    successful_sends = 0
    
    for i, message in enumerate(messages):
        if i > 0:
            import time
            time.sleep(delay_between_messages)
        
        success = send_to_telegram(
            message=message,
            channel_id=channel_id,
            parse_mode=parse_mode,
            timeout=timeout,
            silent=True  # Silent for batch operations
        )
        
        if success:
            successful_sends += 1
        else:
            logger.warning(f"Failed to send message {i+1}/{len(messages)}")
    
    logger.info(f"Sent {successful_sends}/{len(messages)} messages to Telegram")
    return successful_sends


def send_alert(
    alert_type: str,
    message: str,
    channel_id: Optional[str] = None,
    urgent: bool = False
) -> bool:
    """
    Send formatted alert message to Telegram.
    
    Args:
        alert_type: Type of alert (e.g., 'ERROR', 'WARNING', 'INFO')
        message: Alert message content
        channel_id: Target channel/chat ID
        urgent: If True, use bold formatting for urgent alerts
        
    Returns:
        True if alert was sent successfully
    """
    if urgent:
        formatted_message = f"🚨 <b>{alert_type.upper()}</b>\n{message}"
    else:
        alert_emoji = {
            'ERROR': '❌',
            'WARNING': '⚠️',
            'INFO': 'ℹ️',
            'SUCCESS': '✅',
            'PROFIT': '💰',
            'TRADE': '📈',
            'LP_CREATE': '💰',
            'LP_REMOVE': '💸',
            'SWAP': '🔄',
            'REBALANCE': '⚖️',
            'FARM': '🚜'
        }.get(alert_type.upper(), '📢')
        
        formatted_message = f"{alert_emoji} <b>{alert_type.upper()}</b>\n{message}"
    
    return send_to_telegram(
        message=formatted_message,
        channel_id=channel_id,
        parse_mode="HTML"
    )


# Convenience functions for common alert types
def send_error_alert(message: str, urgent: bool = True) -> bool:
    """Send error alert to Telegram."""
    return send_alert("ERROR", message, urgent=urgent)


def send_warning_alert(message: str) -> bool:
    """Send warning alert to Telegram."""
    return send_alert("WARNING", message)


def send_info_alert(message: str) -> bool:
    """Send info alert to Telegram."""
    return send_alert("INFO", message)


def send_trade_alert(message: str) -> bool:
    """Send trading alert to Telegram."""
    return send_alert("TRADE", message)


def send_profit_alert(message: str) -> bool:
    """Send profit alert to Telegram."""
    return send_alert("PROFIT", message, urgent=True)


def send_lp_alert(message: str, alert_type: str = "LP_CREATE") -> bool:
    """Send LP-related alert to Telegram."""
    return send_alert(alert_type, message)


def send_swap_alert(message: str) -> bool:
    """Send swap alert to Telegram."""
    return send_alert("SWAP", message)


def send_farm_alert(message: str) -> bool:
    """Send farming cycle alert to Telegram."""
    return send_alert("FARM", message)


# Economic-focused notification functions
def send_transaction_alert(
    operation: str, 
    details: str, 
    economic_impact: str,
    performance_metrics: str = ""
) -> bool:
    """
    Send economic-focused transaction alert.
    
    Args:
        operation: Transaction type (SWAP, LP_ADD, LP_REMOVE, REWARDS)
        details: Transaction details (amounts, assets)
        economic_impact: Economic impact (fees, profits, capital)
        performance_metrics: Optional performance data
    
    Returns:
        True if alert was sent successfully (or skipped for SKIP operations)
    """
    # Skip Telegram notifications for SKIP operations
    if operation == 'SKIP':
        logger.debug(f"Skipping Telegram notification for SKIP operation: {details}")
        return True
    
    emoji_map = {
        'SWAP': '🔄',
        'LP_ADD': '📈', 
        'LP_REMOVE': '📉',
        'REWARDS': '🎯',
        'ERROR': '❌'
    }
    
    emoji = emoji_map.get(operation, '📢')
    message = f"{emoji} {operation}: {details} | {economic_impact}"
    
    if performance_metrics:
        message += f" | {performance_metrics}"
    
    return send_to_telegram(message, parse_mode="HTML")


def send_status_alert(
    capital: str,
    fees_earned: str, 
    rewards_earned: str,
    performance: str,
    duration: str
) -> bool:
    """
    Send periodic status update (every 30 mins when in range).
    
    Args:
        capital: Current capital deployed
        fees_earned: Fees earned this session
        rewards_earned: Rewards earned this session  
        performance: Performance metrics (APR, daily estimate)
        duration: How long position has been active
        
    Returns:
        True if status was sent successfully
    """
    total_earnings = f"Total: +{fees_earned.replace('Fees: +', '')} + {rewards_earned.replace('Rewards: +', '')}" if "+" in fees_earned and "+" in rewards_earned else f"Fees: {fees_earned}, Rewards: {rewards_earned}"
    
    message = f"💰 STATUS: LP active {duration} | {fees_earned} | {rewards_earned} | {total_earnings}\n📊 PERFORMANCE: {capital} | {performance}"
    
    return send_to_telegram(message, parse_mode="HTML")


def send_error_with_action(error: str, action: str) -> bool:
    """
    Send error alert with suggested action.
    
    Args:
        error: Error description
        action: Suggested action to resolve
        
    Returns:
        True if error alert was sent successfully
    """
    message = f"❌ ERROR: {error} - {action}"
    return send_to_telegram(message, parse_mode="HTML")