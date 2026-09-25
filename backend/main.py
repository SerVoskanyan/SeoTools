"""
SEO Analyzer API — FastAPI backend with anti-bot resilient fetching.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("seo_analyzer")

import httpx
from bs4 import BeautifulSoup
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

RETRY_STATUSES = {403, 429, 503}
MAX_FETCH_ATTEMPTS = 4
REQUEST_TIMEOUT = 15.0
PREVIEW_CHARS = 500
MAX_DETAIL_URLS = 100

TITLE_LEN_OK = (30, 65)
DESC_LEN_OK = (70, 160)

@dataclass(frozen=True)
class BrowserProfile:
    user_agent: str
    sec_ch_ua: str | None
    sec_ch_ua_platform: str | None
    accept_language: str


BROWSER_PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        accept_language="ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="132", "Google Chrome";v="132", "Not_A Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        accept_language="en-US,en;q=0.9,ru;q=0.8",
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_platform='"macOS"',
        accept_language="ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.2 Safari/605.1.15"
        ),
        sec_ch_ua=None,
        sec_ch_ua_platform=None,
        accept_language="ru-RU,ru;q=0.9,en;q=0.8",
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
        sec_ch_ua='"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        accept_language="en-US,en;q=0.9",
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
        ),
        sec_ch_ua=None,
        sec_ch_ua_platform=None,
        accept_language="ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    ),
]

REFERERS = [
    "https://www.google.com/",
    "https://www.google.ru/",
    "https://yandex.ru/search/",
    "https://www.bing.com/",
]

BOT_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "challenge-platform",
    "just a moment",
    "checking your browser",
    "ddos-guard",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "cf-chl-bypass",
    "captcha-box",
    "perimeterx",
    "datadome",
    "geo.captcha-delivery.com",
)

NOT_FOUND_PROBE_PATH = "/seo-check-404-page-test-123"

CMS_SIGNATURES: list[tuple[str, str]] = [
    ("wp-content", "WordPress"),
    ("wp-includes", "WordPress"),
    ("/bitrix/", "1C-Bitrix"),
    ("Joomla!", "Joomla"),
    ("Drupal", "Drupal"),
    ("/_nuxt/", "Nuxt.js"),
    ("__NEXT_DATA__", "Next.js"),
    ("react-root", "React SPA"),
    ("cdn.shopify.com", "Shopify"),
    ("Wix.com", "Wix"),
    ("Tilda", "Tilda"),
    ("modx", "MODX"),
]

GENERATOR_PATTERNS: list[tuple[str, str]] = [
    ("wordpress", "WordPress"),
    ("bitrix", "1C-Bitrix"),
    ("joomla", "Joomla"),
    ("drupal", "Drupal"),
    ("ghost", "Ghost"),
    ("squarespace", "Squarespace"),
]


def build_headers(attempt: int) -> dict[str, str]:
    profile = BROWSER_PROFILES[attempt % len(BROWSER_PROFILES)]
    sec_fetch_site = "none" if attempt == 0 else "cross-site"
    headers: dict[str, str] = {
        "User-Agent": profile.user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": profile.accept_language,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": sec_fetch_site,
        "Sec-Fetch-User": "?1",
        "Referer": REFERERS[attempt % len(REFERERS)],
        "Priority": "u=0, i",
    }
    if profile.sec_ch_ua:
        headers["Sec-Ch-Ua"] = profile.sec_ch_ua
        headers["Sec-Ch-Ua-Mobile"] = "?0"
        if profile.sec_ch_ua_platform:
            headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform
    return headers


def waf_hint_from_headers(response_headers: dict[str, str]) -> str | None:
    server = (header_value(response_headers, "server") or "").lower()
    if "cloudflare" in server:
        return "Cloudflare"
    if header_value(response_headers, "cf-ray"):
        return "Cloudflare"
    if header_value(response_headers, "x-dd-b"):
        return "DataDome"
    if header_value(response_headers, "x-sucuri-id"):
        return "Sucuri WAF"
    if "akamaighost" in server or header_value(response_headers, "x-akamai-transformed"):
        return "Akamai"
    return None


def looks_like_bot_challenge(html: str, response_headers: dict[str, str]) -> bool:
    if waf_hint_from_headers(response_headers):
        sample = html[:8000].lower()
        if any(marker in sample for marker in BOT_CHALLENGE_MARKERS):
            return True
        if len(html.strip()) < 500 and "cloudflare" in sample:
            return True
    sample = html[:12000].lower()
    if "just a moment" in sample and "cloudflare" in sample:
        return True
    if "challenge-platform" in sample or "cf-browser-verification" in sample:
        return True
    return False


def humanize_fetch_error(meta: FetchMeta) -> str:
    code = meta.status_code
    err = (meta.error or "").lower()
    waf = waf_hint_from_headers(meta.response_headers)

    if meta.error == "bot_protection":
        waf_part = f" ({waf})" if waf else ""
        return (
            "Не удалось получить доступ к сайту: сработала защита от ботов"
            f"{waf_part}. Страница требует браузер с JavaScript."
        )
    if code == 403:
        waf_part = f" ({waf})" if waf else ""
        return f"Не удалось получить доступ к сайту: возможна защита от ботов (403){waf_part}."
    if code == 429:
        return "Сайт временно ограничил число запросов (429). Попробуйте позже."
    if code in (503, 520, 521, 522, 523, 524):
        waf_part = f" ({waf})" if waf else ""
        return f"Сайт недоступен или отдаёт страницу проверки (HTTP {code}){waf_part}."
    if code == 408 or "timeout" in err:
        return "Превышено время ожидания ответа от сайта."
    if code == 401:
        return "Доступ к странице запрещён (401)."
    if code >= 400 and code < 500:
        return f"Сайт отклонил запрос (HTTP {code})."
    if code >= 500:
        return f"Ошибка на стороне сайта (HTTP {code})."
    if meta.error:
        return f"Не удалось загрузить страницу: {meta.error}."
    return "Не удалось загрузить страницу."


def normalize_header_map(headers: httpx.Headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def header_value(headers: dict[str, str], name: str) -> str | None:
    return headers.get(name.lower())


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class CheckItem(BaseModel):
    id: str
    level: str  # pass | warn | crit | info
    message: str


class RecommendationItem(BaseModel):
    id: str
    category: str  # meta | headings | images | links | robots | sitemap | security | server
    status: str  # ok | warning | error
    title: str
    value: str  # «Как сейчас»
    expected: str = ""  # «Как должно быть»
    recommendation: str  # «Решение»
    praise: str = ""  # текст для status ok
    char_count: int | None = None
    detail_urls: list[str] = Field(default_factory=list)


class TechStackBlock(BaseModel):
    html_lang: str | None
    cms_or_engine: str | None
    server_header: str | None
    powered_by: str | None
    signals: list[str]


class FetchMeta(BaseModel):
    status_code: int
    final_url: str
    attempts: int
    error: str | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)


class MetaBlock(BaseModel):
    title: str
    title_char_count: int
    title_status: str  # ok | warning | error
    description: str
    description_char_count: int
    description_status: str  # ok | warning | error
    canonical: str | None
    robots: str
    og_title: str | None
    og_description: str | None
    og_image: str | None


class SchemaBlock(BaseModel):
    found: bool
    types: list[str]
    formats: list[str]


class HeadingItem(BaseModel):
    tag: str
    level: int
    text: str


class HeadingsBlock(BaseModel):
    h1_count: int
    total: int
    hierarchy_ok: bool
    warnings: list[str]
    items: list[HeadingItem]


class ImagesBlock(BaseModel):
    total: int
    missing_alt_count: int
    missing_alt_urls: list[str]


class LinksBlock(BaseModel):
    total: int
    internal: int
    external: int
    nofollow: int
    target_blank: int
    external_blank_missing_rel: int
    unsafe_blank_urls: list[str]


class SecurityBlock(BaseModel):
    is_https: bool
    has_viewport: bool
    viewport_content: str | None
    has_favicon: bool
    favicon_href: str | None


class TechFileBlock(BaseModel):
    url: str
    available: bool
    status_code: int | None
    size_bytes: int
    preview: str
    error: str | None = None


class AnalyzeResponse(BaseModel):
    success: bool
    page_url: str
    analyzed_at: str
    fetch: FetchMeta
    seo_score: int = Field(ge=0, le=100)
    score_label: str
    checks: list[CheckItem]
    recommendations: list[RecommendationItem]
    tech: TechStackBlock
    meta: MetaBlock
    schema_org: SchemaBlock
    headings: HeadingsBlock
    images: ImagesBlock
    links: LinksBlock
    security: SecurityBlock
    robots_txt: TechFileBlock
    sitemap_xml: TechFileBlock


app = FastAPI(
    title="SEO Analyzer API",
    version="1.0.0",
    description="Backend SEO audit with resilient HTTP fetching",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def fetch_url_resilient(url: str) -> tuple[str | None, FetchMeta]:
    last_status = 0
    last_error: str | None = None
    final_url = url
    last_response_headers: dict[str, str] = {}

    for attempt in range(MAX_FETCH_ATTEMPTS):
        headers = build_headers(attempt)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=REQUEST_TIMEOUT,
                headers=headers,
            ) as client:
                response = await client.get(url)
                last_status = response.status_code
                final_url = str(response.url)
                last_response_headers = normalize_header_map(response.headers)

                logger.info(
                    "fetch attempt=%s url=%s status=%s final_url=%s",
                    attempt + 1,
                    url,
                    response.status_code,
                    final_url,
                )

                if response.status_code in RETRY_STATUSES and attempt < MAX_FETCH_ATTEMPTS - 1:
                    logger.warning(
                        "fetch retryable status=%s attempt=%s url=%s",
                        response.status_code,
                        attempt + 1,
                        url,
                    )
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue

                if response.status_code >= 400:
                    last_error = f"HTTP {response.status_code}"
                    if response.status_code in RETRY_STATUSES and attempt < MAX_FETCH_ATTEMPTS - 1:
                        continue
                    logger.error(
                        "fetch failed status=%s url=%s attempts=%s",
                        response.status_code,
                        url,
                        attempt + 1,
                    )
                    return None, FetchMeta(
                        status_code=response.status_code,
                        final_url=final_url,
                        attempts=attempt + 1,
                        error=last_error,
                        response_headers=last_response_headers,
                    )

                text = response.text
                if not text or not text.strip():
                    last_error = "Empty response body"
                    if attempt < MAX_FETCH_ATTEMPTS - 1:
                        await asyncio.sleep(0.3)
                        continue
                    logger.error("fetch empty body url=%s status=%s", url, response.status_code)
                    return None, FetchMeta(
                        status_code=response.status_code,
                        final_url=final_url,
                        attempts=attempt + 1,
                        error=last_error,
                        response_headers=last_response_headers,
                    )

                if looks_like_bot_challenge(text, last_response_headers):
                    last_error = "bot_protection"
                    last_status = response.status_code
                    logger.warning(
                        "fetch bot challenge detected url=%s status=%s waf=%s attempt=%s",
                        url,
                        response.status_code,
                        waf_hint_from_headers(last_response_headers),
                        attempt + 1,
                    )
                    if attempt < MAX_FETCH_ATTEMPTS - 1:
                        await asyncio.sleep(0.6 * (attempt + 1))
                        continue
                    return None, FetchMeta(
                        status_code=response.status_code,
                        final_url=final_url,
                        attempts=attempt + 1,
                        error=last_error,
                        response_headers=last_response_headers,
                    )

                return text, FetchMeta(
                    status_code=response.status_code,
                    final_url=final_url,
                    attempts=attempt + 1,
                    error=None,
                    response_headers=last_response_headers,
                )
        except httpx.TimeoutException:
            last_error = "Request timeout"
            last_status = 408
            logger.warning("fetch timeout attempt=%s url=%s", attempt + 1, url)
        except httpx.RequestError as exc:
            last_error = str(exc)
            last_status = 0
            logger.warning(
                "fetch request error attempt=%s url=%s reason=%s",
                attempt + 1,
                url,
                exc,
            )

        if attempt < MAX_FETCH_ATTEMPTS - 1:
            await asyncio.sleep(0.5 * (attempt + 1))

    logger.error(
        "fetch exhausted retries url=%s last_status=%s error=%s",
        url,
        last_status,
        last_error,
    )
    return None, FetchMeta(
        status_code=last_status,
        final_url=final_url,
        attempts=MAX_FETCH_ATTEMPTS,
        error=last_error or "Fetch failed",
        response_headers=last_response_headers,
    )


async def fetch_probe(
    url: str,
    *,
    follow_redirects: bool = True,
) -> tuple[int, str, dict[str, str], str | None]:
    """Lightweight GET for redirects, 404 probe, and header checks."""
    headers = build_headers(0)
    try:
        async with httpx.AsyncClient(
            follow_redirects=follow_redirects,
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        ) as client:
            response = await client.get(url)
            return (
                response.status_code,
                str(response.url),
                normalize_header_map(response.headers),
                None,
            )
    except httpx.TimeoutException:
        return 408, url, {}, "Request timeout"
    except httpx.RequestError as exc:
        return 0, url, {}, str(exc)


async def fetch_tech_file(base_origin: str, path: str) -> TechFileBlock:
    file_url = urljoin(base_origin + "/", path.lstrip("/"))
    content, meta = await fetch_url_resilient(file_url)
    if content is None:
        return TechFileBlock(
            url=file_url,
            available=False,
            status_code=meta.status_code if meta.status_code else None,
            size_bytes=0,
            preview="",
            error=meta.error,
        )
    preview = content[:PREVIEW_CHARS]
    return TechFileBlock(
        url=file_url,
        available=True,
        status_code=meta.status_code,
        size_bytes=len(content.encode("utf-8", errors="replace")),
        preview=preview,
        error=None,
    )


def normalize_page_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        return f"https://{url}"
    return url


def resolve_href(href: str | None, base: str) -> str | None:
    if not href or not href.strip():
        return None
    try:
        return urljoin(base, href.strip())
    except Exception:
        return href


def analyze_headings(soup: BeautifulSoup) -> HeadingsBlock:
    tags = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    items: list[HeadingItem] = []
    for tag in tags:
        name = tag.name.lower()
        level = int(name[1])
        text = tag.get_text(strip=True)[:200]
        items.append(HeadingItem(tag=name, level=level, text=text))

    h1_count = sum(1 for i in items if i.level == 1)
    warnings: list[str] = []
    hierarchy_ok = True
    for i in range(len(items) - 1):
        jump = items[i + 1].level - items[i].level
        if jump > 1:
            hierarchy_ok = False
            warnings.append(
                f"{items[i].tag} → {items[i + 1].tag}: пропущен уровень"
            )

    return HeadingsBlock(
        h1_count=h1_count,
        total=len(items),
        hierarchy_ok=hierarchy_ok,
        warnings=warnings,
        items=items,
    )


def analyze_images(soup: BeautifulSoup, origin: str) -> ImagesBlock:
    imgs = soup.find_all("img")
    missing: list[str] = []
    for img in imgs:
        alt = img.get("alt")
        if alt is None or not str(alt).strip():
            src = img.get("src") or img.get("data-src") or "(без src)"
            resolved = resolve_href(src, origin) or src
            missing.append(resolved)
    return ImagesBlock(
        total=len(imgs),
        missing_alt_count=len(missing),
        missing_alt_urls=missing[:MAX_DETAIL_URLS],
    )


def analyze_links(soup: BeautifulSoup, page_url: str) -> LinksBlock:
    parsed_page = urlparse(page_url)

    internal = external = nofollow = target_blank = external_blank_missing_rel = 0
    unsafe_blank: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        try:
            abs_url = urljoin(page_url, href)
            link_parsed = urlparse(abs_url)
        except Exception:
            continue

        rel = (a.get("rel") or [])
        if isinstance(rel, str):
            rel = rel.split()
        rel_lower = " ".join(rel).lower()
        if "nofollow" in rel_lower:
            nofollow += 1
        if a.get("target") == "_blank":
            target_blank += 1
            if "noopener" not in rel_lower and "noreferrer" not in rel_lower:
                unsafe_blank.append(abs_url)

        is_internal = link_parsed.netloc == parsed_page.netloc
        if is_internal:
            internal += 1
        else:
            external += 1
            if a.get("target") == "_blank":
                if "noopener" not in rel_lower and "noreferrer" not in rel_lower:
                    external_blank_missing_rel += 1

    total = internal + external
    return LinksBlock(
        total=total,
        internal=internal,
        external=external,
        nofollow=nofollow,
        target_blank=target_blank,
        external_blank_missing_rel=external_blank_missing_rel,
        unsafe_blank_urls=unsafe_blank[:MAX_DETAIL_URLS],
    )


def meta_field_status(char_count: int, ok_range: tuple[int, int], *, empty_error: bool) -> str:
    if char_count == 0:
        return "error" if empty_error else "warning"
    lo, hi = ok_range
    if lo <= char_count <= hi:
        return "ok"
    return "warning"


def _schema_type_label(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if "/" in raw:
        return raw.rstrip("/").split("/")[-1]
    return raw


def _collect_json_ld_types(node: Any, into: set[str]) -> None:
    if isinstance(node, dict):
        t = node.get("@type")
        if t:
            if isinstance(t, list):
                for item in t:
                    if isinstance(item, str):
                        into.add(_schema_type_label(item))
            elif isinstance(t, str):
                into.add(_schema_type_label(t))
        graph = node.get("@graph")
        if graph:
            _collect_json_ld_types(graph, into)
        for val in node.values():
            _collect_json_ld_types(val, into)
    elif isinstance(node, list):
        for item in node:
            _collect_json_ld_types(item, into)


def analyze_schema(soup: BeautifulSoup) -> SchemaBlock:
    types: set[str] = set()
    formats: set[str] = []

    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        formats.append("JSON-LD")
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
            _collect_json_ld_types(data, types)
        except json.JSONDecodeError:
            continue

    for el in soup.find_all(attrs={"itemscope": True}):
        formats.append("Microdata")
        itemtype = el.get("itemtype") or ""
        if itemtype:
            types.add(_schema_type_label(itemtype))

    unique_formats = sorted(set(formats))
    return SchemaBlock(
        found=bool(types),
        types=sorted(types),
        formats=unique_formats,
    )


def detect_tech_stack(
    html: str,
    soup: BeautifulSoup,
    response_headers: dict[str, str],
) -> TechStackBlock:
    html_lower = html.lower()
    signals: list[str] = []
    cms: str | None = None

    generator_el = soup.find("meta", attrs={"name": "generator"})
    generator = (generator_el.get("content") or "").strip() if generator_el else ""
    if generator:
        signals.append(f"meta generator: {generator[:120]}")
        gen_l = generator.lower()
        for needle, name in GENERATOR_PATTERNS:
            if needle in gen_l:
                cms = name
                break

    if not cms:
        for needle, name in CMS_SIGNATURES:
            if needle.lower() in html_lower:
                cms = name
                signals.append(f"markup: {needle}")
                break

    server = header_value(response_headers, "server")
    powered = header_value(response_headers, "x-powered-by")
    if server:
        signals.append(f"Server: {server}")
    if powered:
        signals.append(f"X-Powered-By: {powered}")
        if not cms and powered:
            cms = powered.split("/")[0].strip()[:80]

    html_el = soup.find("html")
    html_lang = None
    if html_el:
        lang = html_el.get("lang") or html_el.get("xml:lang")
        if lang:
            html_lang = str(lang).strip()[:32]

    content_lang = header_value(response_headers, "content-language")
    if content_lang and not html_lang:
        html_lang = content_lang.strip()[:32]

    return TechStackBlock(
        html_lang=html_lang,
        cms_or_engine=cms,
        server_header=server,
        powered_by=powered,
        signals=signals[:8],
    )


def host_variants(netloc: str) -> tuple[str, str | None]:
    """Return (canonical non-www host, www host or None if already www-only)."""
    host = netloc.lower()
    if host.startswith("www."):
        return host[4:], host
    return host, f"www.{host}" if host else None


async def analyze_server_checks(
    effective_url: str,
    main_headers: dict[str, str],
) -> list[RecommendationItem]:
    """Redirects, response compression, security headers, custom 404."""
    items: list[RecommendationItem] = []
    parsed = urlparse(effective_url)
    if not parsed.scheme or not parsed.netloc:
        return items

    base_host, www_host = host_variants(parsed.netloc)
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""

    # HTTP → HTTPS
    http_url = f"http://{parsed.netloc}{path}{query}"
    http_status, http_final, _, http_err = await fetch_probe(http_url, follow_redirects=True)
    if http_err:
        items.append(
            RecommendationItem(
                id="redirect_http_https",
                category="server",
                status="warning",
                title="Редирект HTTP → HTTPS",
                value=f"Не удалось проверить: {http_err}",
                recommendation=(
                    "Убедитесь, что http:// открывается и отдаёт 301/302 на https:// "
                    "на уровне веб-сервера или CDN."
                ),
            )
        )
    else:
        final_p = urlparse(http_final)
        https_ok = final_p.scheme == "https"
        if https_ok:
            items.append(
                RecommendationItem(
                    id="redirect_http_https",
                    category="server",
                    status="ok",
                    title="Редирект HTTP → HTTPS",
                    value=f"http:// → {http_final} (HTTP {http_status})",
                    recommendation="Редирект на HTTPS настроен корректно.",
                )
            )
        else:
            items.append(
                RecommendationItem(
                    id="redirect_http_https",
                    category="server",
                    status="error",
                    title="Редирект HTTP → HTTPS",
                    value=f"После запроса http:// финальный URL: {http_final}",
                    recommendation=(
                        "Настройте принудительный 301 редирект с HTTP на HTTPS "
                        "(nginx: return 301 https://$host$request_uri; или аналог в Apache/CDN)."
                    ),
                )
            )

    # www → non-www (если есть пара www / bare)
    if www_host and base_host:
        www_url = f"https://{www_host}{path}{query}"
        bare_url = f"https://{base_host}{path}{query}"
        w_status, w_final, _, w_err = await fetch_probe(www_url, follow_redirects=True)
        if w_err:
            items.append(
                RecommendationItem(
                    id="redirect_www",
                    category="server",
                    status="warning",
                    title="Канонический хост (www → non-www)",
                    value=f"Проверка www не выполнена: {w_err}",
                    recommendation=(
                        "Выберите один канонический домен (обычно без www) и настройте 301 "
                        "с альтернативного варианта."
                    ),
                )
            )
        else:
            w_final_host = urlparse(w_final).netloc.lower()
            bare_host = base_host.lower()
            if w_final_host == bare_host or w_final_host == f"www.{bare_host}":
                canonical_ok = w_final_host == bare_host
                if canonical_ok and w_final.startswith(f"https://{base_host}"):
                    items.append(
                        RecommendationItem(
                            id="redirect_www",
                            category="server",
                            status="ok",
                            title="Канонический хост (www → non-www)",
                            value=f"https://{www_host} → {w_final}",
                            recommendation="www корректно сводится к версии без www.",
                        )
                    )
                elif parsed.netloc.lower().startswith("www."):
                    items.append(
                        RecommendationItem(
                            id="redirect_www",
                            category="server",
                            status="warning",
                            title="Канонический хост (www → non-www)",
                            value=f"Страница на www; финал www-запроса: {w_final}",
                            recommendation=(
                                f"Рекомендуется 301 с https://{www_host} на https://{base_host} "
                                "и единый canonical без www."
                            ),
                        )
                    )
                else:
                    items.append(
                        RecommendationItem(
                            id="redirect_www",
                            category="server",
                            status="ok",
                            title="Канонический хост",
                            value=f"Основной URL без www; www → {w_final}",
                            recommendation="При необходимости добавьте редирект www → non-www для единообразия.",
                        )
                    )
            else:
                items.append(
                    RecommendationItem(
                        id="redirect_www",
                        category="server",
                        status="warning",
                        title="Канонический хост (www → non-www)",
                        value=f"https://{www_host} → {w_final} (ожидался {bare_url})",
                        recommendation=(
                            f"Настройте 301 с www на https://{base_host} "
                            "и укажите canonical на выбранный хост."
                        ),
                    )
                )

    enc = header_value(main_headers, "content-encoding")
    if enc:
        enc_l = enc.lower()
        if "gzip" in enc_l or "br" in enc_l or "deflate" in enc_l:
            items.append(
                RecommendationItem(
                    id="content_encoding",
                    category="server",
                    status="ok",
                    title="Content-Encoding",
                    value=enc,
                    recommendation="Сжатие ответа включено — это ускоряет загрузку.",
                )
            )
        else:
            items.append(
                RecommendationItem(
                    id="content_encoding",
                    category="server",
                    status="warning",
                    title="Content-Encoding",
                    value=enc,
                    recommendation="Включите gzip или Brotli для HTML/CSS/JS на сервере или CDN.",
                )
            )
    else:
        items.append(
            RecommendationItem(
                id="content_encoding",
                category="server",
                status="warning",
                title="Content-Encoding",
                value="Заголовок не найден",
                recommendation=(
                    "Включите gzip/br (nginx gzip on; brotli — модуль или CDN) "
                    "для текстовых ресурсов."
                ),
            )
        )

    hsts = header_value(main_headers, "strict-transport-security")
    if hsts:
        items.append(
            RecommendationItem(
                id="hsts",
                category="security",
                status="ok",
                title="Strict-Transport-Security",
                value=hsts[:200],
                recommendation="HSTS настроен — браузеры будут принудительно использовать HTTPS.",
            )
        )
    else:
        items.append(
            RecommendationItem(
                id="hsts",
                category="security",
                status="warning",
                title="Strict-Transport-Security",
                value="Заголовок отсутствует",
                recommendation=(
                    "Добавьте Strict-Transport-Security с max-age ≥ 31536000 "
                    "(только после стабильного HTTPS)."
                ),
            )
        )

    xfo_val = header_value(main_headers, "x-frame-options")
    csp_val = header_value(main_headers, "content-security-policy") or ""
    if xfo_val:
        items.append(
            RecommendationItem(
                id="x_frame_options",
                category="security",
                status="ok",
                title="X-Frame-Options",
                value=xfo_val,
                recommendation="Защита от clickjacking через X-Frame-Options включена.",
            )
        )
    elif "frame-ancestors" in csp_val.lower():
        items.append(
            RecommendationItem(
                id="x_frame_options",
                category="security",
                status="ok",
                title="X-Frame-Options / CSP frame-ancestors",
                value=csp_val[:200],
                recommendation="Ограничение встраивания задано через CSP.",
            )
        )
    else:
        items.append(
            RecommendationItem(
                id="x_frame_options",
                category="security",
                status="warning",
                title="X-Frame-Options",
                value="Заголовок отсутствует",
                recommendation=(
                    "Добавьте X-Frame-Options: SAMEORIGIN или DENY, "
                    "либо CSP: frame-ancestors 'self'."
                ),
            )
        )

    origin = f"{parsed.scheme}://{parsed.netloc}"
    not_found_url = urljoin(origin + "/", NOT_FOUND_PROBE_PATH.lstrip("/"))
    nf_status, _, _, nf_err = await fetch_probe(not_found_url, follow_redirects=False)
    if nf_err:
        items.append(
            RecommendationItem(
                id="custom_404",
                category="server",
                status="warning",
                title="Страница 404",
                value=f"Проверка не выполнена: {nf_err}",
                recommendation=(
                    f"Убедитесь, что несуществующий URL (например {NOT_FOUND_PROBE_PATH}) "
                    "возвращает HTTP 404, а не 200 с контентом главной."
                ),
            )
        )
    elif nf_status == 404:
        items.append(
            RecommendationItem(
                id="custom_404",
                category="server",
                status="ok",
                title="Страница 404",
                value=f"GET {NOT_FOUND_PROBE_PATH} → HTTP 404",
                recommendation="Сервер корректно отдаёт 404 для несуществующих URL.",
            )
        )
    elif nf_status in (301, 302, 307, 308):
        items.append(
            RecommendationItem(
                id="custom_404",
                category="server",
                status="warning",
                title="Страница 404",
                value=f"GET {NOT_FOUND_PROBE_PATH} → HTTP {nf_status} (редирект)",
                recommendation="Для несуществующих путей предпочтителен ответ 404, а не редирект на главную.",
            )
        )
    else:
        items.append(
            RecommendationItem(
                id="custom_404",
                category="server",
                status="error",
                title="Страница 404 (soft 404)",
                value=f"GET {NOT_FOUND_PROBE_PATH} → HTTP {nf_status}",
                recommendation=(
                    "Настройте отдачу настоящего 404: soft 404 (200 OK) мешает индексации "
                    "и путает поисковые системы."
                ),
            )
        )

    return items


def detect_favicon(soup: BeautifulSoup, page_url: str) -> tuple[bool, str | None]:
    for selector in (
        'link[rel="icon"]',
        'link[rel="shortcut icon"]',
        'link[rel="apple-touch-icon"]',
    ):
        link = soup.select_one(selector)
        if link and link.get("href"):
            return True, resolve_href(link["href"], page_url)
    return False, None


def rec_item(
    *,
    id: str,
    category: str,
    status: str,
    title: str,
    current: str,
    expected: str,
    solution: str,
    praise: str = "",
    char_count: int | None = None,
    detail_urls: list[str] | None = None,
) -> RecommendationItem:
    return RecommendationItem(
        id=id,
        category=category,
        status=status,
        title=title,
        value=current,
        expected=expected,
        recommendation=solution,
        praise=praise,
        char_count=char_count,
        detail_urls=detail_urls or [],
    )


def build_checks(
    meta: MetaBlock,
    schema: SchemaBlock,
    headings: HeadingsBlock,
    images: ImagesBlock,
    links: LinksBlock,
    security: SecurityBlock,
    robots: TechFileBlock,
    sitemap: TechFileBlock,
) -> list[CheckItem]:
    checks: list[CheckItem] = []

    if meta.title_status == "error":
        checks.append(CheckItem(id="title", level="crit", message="Отсутствует <title>"))
    elif meta.title_status == "warning":
        checks.append(
            CheckItem(
                id="title",
                level="warn",
                message=f"Title: {meta.title_char_count} симв. (рекомендуется 30–65)",
            )
        )
    else:
        checks.append(
            CheckItem(
                id="title",
                level="pass",
                message=f"Title: {meta.title_char_count} симв.",
            )
        )

    if not meta.description:
        checks.append(CheckItem(id="description", level="warn", message="Нет meta description"))
    elif meta.description_status == "warning":
        checks.append(
            CheckItem(
                id="description",
                level="warn",
                message=f"Description: {meta.description_char_count} симв. (рекомендуется 70–160)",
            )
        )
    else:
        checks.append(
            CheckItem(
                id="description",
                level="pass",
                message=f"Description: {meta.description_char_count} симв.",
            )
        )

    if meta.canonical:
        checks.append(CheckItem(id="canonical", level="pass", message="Canonical указан"))
    else:
        checks.append(CheckItem(id="canonical", level="warn", message="Canonical не найден"))

    robots_l = meta.robots.lower()
    if "noindex" in robots_l:
        checks.append(
            CheckItem(
                id="robots",
                level="crit",
                message="meta robots: noindex — страница не индексируется",
            )
        )
    elif "nofollow" in robots_l:
        checks.append(CheckItem(id="robots", level="warn", message="meta robots: nofollow"))
    else:
        checks.append(
            CheckItem(id="robots", level="pass", message="meta robots без noindex/nofollow")
        )

    og_missing = [
        name
        for name, val in (
            ("og:title", meta.og_title),
            ("og:description", meta.og_description),
            ("og:image", meta.og_image),
        )
        if not val
    ]
    if len(og_missing) == 3:
        checks.append(CheckItem(id="og", level="warn", message="Open Graph не настроен"))
    elif og_missing:
        checks.append(
            CheckItem(id="og", level="warn", message=f"OG: отсутствует {', '.join(og_missing)}")
        )
    else:
        checks.append(
            CheckItem(id="og", level="pass", message="Open Graph (title, description, image)")
        )

    checks.append(build_checks_schema(schema))

    if headings.h1_count == 0:
        checks.append(CheckItem(id="h1", level="crit", message="Нет H1"))
    elif headings.h1_count > 1:
        checks.append(
            CheckItem(id="h1", level="crit", message=f"H1: {headings.h1_count} (нужен один)")
        )
    else:
        checks.append(CheckItem(id="h1", level="pass", message="Один H1 на странице"))

    if headings.total > 0:
        if headings.hierarchy_ok:
            checks.append(
                CheckItem(id="headings", level="pass", message="Иерархия заголовков корректна")
            )
        else:
            checks.append(
                CheckItem(
                    id="headings",
                    level="warn",
                    message="Пропуски уровней в иерархии заголовков",
                )
            )

    if images.total == 0:
        checks.append(CheckItem(id="img-alt", level="info", message="Изображений не найдено"))
    elif images.missing_alt_count:
        checks.append(
            CheckItem(
                id="img-alt",
                level="warn",
                message=f"{images.missing_alt_count} изображений без alt",
            )
        )
    else:
        checks.append(CheckItem(id="img-alt", level="pass", message="У всех img есть alt"))

    if links.total == 0:
        checks.append(CheckItem(id="links", level="info", message="Ссылок не найдено"))
    elif len(links.unsafe_blank_urls) > 0:
        checks.append(
            CheckItem(
                id="links",
                level="warn",
                message=f"{len(links.unsafe_blank_urls)} ссылок с target=_blank без rel=\"noopener noreferrer\"",
            )
        )
    else:
        checks.append(CheckItem(id="links", level="pass", message="Ссылки: rel/target в порядке"))

    if security.is_https:
        checks.append(CheckItem(id="ssl", level="pass", message="HTTPS"))
    else:
        checks.append(CheckItem(id="ssl", level="crit", message="Сайт не на HTTPS"))

    if security.has_viewport:
        checks.append(CheckItem(id="viewport", level="pass", message="Meta viewport найден"))
    else:
        checks.append(
            CheckItem(id="viewport", level="crit", message="Нет meta viewport (мобильность)")
        )

    if security.has_favicon:
        checks.append(CheckItem(id="favicon", level="pass", message="Favicon найден"))
    else:
        checks.append(CheckItem(id="favicon", level="warn", message="Favicon не найден"))

    if robots.available:
        checks.append(CheckItem(id="robots_txt", level="pass", message="robots.txt доступен"))
    else:
        checks.append(
            CheckItem(
                id="robots_txt",
                level="warn",
                message="robots.txt недоступен или ошибка загрузки",
            )
        )

    if sitemap.available:
        checks.append(CheckItem(id="sitemap", level="pass", message="sitemap.xml доступен"))
    else:
        checks.append(
            CheckItem(
                id="sitemap",
                level="warn",
                message="sitemap.xml недоступен или ошибка загрузки",
            )
        )

    return checks


def build_checks_schema(schema: SchemaBlock) -> CheckItem:
    if schema.found:
        label = ", ".join(schema.types[:6])
        return CheckItem(
            id="schema_org",
            level="pass",
            message=f"Schema.org: {label}",
        )
    return CheckItem(
        id="schema_org",
        level="warn",
        message="Микроразметка Schema.org не найдена",
    )


def level_to_status(level: str) -> str:
    if level == "crit":
        return "error"
    if level == "warn":
        return "warning"
    return "ok"


CHECK_TITLES: dict[str, str] = {
    "title": "Title",
    "description": "Meta Description",
    "canonical": "Canonical URL",
    "robots": "Meta Robots",
    "og": "Open Graph",
    "schema_org": "Schema.org (микроразметка)",
    "h1": "Заголовок H1",
    "headings": "Иерархия H1–H6",
    "img-alt": "Alt у изображений",
    "links": "Ссылки (rel / target)",
    "ssl": "HTTPS",
    "viewport": "Meta Viewport",
    "favicon": "Favicon",
    "robots_txt": "robots.txt",
    "sitemap": "sitemap.xml",
}


def check_category(check_id: str) -> str:
    mapping = {
        "title": "meta",
        "description": "meta",
        "canonical": "meta",
        "robots": "meta",
        "og": "meta",
        "schema_org": "meta",
        "h1": "headings",
        "headings": "headings",
        "img-alt": "images",
        "links": "links",
        "ssl": "security",
        "viewport": "security",
        "favicon": "security",
        "robots_txt": "robots",
        "sitemap": "sitemap",
    }
    return mapping.get(check_id, "meta")


EXPECTED_STANDARDS: dict[str, str] = {
    "title": "Title: 30–65 символов, уникальный, с ключевой фразой и брендом.",
    "description": "Meta description: 70–160 символов, понятное описание страницы для сниппета.",
    "canonical": "Один canonical URL на страницу, совпадающий с индексируемой версией.",
    "robots": "Без noindex/nofollow, если страница должна индексироваться и передавать ссылочный вес.",
    "og": "Заполнены og:title, og:description и og:image для соцсетей и мессенджеров.",
    "schema_org": "JSON-LD или Microdata: Organization, WebSite, Product, FAQ и др. по типу страницы.",
    "h1": "Ровно один H1, отражающий главную тему документа.",
    "headings": "Последовательная иерархия H1→H2→H3 без пропусков уровней.",
    "img-alt": "У каждого значимого изображения осмысленный alt (декоративным — alt=\"\").",
    "links": "У ссылок с target=\"_blank\" атрибут rel=\"noopener noreferrer\".",
    "ssl": "Страница и все ресурсы доступны только по HTTPS.",
    "viewport": "Meta viewport для корректного мобильного отображения.",
    "favicon": "Favicon в <link rel=\"icon\"> для узнаваемости в вкладке и выдаче.",
    "robots_txt": "Файл /robots.txt доступен по HTTP 200 в корне домена.",
    "sitemap": "Актуальный /sitemap.xml и ссылка Sitemap: в robots.txt.",
    "html_lang": "Атрибут lang на <html> (например lang=\"ru\").",
}


def solution_for_check(check_id: str) -> str:
    tips: dict[str, str] = {
        "title": "Добавьте или отредактируйте <title> в <head>, уложив текст в 30–65 символов.",
        "description": "Заполните <meta name=\"description\" content=\"…\"> в диапазоне 70–160 символов.",
        "canonical": "Добавьте <link rel=\"canonical\" href=\"https://…\"> на канонический URL.",
        "robots": "Удалите noindex/nofollow из meta robots или HTTP X-Robots-Tag, если страница нужна в поиске.",
        "og": "Добавьте мета-теги og:title, og:description и og:image в разметку.",
        "schema_org": (
            "Добавьте блок <script type=\"application/ld+json\"> с типами Organization, WebSite "
            "или подходящими для страницы — так улучшаются расширенные сниппеты в Google и Yandex."
        ),
        "h1": "Оставьте один главный <h1>; остальные ключевые блоки оформите H2–H3.",
        "headings": "Исправьте порядок заголовков, не перескакивая уровни (например H2 → H3).",
        "img-alt": "Пропишите alt для перечисленных URL или alt=\"\" для чисто декоративных.",
        "links": "Добавьте rel=\"noopener noreferrer\" к каждой ссылке с target=\"_blank\" из списка.",
        "ssl": "Установите TLS-сертификат и настройте 301 редирект с HTTP на HTTPS.",
        "viewport": "Добавьте <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.",
        "favicon": "Подключите favicon: <link rel=\"icon\" href=\"/favicon.ico\" sizes=\"any\">.",
        "robots_txt": "Создайте /robots.txt с правилами User-agent и при необходимости Sitemap.",
        "sitemap": "Сгенерируйте sitemap.xml (CMS или вручную) и укажите путь в robots.txt.",
        "html_lang": "Укажите lang на элементе <html>, например <html lang=\"ru\">.",
    }
    return tips.get(check_id, "Устраните замечание согласно SEO-чеклисту.")


def praise_for_check(check_id: str) -> str:
    praises: dict[str, str] = {
        "title": "Длина title в норме — заголовок в сниппете не обрежется некорректно.",
        "description": "Description попадает в рекомендуемый диапазон для поисковых сниппетов.",
        "canonical": "Canonical задан — риск дублей по URL снижен.",
        "robots": "Meta robots не блокирует индексацию страницы.",
        "og": "Open Graph настроен — превью в соцсетях будет полным.",
        "schema_org": "Микроразметка найдена — вы на правильном пути к расширенным сниппетам.",
        "h1": "На странице один H1 — структура понятна поисковым системам.",
        "headings": "Иерархия заголовков выстроена логично.",
        "img-alt": "У всех изображений указан alt — это плюс для SEO и доступности.",
        "links": "Ссылки с target=\"_blank\" защищены rel=\"noopener noreferrer\".",
        "ssl": "Страница отдаётся по HTTPS.",
        "viewport": "Viewport настроен — мобильная версия отображается корректно.",
        "favicon": "Favicon подключён.",
        "robots_txt": "robots.txt доступен краулерам.",
        "sitemap": "sitemap.xml доступен — карта сайта помогает обходу.",
        "html_lang": "Язык страницы явно указан для поисковиков и assistive tech.",
    }
    return praises.get(check_id, "Параметр соответствует лучшим практикам SEO.")


def current_for_check(
    c: CheckItem,
    meta: MetaBlock,
    schema: SchemaBlock,
    headings: HeadingsBlock,
    images: ImagesBlock,
    links: LinksBlock,
    security: SecurityBlock,
    robots: TechFileBlock,
    sitemap: TechFileBlock,
) -> tuple[str, int | None, list[str]]:
    detail_urls: list[str] = []
    char_count: int | None = None

    if c.id == "title":
        char_count = meta.title_char_count
        if not meta.title:
            return "Отсутствует (0 символов)", char_count, detail_urls
        return f"«{meta.title}» — {meta.title_char_count} символов", char_count, detail_urls

    if c.id == "description":
        char_count = meta.description_char_count
        if not meta.description:
            return "Не задан (0 символов)", char_count, detail_urls
        desc_preview = meta.description[:180] + ("…" if len(meta.description) > 180 else "")
        return f"«{desc_preview}» — {meta.description_char_count} символов", char_count, detail_urls

    if c.id == "schema_org":
        if schema.found:
            fmt = ", ".join(schema.formats) if schema.formats else "разметка"
            types = ", ".join(schema.types)
            return f"Schema.org ({fmt}): {types}", None, detail_urls
        return "Микроразметка JSON-LD / Microdata не обнаружена", None, detail_urls

    if c.id == "canonical":
        return meta.canonical or "Canonical не указан", None, detail_urls
    if c.id == "robots":
        return meta.robots or "(meta robots не задан)", None, detail_urls
    if c.id == "og":
        parts = [
            f"og:title={'✓' if meta.og_title else '—'}",
            f"og:description={'✓' if meta.og_description else '—'}",
            f"og:image={'✓' if meta.og_image else '—'}",
        ]
        return "; ".join(parts), None, detail_urls
    if c.id == "h1":
        return f"H1 на странице: {headings.h1_count} (всего заголовков: {headings.total})", None, detail_urls
    if c.id == "headings":
        if headings.warnings:
            return "; ".join(headings.warnings[:3]), None, detail_urls
        return "Пропусков уровней не обнаружено", None, detail_urls
    if c.id == "img-alt":
        detail_urls = list(images.missing_alt_urls)
        if images.total == 0:
            return "На странице нет тегов <img>", None, detail_urls
        if images.missing_alt_count == 0:
            return f"Все {images.total} изображений с alt", None, detail_urls
        return f"{images.missing_alt_count} из {images.total} изображений без alt", None, detail_urls
    if c.id == "links":
        detail_urls = list(links.unsafe_blank_urls)
        if not detail_urls:
            return (
                f"Ссылки в порядке (внутр.: {links.internal}, внешн.: {links.external})",
                None,
                detail_urls,
            )
        return (
            f"{len(detail_urls)} ссылок с target=\"_blank\" без rel=\"noopener noreferrer\"",
            None,
            detail_urls,
        )
    if c.id == "ssl":
        return "HTTPS" if security.is_https else "Страница загружена по HTTP", None, detail_urls
    if c.id == "viewport":
        return security.viewport_content or "Meta viewport отсутствует", None, detail_urls
    if c.id == "favicon":
        return security.favicon_href or "Favicon не найден", None, detail_urls
    if c.id == "robots_txt":
        if robots.available:
            return f"Доступен, HTTP {robots.status_code}, {robots.size_bytes} байт", None, detail_urls
        return robots.error or "Файл недоступен", None, detail_urls
    if c.id == "sitemap":
        if sitemap.available:
            return f"Доступен, HTTP {sitemap.status_code}, {sitemap.size_bytes} байт", None, detail_urls
        return sitemap.error or "Файл недоступен", None, detail_urls

    return c.message, char_count, detail_urls


def enrich_server_recommendation(rec: RecommendationItem) -> RecommendationItem:
    templates: dict[str, dict[str, str]] = {
        "redirect_http_https": {
            "expected": "Любой запрос по http:// автоматически перенаправляется на https:// (301).",
            "praise_ok": "Редирект HTTP → HTTPS настроен — трафик идёт на защищённую версию.",
        },
        "redirect_www": {
            "expected": "Выбран один канонический хост (обычно без www), альтернатива отдаёт 301.",
            "praise_ok": "Канонический домен согласован — дубли по www/non-www минимизированы.",
        },
        "content_encoding": {
            "expected": "Content-Encoding: gzip или br для HTML и статики.",
            "praise_ok": "Сжатие ответа включено — страница грузится быстрее.",
        },
        "hsts": {
            "expected": "Заголовок Strict-Transport-Security с достаточным max-age.",
            "praise_ok": "HSTS активен — браузеры принудительно используют HTTPS.",
        },
        "x_frame_options": {
            "expected": "X-Frame-Options или CSP frame-ancestors ограничивает встраивание.",
            "praise_ok": "Защита от clickjacking через заголовки включена.",
        },
        "custom_404": {
            "expected": "Несуществующий URL возвращает HTTP 404 (не 200 и не soft 404).",
            "praise_ok": "Сервер корректно отдаёт 404 для битых URL.",
        },
    }
    tpl = templates.get(rec.id, {})
    expected = rec.expected or tpl.get("expected", "Соответствие техническим SEO-стандартам.")
    praise = rec.praise
    if rec.status == "ok" and not praise:
        praise = tpl.get("praise_ok", rec.recommendation)
    return rec.model_copy(update={"expected": expected, "praise": praise})


def build_recommendations(
    checks: list[CheckItem],
    meta: MetaBlock,
    schema: SchemaBlock,
    headings: HeadingsBlock,
    images: ImagesBlock,
    links: LinksBlock,
    security: SecurityBlock,
    robots: TechFileBlock,
    sitemap: TechFileBlock,
    tech: TechStackBlock,
    server_items: list[RecommendationItem],
) -> list[RecommendationItem]:
    items: list[RecommendationItem] = []

    for c in checks:
        status = level_to_status(c.level)
        current, char_count, detail_urls = current_for_check(
            c, meta, schema, headings, images, links, security, robots, sitemap
        )
        check_id = c.id
        expected = EXPECTED_STANDARDS.get(check_id, "Соответствие рекомендациям SEO.")
        solution = solution_for_check(check_id)
        praise = praise_for_check(check_id) if status == "ok" else ""

        if check_id == "schema_org" and schema.found:
            types_preview = ", ".join(schema.types[:8])
            praise = f"Обнаружены типы: {types_preview}. Микроразметка помогает расширенным сниппетам."

        items.append(
            rec_item(
                id=check_id,
                category=check_category(check_id),
                status=status,
                title=CHECK_TITLES.get(check_id, c.message),
                current=current,
                expected=expected,
                solution=solution,
                praise=praise,
                char_count=char_count,
                detail_urls=detail_urls,
            )
        )

    if tech.html_lang:
        items.append(
            rec_item(
                id="html_lang",
                category="meta",
                status="ok",
                title="Язык страницы",
                current=f"lang=\"{tech.html_lang}\"",
                expected=EXPECTED_STANDARDS["html_lang"],
                solution=solution_for_check("html_lang"),
                praise=praise_for_check("html_lang"),
            )
        )
    else:
        items.append(
            rec_item(
                id="html_lang",
                category="meta",
                status="warning",
                title="Язык страницы",
                current="Атрибут lang на <html> не найден",
                expected=EXPECTED_STANDARDS["html_lang"],
                solution=solution_for_check("html_lang"),
            )
        )

    if tech.cms_or_engine:
        items.append(
            rec_item(
                id="cms_engine",
                category="server",
                status="ok",
                title="CMS / движок",
                current=tech.cms_or_engine,
                expected="Определён стек сайта для точечных SEO-рекомендаций.",
                solution="Используйте SEO-модуль CMS и следите за обновлениями.",
                praise="Движок определён — можно применять специфичные для CMS настройки SEO.",
            )
        )
    else:
        sig = "; ".join(tech.signals[:3]) if tech.signals else "Явных сигнатур CMS не найдено"
        items.append(
            rec_item(
                id="cms_engine",
                category="server",
                status="ok",
                title="CMS / движок",
                current=sig,
                expected="Понимание стека для сопровождения SEO.",
                solution="Документируйте стек и заголовки безопасности в проекте.",
                praise="Сайт на кастомном или headless-стеке — контролируйте SEO вручную.",
            )
        )

    for srv in server_items:
        items.append(enrich_server_recommendation(srv))

    return items


def compute_seo_score(checks: list[CheckItem], recommendations: list[RecommendationItem] | None = None) -> int:
    score = 100
    weights = {"crit": 12, "warn": 5, "info": 0, "pass": 0}
    for c in checks:
        score -= weights.get(c.level, 0)
    if recommendations:
        rec_weights = {"error": 8, "warning": 4, "ok": 0}
        seen: set[str] = set()
        for r in recommendations:
            if r.id in seen:
                continue
            seen.add(r.id)
            if r.category == "server" or r.id in (
                "hsts",
                "x_frame_options",
                "content_encoding",
                "redirect_http_https",
                "redirect_www",
                "custom_404",
            ):
                score -= rec_weights.get(r.status, 0)
    return max(0, min(100, score))


def score_label(score: int) -> str:
    if score >= 80:
        return "Отлично"
    if score >= 50:
        return "Требует внимания"
    return "Критично"


def parse_html_audit(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(page_url)

    title_el = soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""
    desc_el = soup.find("meta", attrs={"name": "description"})
    description = (desc_el.get("content") or "").strip() if desc_el else ""
    canonical_el = soup.find("link", rel=lambda r: r and "canonical" in r)
    canonical = resolve_href(canonical_el.get("href"), page_url) if canonical_el else None
    robots_el = soup.find("meta", attrs={"name": "robots"})
    robots = (robots_el.get("content") or "").strip() if robots_el else ""

    og_title_el = soup.find("meta", property="og:title")
    og_desc_el = soup.find("meta", property="og:description")
    og_image_el = soup.find("meta", property="og:image")

    title_count = len(title)
    desc_count = len(description)
    meta = MetaBlock(
        title=title,
        title_char_count=title_count,
        title_status=meta_field_status(title_count, TITLE_LEN_OK, empty_error=True),
        description=description,
        description_char_count=desc_count,
        description_status=meta_field_status(desc_count, DESC_LEN_OK, empty_error=False),
        canonical=canonical,
        robots=robots,
        og_title=og_title_el.get("content") if og_title_el else None,
        og_description=og_desc_el.get("content") if og_desc_el else None,
        og_image=resolve_href(og_image_el.get("content"), page_url) if og_image_el else None,
    )

    schema_org = analyze_schema(soup)
    headings = analyze_headings(soup)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    images = analyze_images(soup, origin)
    links = analyze_links(soup, page_url)

    viewport_el = soup.find("meta", attrs={"name": "viewport"})
    has_favicon, favicon_href = detect_favicon(soup, page_url)

    security = SecurityBlock(
        is_https=parsed.scheme == "https",
        has_viewport=viewport_el is not None,
        viewport_content=viewport_el.get("content") if viewport_el else None,
        has_favicon=has_favicon,
        favicon_href=favicon_href,
    )

    return {
        "meta": meta,
        "schema_org": schema_org,
        "headings": headings,
        "images": images,
        "links": links,
        "security": security,
    }


@app.get("/api")
async def root() -> dict[str, str]:
    return {"service": "SEO Analyzer API", "docs": "/docs", "analyze": "POST /api/analyze"}


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    page_url = normalize_page_url(str(body.url))

    try:
        html, fetch_meta = await fetch_url_resilient(page_url)
        if html is None:
            message = humanize_fetch_error(fetch_meta)
            logger.error(
                "analyze fetch failed url=%s status=%s message=%s fetch=%s",
                page_url,
                fetch_meta.status_code,
                message,
                fetch_meta.model_dump(),
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": message,
                    "fetch": fetch_meta.model_dump(),
                },
            )

        effective_url = fetch_meta.final_url or page_url
        audit = parse_html_audit(html, effective_url)

        parsed = urlparse(effective_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        robots_task = fetch_tech_file(origin, "/robots.txt")
        sitemap_task = fetch_tech_file(origin, "/sitemap.xml")
        server_task = analyze_server_checks(effective_url, fetch_meta.response_headers)
        robots_txt, sitemap_xml, server_recs = await asyncio.gather(
            robots_task, sitemap_task, server_task
        )

        soup = BeautifulSoup(html, "lxml")
        tech = detect_tech_stack(html, soup, fetch_meta.response_headers)

        checks = build_checks(
            audit["meta"],
            audit["schema_org"],
            audit["headings"],
            audit["images"],
            audit["links"],
            audit["security"],
            robots_txt,
            sitemap_xml,
        )
        recommendations = build_recommendations(
            checks,
            audit["meta"],
            audit["schema_org"],
            audit["headings"],
            audit["images"],
            audit["links"],
            audit["security"],
            robots_txt,
            sitemap_xml,
            tech,
            server_recs,
        )
        seo_score = compute_seo_score(checks, recommendations)

        analyzed_at = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

        return AnalyzeResponse(
            success=True,
            page_url=effective_url,
            analyzed_at=analyzed_at,
            fetch=fetch_meta,
            seo_score=seo_score,
            score_label=score_label(seo_score),
            checks=checks,
            recommendations=recommendations,
            tech=tech,
            meta=audit["meta"],
            schema_org=audit["schema_org"],
            headings=audit["headings"],
            images=audit["images"],
            links=audit["links"],
            security=audit["security"],
            robots_txt=robots_txt,
            sitemap_xml=sitemap_xml,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("analyze unexpected error url=%s", page_url)
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Внутренняя ошибка при анализе страницы. Попробуйте другой URL или повторите позже.",
                "reason": str(exc),
            },
        ) from exc


_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if os.getenv("VERCEL") != "1" and _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
