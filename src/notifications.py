import requests
import logging
import json
from .config import WEBHOOK_URL, DRY_RUN

logger = logging.getLogger(__name__)

def send_webhook_alert(title: str, message: str, color: int = 3447003):
    """
    Sends an alert to a Discord webhook if configured.
    color: int, e.g., 3447003 (blue), 16711680 (red), 65280 (green), 16753920 (orange)
    """
    if not WEBHOOK_URL:
        logger.debug("No webhook URL configured, skipping notification.")
        return

    prefix = "[DRY-RUN] " if DRY_RUN else "[LIVE] "
    full_title = f"{prefix}{title}"

    payload = {
        "username": "Binance Algorithmic Bot",
        "embeds": [
            {
                "title": full_title,
                "description": message,
                "color": color
            }
        ]
    }

    try:
        response = requests.post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        if response.status_code not in (200, 204):
            logger.error(f"Webhook delivery failed with status {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Error sending webhook alert: {e}")
