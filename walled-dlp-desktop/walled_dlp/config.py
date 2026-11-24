"""Static configuration. Site-specific page knowledge (selectors) lives here."""
from __future__ import annotations

import os

MARKER = "WALLED_SENSITIVE"
RESPONSE_COLOR = "#e8590c"
MAX_FILE_BYTES = 15 * 1024 * 1024

SITES = {
    "ChatGPT": {"url": "https://chatgpt.com", "hosts": ["chatgpt.com"]},
    "Perplexity": {"url": "https://www.perplexity.ai", "hosts": ["www.perplexity.ai", "perplexity.ai"]},
}

# Prompt box / send button (used only for a clean "cancel keeps my text" experience;
# the network hook is the safety net if these ever stop matching).
COMPOSER_SELECTORS = ["#prompt-textarea", "#ask-input", "div[contenteditable='true']", "textarea"]
SEND_BUTTON_SELECTOR = ",".join([
    "button[data-testid='send-button']", "button#composer-submit-button",
    "button[data-testid='submit-button']", "button[aria-label='Submit']",
    "button[aria-label='Send prompt']", "button[aria-label='Send message']",
])
# AI answer containers whose text is recoloured.
RESPONSE_SELECTORS = [
    "[data-message-author-role='assistant']",   # ChatGPT message
    "[data-turn='assistant']",                  # ChatGPT turn wrapper (newer markup)
    ".markdown",                                # ChatGPT answer body
    "[id^='markdown-content-']",                # Perplexity answer
    "div.prose",                                # Perplexity / generic answer body
]


def allowed_hosts() -> list[str]:
    hosts = [h for s in SITES.values() for h in s["hosts"]]
    hosts += [h for h in os.environ.get("WALLED_EXTRA_HOSTS", "").split(",") if h]  # tests only
    return hosts


def page_config() -> dict:
    return {"hosts": allowed_hosts(), "marker": MARKER, "responseColor": RESPONSE_COLOR,
            "responseSelectors": RESPONSE_SELECTORS, "composerSelectors": COMPOSER_SELECTORS,
            "sendButtonSelector": SEND_BUTTON_SELECTOR, "maxFileBytes": MAX_FILE_BYTES,
            "failClosed": True}
