from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import AppConfig
from .models import Post

log = logging.getLogger(__name__)

LIST_SELECTORS = [
    "tr.ub-content.us-post",
    "tr.ub-content",
    "tbody tr",
]
TITLE_SELECTORS = [
    "td.gall_tit a",
    ".gall_tit a",
    "a[href*='board/view']",
]
WRITER_SELECTORS = [
    ".gall_writer",
    "td.gall_writer",
    "[data-nick]",
]
CONTENT_IMAGE_SELECTORS = [
    ".appending_file_box img",
    ".write_div img",
    ".writing_view_box img",
    ".view_content_wrap img",
    "img[src*='dcimg']",
]
NOTICE_POST_TYPES = {"icon_notice"}
IMAGE_POST_TYPES = {"icon_pic", "main_img"}
EXCLUDE_SUBJECTS = {"공지", "설문", "이슈"}
VIEWIMAGE_LINK_SELECTORS = [
    "a[href*='viewimage']",
]
EXCLUDE_IMAGE_PATTERNS = [
    "profile",
    "banner",
    "ad.",
    "/ad/",
    "icon",
    "logo",
    "dccon",
    "sticker",
    "captcha",
    "comment_box",
    "loading_btntype",
    "_images",
]
EXCLUDE_IMAGE_HOSTS = {
    "nstatic.dcinside.com",
}
DCINSIDE_IMAGE_HOST_MARKERS = (
    "dcinside.com",
    "dcinside.co.kr",
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DCWatch/0.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
}


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchOptions:
    timeout: float = 10.0


def build_list_url(config: AppConfig, page: int) -> str:
    root = "https://gall.dcinside.com/mgallery/board/lists/" if config.gallery_type == "minor" else "https://gall.dcinside.com/board/lists/"
    query = urlencode({"id": config.gallery_id, "page": page})
    return f"{root}?{query}"


def fetch_recent_posts(config: AppConfig) -> list[Post]:
    posts: list[Post] = []
    seen: set[str] = set()
    for page in range(1, max(1, config.pages_to_scan) + 1):
        url = build_list_url(config, page)
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        response.raise_for_status()
        for post in extract_posts_from_html(response.text, url):
            if post.post_no not in seen:
                posts.append(post)
                seen.add(post.post_no)
    return posts


def extract_posts_from_html(html: str, base_url: str) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    rows = _select_first_nonempty(soup, LIST_SELECTORS)
    for row in rows:
        if _is_excluded_list_row(row):
            continue
        post_no = _post_no_from_row(row)
        if not post_no or not post_no.isdigit():
            continue
        title_link = _first_match(row, TITLE_SELECTORS)
        if title_link is None or not title_link.get("href"):
            continue
        title = _clean_text(title_link.get_text(" ", strip=True))
        url = urljoin(base_url, title_link["href"])
        if "board/view" not in url:
            continue
        writer_node = _first_match(row, WRITER_SELECTORS)
        writer = _writer_from_node(writer_node)
        post_type = _post_type_from_row(row)
        posts.append(Post(post_no=post_no, title=title, url=url, writer=writer, has_image=_row_has_image(row), post_type=post_type))
    return posts


def fetch_post_image_urls(post_url: str) -> list[str]:
    response = requests.get(post_url, headers=DEFAULT_HEADERS | {"Referer": post_url}, timeout=10)
    response.raise_for_status()
    return extract_image_urls_from_html(response.text, post_url)


def extract_image_urls_from_html(html: str, post_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for node in _iter_selector_matches(soup, CONTENT_IMAGE_SELECTORS):
        src = node.get("data-original") or node.get("data-src") or node.get("src")
        if src:
            _append_image_url(urls, src, post_url)
    for node in _iter_selector_matches(soup, VIEWIMAGE_LINK_SELECTORS):
        href = node.get("href")
        if href:
            _append_image_url(urls, href, post_url, allow_viewimage=True)
    return _dedupe(urls)


def download_image_bytes(image_url: str, referer: str, max_bytes: int) -> bytes:
    headers = DEFAULT_HEADERS | {"Referer": referer, "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    with requests.get(image_url, headers=headers, timeout=15, stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        is_viewimage = "viewimage" in image_url.lower()
        if "image" not in content_type and not is_viewimage:
            raise DownloadError(f"content-type is not image: {content_type}")
        length = response.headers.get("content-length")
        if length and int(length) > max_bytes:
            raise DownloadError("image exceeds configured size limit")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise DownloadError("image exceeds configured size limit")
            chunks.append(chunk)
        if total == 0:
            raise DownloadError("empty image response")
        return b"".join(chunks)


def _select_first_nonempty(soup: BeautifulSoup, selectors: Iterable[str]):
    for selector in selectors:
        matches = soup.select(selector)
        if matches:
            return matches
    return []


def _iter_selector_matches(soup: BeautifulSoup, selectors: Iterable[str]):
    seen: set[int] = set()
    for selector in selectors:
        for node in soup.select(selector):
            marker = id(node)
            if marker not in seen:
                seen.add(marker)
                yield node


def _first_match(node, selectors: Iterable[str]):
    for selector in selectors:
        match = node.select_one(selector)
        if match is not None:
            return match
    return None


def _post_no_from_row(row) -> str:
    value = row.get("data-no") or ""
    if value.isdigit():
        return value
    number_node = row.select_one(".gall_num")
    if number_node is None:
        return ""
    match = re.search(r"\d+", number_node.get_text(" ", strip=True))
    return match.group(0) if match else ""


def _is_excluded_list_row(row) -> bool:
    post_type = _post_type_from_row(row)
    if post_type in NOTICE_POST_TYPES:
        return True
    subject_node = row.select_one(".gall_subject")
    subject = _clean_text(subject_node.get_text(" ", strip=True)) if subject_node else ""
    return subject in EXCLUDE_SUBJECTS


def _post_type_from_row(row) -> str:
    value = row.get("data-type") or ""
    if value:
        return _clean_text(value)
    icon = row.select_one(".gall_tit .icon_img")
    if icon is None:
        return ""
    classes = icon.get("class") or []
    return next((str(class_name) for class_name in classes if str(class_name).startswith("icon_") and class_name != "icon_img"), "")


def _row_has_image(row) -> bool:
    post_type = _post_type_from_row(row)
    if post_type in IMAGE_POST_TYPES:
        return True
    icon = row.select_one(".gall_tit .icon_pic")
    return icon is not None


def _writer_from_node(node) -> str:
    if node is None:
        return ""
    for attr in ("data-nick", "title"):
        value = node.get(attr)
        if value:
            return _clean_text(value)
    return _clean_text(node.get_text(" ", strip=True))


def _append_image_url(urls: list[str], raw_url: str, post_url: str, allow_viewimage: bool = False) -> None:
    url = urljoin(post_url, raw_url.strip())
    parsed = urlparse(url)
    lowered = url.lower()
    if not parsed.scheme.startswith("http"):
        return
    if parsed.netloc.lower() in EXCLUDE_IMAGE_HOSTS:
        return
    if any(pattern in lowered for pattern in EXCLUDE_IMAGE_PATTERNS):
        return
    if allow_viewimage or _looks_like_image_url(lowered):
        urls.append(url)


def _looks_like_image_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif")):
        return True
    params = parse_qs(parsed.query)
    if _is_dcinside_image_host(host) and "viewimage" in path and {"id", "no"}.issubset(params):
        return True
    return any("image" in key.lower() or "file" in key.lower() for key in params)


def _is_dcinside_image_host(host: str) -> bool:
    return any(marker in host for marker in DCINSIDE_IMAGE_HOST_MARKERS)


def _dedupe(urls: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
