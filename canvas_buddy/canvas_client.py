"""Thin Canvas LMS REST API client: pagination, retries, rate-limit awareness."""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator
from urllib.parse import urljoin

import requests

log = logging.getLogger(__name__)


class CanvasError(RuntimeError):
    pass


class CanvasAuthError(CanvasError):
    pass


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 30,
        max_retries: int = 4,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_root = f"{self.base_url}/api/v1/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "canvas-buddy/1.0",
            }
        )

    # -- low level ---------------------------------------------------------

    def _request(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:  # network blip
                last_exc = exc
                log.warning("Request error (attempt %s/%s): %s", attempt, self.max_retries, exc)
            else:
                if resp.status_code in (401, 403):
                    raise CanvasAuthError(
                        f"Canvas refused the request ({resp.status_code}). "
                        "The token may be expired, revoked, or lack scope for this course."
                    )
                if resp.status_code == 404:
                    raise CanvasError(f"Not found: {url}")
                if resp.status_code == 429 or resp.status_code >= 500:
                    log.warning(
                        "Canvas returned %s (attempt %s/%s); backing off %.0fs",
                        resp.status_code,
                        attempt,
                        self.max_retries,
                        delay,
                    )
                    last_exc = CanvasError(f"HTTP {resp.status_code}")
                else:
                    resp.raise_for_status()
                    return resp
            if attempt < self.max_retries:
                time.sleep(delay)
                delay *= 2
        raise CanvasError(f"Giving up on {url}: {last_exc}")

    @staticmethod
    def _next_link(resp: requests.Response) -> str | None:
        link = resp.headers.get("Link") or resp.headers.get("link")
        if not link:
            return None
        for part in link.split(","):
            segments = part.split(";")
            if len(segments) < 2:
                continue
            url = segments[0].strip().strip("<>")
            for seg in segments[1:]:
                if seg.strip() in ('rel="next"', "rel=next"):
                    return url
        return None

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Yield every item across all pages of a list endpoint."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        url = urljoin(self.api_root, path.lstrip("/"))
        first = True
        while url:
            resp = self._request(url, params if first else None)
            first = False
            data = resp.json()
            if isinstance(data, dict):
                data = [data]
            for item in data:
                yield item
            url = self._next_link(resp)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = urljoin(self.api_root, path.lstrip("/"))
        return self._request(url, params).json()

    # -- convenience -------------------------------------------------------

    def whoami(self) -> dict:
        return self.get("users/self/profile")

    def active_courses(self) -> list[dict]:
        return list(
            self.paginate(
                "courses",
                {"enrollment_state": "active", "include[]": "term"},
            )
        )

    def course(self, course_id: int, include_syllabus: bool = False) -> dict:
        params = {"include[]": "syllabus_body"} if include_syllabus else None
        return self.get(f"courses/{course_id}", params)

    def announcements(self, course_id: int) -> list[dict]:
        return list(
            self.paginate(
                f"courses/{course_id}/discussion_topics",
                {"only_announcements": "true"},
            )
        )

    def discussions(self, course_id: int) -> list[dict]:
        return list(self.paginate(f"courses/{course_id}/discussion_topics"))

    def discussion_entries(self, course_id: int, topic_id: int) -> list[dict]:
        return list(self.paginate(f"courses/{course_id}/discussion_topics/{topic_id}/entries"))

    def assignments(self, course_id: int) -> list[dict]:
        return list(
            self.paginate(
                f"courses/{course_id}/assignments",
                {"order_by": "due_at"},
            )
        )

    def modules(self, course_id: int) -> list[dict]:
        return list(
            self.paginate(
                f"courses/{course_id}/modules",
                {"include[]": "items"},
            )
        )

    def module_items(self, course_id: int, module_id: int) -> list[dict]:
        return list(self.paginate(f"courses/{course_id}/modules/{module_id}/items"))

    def pages(self, course_id: int) -> list[dict]:
        return list(self.paginate(f"courses/{course_id}/pages", {"sort": "updated_at"}))

    def files(self, course_id: int) -> list[dict]:
        return list(self.paginate(f"courses/{course_id}/files", {"sort": "updated_at"}))
