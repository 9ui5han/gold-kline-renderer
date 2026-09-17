"""Fetch readable text from trusted official macro article pages."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

TRUSTED_HOST_SUFFIXES = ("federalreserve.gov", "newyorkfed.org", "whitehouse.gov", "state.gov", "bls.gov", "bea.gov", "treasury.gov")

class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.depth = 0
        self.content_depth: int | None = None
    def handle_starttag(self, tag: str, attrs) -> None:
        self.depth += 1
        if tag in {"article", "main"} and self.content_depth is None:
            self.content_depth = self.depth
        if tag in {"script", "style", "noscript", "nav", "header", "footer", "aside", "form"}:
            self.skip += 1
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer", "aside", "form"}:
            self.skip = max(0, self.skip - 1)
        self.depth = max(0, self.depth - 1)
    def handle_data(self, data: str) -> None:
        if not self.skip and (self.content_depth is None or self.depth >= self.content_depth):
            text = " ".join(data.split())
            if text:
                self.parts.append(text)

def fetch_article_text(url: str, *, timeout: float = 15.0) -> str:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in TRUSTED_HOST_SUFFIXES):
        return ""
    try:
        response = httpx.get(url, follow_redirects=True, timeout=timeout, headers={"User-Agent": "gold-kline-renderer-macro/1.0"})
        response.raise_for_status()
    except (httpx.HTTPError, OSError):
        return ""
    if "html" not in response.headers.get("content-type", "").casefold():
        return ""
    parser = _TextParser()
    try:
        parser.feed(response.text)
    except Exception:
        return ""
    boilerplate = {
        "Skip to main content", "An official website of the United States Government",
        "Here's how you know", "Official websites use .gov", "Secure .gov websites use HTTPS",
        "Back to Home", "Stay Connected",
    }
    cleaned = [part for part in parser.parts if part not in boilerplate]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()[:30000]
