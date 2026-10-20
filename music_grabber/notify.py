"""ntfy notifications: the grabber half of BPM Tagger's NotificationManager.

The scan-batching half of that class is BPM Tagger specific and not carried over.
Sending never raises: ntfy being down must not fail a grab.
"""

import base64
import logging

import requests

log = logging.getLogger(__name__)


def _header(value: str) -> str:
    """HTTP headers are latin-1; ntfy reads RFC 2047 encoded words, so anything else (an en dash
    in a track title, say) is sent as =?UTF-8?B?...?= instead of failing the whole ping."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode()).decode() + "?="


class Notifier:
    def __init__(self, ntfy_url: str, topic: str):
        self._url = ntfy_url.rstrip("/")
        self._topic = topic

    @classmethod
    def from_config(cls, config: dict):
        """A Notifier when ntfy is configured, else None."""
        if config.get("ntfy_url") and config.get("ntfy_topic"):
            return cls(config["ntfy_url"], config["ntfy_topic"])
        return None

    def send_grabber(self, title: str, body: str, click_url: str = "",
                     priority: str = "default", tags: str = "arrow_down", actions: str = ""):
        """Ambiguity/failure use high priority + a click URL/action into the inbox."""
        self._post(title, body, tags, priority=priority, click_url=click_url, actions=actions)

    def _post(self, title: str, body: str, tag: str, priority: str = "low",
              click_url: str = "", actions: str = ""):
        try:
            headers = {"Title": _header(title.replace("\n", " ")), "Priority": priority, "Tags": tag}
            if click_url:
                headers["Click"] = _header(click_url)
            if actions:
                headers["Actions"] = _header(actions)
            resp = requests.post(f"{self._url}/{self._topic}", data=body.encode(),
                                 headers=headers, timeout=10)
            resp.raise_for_status()
            log.debug("ntfy notification sent: %s", title)
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)
