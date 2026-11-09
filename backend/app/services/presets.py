from __future__ import annotations

SITE_PRESETS: dict[str, dict] = {
    "anthropic": {
        "name": "Anthropic (Claude)",
        "match_type": "domain",
        "values": [
            "anthropic.com",
            "claude.ai",
            "claude.com",
            "console.anthropic.com",
            "api.anthropic.com",
        ],
    },
    "openai": {
        "name": "OpenAI (ChatGPT)",
        "match_type": "domain",
        "values": ["openai.com", "chatgpt.com", "api.openai.com", "platform.openai.com"],
    },
    "google": {
        "name": "Google / Gemini",
        "match_type": "geosite",
        "values": ["google", "google-gemini", "google-deepmind"],
    },
    "telegram": {
        "name": "Telegram",
        "match_type": "geosite",
        "values": ["telegram"],
    },
    "github": {
        "name": "GitHub",
        "match_type": "domain",
        "values": ["github.com", "api.github.com", "raw.githubusercontent.com", "objects.githubusercontent.com"],
    },
    "youtube": {
        "name": "YouTube",
        "match_type": "geosite",
        "values": ["youtube"],
    },
    "meta": {
        "name": "Meta (Facebook/Instagram)",
        "match_type": "geosite",
        "values": ["meta", "facebook", "instagram"],
    },
    "custom": {"name": "Свои домены", "match_type": "domain", "values": []},
}


def preset_list() -> list[dict]:
    return [{"key": key, **value} for key, value in SITE_PRESETS.items()]
