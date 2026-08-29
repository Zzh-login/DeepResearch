import asyncio
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import trafilatura

from infrastructure.config.settings import Settings
from infrastructure.tools.web_search import search_structured


class UnsafeUrlError(ValueError):
    pass


@dataclass(frozen=True)
class WebDocument:
    title: str
    url: str
    content: str
    score: float


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("只允许公开 HTTP/HTTPS URL")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL 不允许包含凭据")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise UnsafeUrlError("只允许访问 80/443 端口")

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        parsed.hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeUrlError("拒绝访问本机、内网或保留地址")


class ResearchWebGateway:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def search(self, query: str) -> list[dict]:
        return await search_structured(
            query=query,
            top_k=self._settings.research_results_per_query,
            tavily_key=os.getenv("WEB_SEARCH_API_KEY", ""),
            baidu_key=os.getenv("BAIDU_API_KEY", ""),
        )

    async def _download(self, url: str) -> tuple[str, bytes, str]:
        timeout = self._settings.research_fetch_timeout_seconds
        max_bytes = self._settings.research_max_response_bytes
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "RobotResearchBot/1.0"},
        ) as client:
            current_url = url
            for _ in range(4):
                await _validate_public_url(current_url)
                async with client.stream("GET", current_url) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("网页重定向缺少目标地址")
                        current_url = str(httpx.URL(current_url).join(location))
                        continue

                    response.raise_for_status()
                    content_type = response.headers.get(
                        "content-type", ""
                    ).lower()
                    if not any(
                        item in content_type
                        for item in ("text/html", "text/plain")
                    ):
                        raise ValueError("只抓取 HTML 或纯文本")

                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > max_bytes:
                        raise ValueError("网页响应超过大小限制")

                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("网页响应超过大小限制")
                        chunks.append(chunk)
                    encoding = response.encoding or "utf-8"
                    return current_url, b"".join(chunks), encoding
            raise ValueError("网页重定向次数过多")

    async def fetch(self, title: str, url: str, score: float) -> WebDocument:
        final_url, raw, encoding = await self._download(url)

        text = trafilatura.extract(
            raw.decode(encoding, errors="replace"),
            include_comments=False,
            include_tables=True,
        ) or ""
        text = text.strip()[: self._settings.research_max_source_chars]
        if len(text) < 80:
            raise ValueError("网页正文过短")
        return WebDocument(
            title=title,
            url=final_url,
            content=text,
            score=score,
        )
