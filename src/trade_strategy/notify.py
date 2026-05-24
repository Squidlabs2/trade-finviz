from __future__ import annotations

import os
from dataclasses import dataclass
from urllib import request


@dataclass(frozen=True)
class NtfyConfig:
    url: str
    topic: str
    token: str | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.url.rstrip('/')}/{self.topic}"


def ntfy_config_from_env() -> NtfyConfig | None:
    url = os.getenv("NTFY_URL")
    topic = os.getenv("NTFY_TOPIC")
    if not url or not topic:
        return None
    return NtfyConfig(url=url, topic=topic, token=os.getenv("NTFY_TOKEN"))


def send_ntfy_message(config: NtfyConfig, title: str, message: str, priority: str = "default") -> None:
    payload = message.encode("utf-8")
    req = request.Request(config.endpoint, data=payload, method="POST")
    req.add_header("User-Agent", "trade-strategy-weekly-scan/0.1")
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    if config.token:
        req.add_header("Authorization", f"Bearer {config.token}")

    with request.urlopen(req, timeout=20) as response:
        response.read()
