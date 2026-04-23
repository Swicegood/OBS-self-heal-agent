"""Scoped Thruk TAC health: count CRITICAL/DOWN/etc only in HTML rows matching a service + host.

Reuses the same login + tac.cgi fetch pattern as `lan-monitoring/scripts/thruk_status.py`
(stdlib only), but filters by row so aggregate page noise does not affect policy."""

from __future__ import annotations

import html
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from obs_self_heal.config import AppConfig
from obs_self_heal.models import PublicStreamHealth

_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.DOTALL | re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r"(?is)<script[^>]*>.*?</script>|<style[^>]*>.*?</style>")
_TAG_RE = re.compile(r"<[^>]+>")
_SERVICE_STATUS_RE = re.compile(r"\b(OK|WARNING|CRITICAL|UNKNOWN)\b", re.IGNORECASE)
_CURRENT_STATUS_RE = re.compile(r"current\s+status\s*[:\-]?\s*(OK|WARNING|CRITICAL|UNKNOWN)", re.IGNORECASE)
_NOT_FOUND_RE = re.compile(r"\b(no such host|no such service|not found|does not exist)\b", re.IGNORECASE)


def _html_to_visible_text(raw: str) -> str:
    """Approximate visible text for substring search (handles split tags)."""
    t = _SCRIPT_STYLE_RE.sub(" ", raw)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(tr|div|p|li|h\d)\s*>", "\n", t, flags=re.I)
    t = _TAG_RE.sub(" ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def _extract_service_status_from_html(html_raw: str) -> tuple[str | None, str | None]:
    """Best-effort extraction of service state from a service detail-like HTML page."""
    txt = _html_to_visible_text(html_raw)
    if _NOT_FOUND_RE.search(txt):
        return None, "scoped_object_not_found"
    m = _CURRENT_STATUS_RE.search(txt)
    if m:
        return m.group(1).upper(), None

    # Fallback: take the first status token after a "Current Status" label if formatting is odd.
    i = txt.lower().find("current status")
    if i != -1:
        window = txt[i : i + 400]
        m2 = _SERVICE_STATUS_RE.search(window)
        if m2:
            return m2.group(1).upper(), None

    return None, "service_status_not_found"


def _extract_service_status_from_status_html(html_raw: str, service_name: str) -> tuple[str | None, str | None]:
    """Extract a service state from status.cgi-style HTML by proximity in visible text."""
    txt = _html_to_visible_text(html_raw)
    if _NOT_FOUND_RE.search(txt):
        return None, "scoped_object_not_found"
    svc = service_name.strip().lower()
    if not svc:
        return None, "scope_filter_incomplete"

    low = txt.lower()
    pos = 0
    while True:
        i = low.find(svc, pos)
        if i == -1:
            break
        window = txt[i : i + 600]
        m = _SERVICE_STATUS_RE.search(window)
        if m:
            return m.group(1).upper(), None
        pos = i + 1
    return None, "service_status_not_found"

def _service_health_from_status(status: str, *, stdout: str, elapsed: float) -> PublicStreamHealth:
    s = status.upper()
    crit = 1 if s in ("CRITICAL", "UNKNOWN") else 0
    warn = 1 if s == "WARNING" else 0
    return PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout=stdout,
        stderr="",
        critical_count=crit,
        down_count=0,
        warning_count=warn,
        unreachable_count=0,
        parse_error=None,
        elapsed_sec=elapsed,
    )


def _build_service_detail_url(base: str, host_name: str, service_name: str) -> str:
    q = urllib.parse.urlencode({"type": "2", "host": host_name, "service": service_name})
    return f"{base}/thruk/cgi-bin/extinfo.cgi?{q}"


def _build_host_status_url(base: str, host_name: str) -> str:
    q = urllib.parse.urlencode({"host": host_name, "style": "detail"})
    return f"{base}/thruk/cgi-bin/status.cgi?{q}"

def _count_in_proximity(
    html_raw: str,
    service_substring: str,
    host_substrings: list[str],
    window_chars: int,
) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    """Match service + host within a sliding text window (raw HTML or visible text)."""
    svc = service_substring.strip()
    hosts = [h.lower() for h in host_substrings if h.strip()]
    if not svc or not hosts:
        return None, None, None, None, "scope_filter_incomplete"

    # Try unescaped HTML string first (entity boundaries).
    candidates: list[str] = [html.unescape(html_raw), _html_to_visible_text(html_raw)]

    for blob in candidates:
        low = blob.lower()
        svc_l = svc.lower()
        pos = 0
        while True:
            i = low.find(svc_l, pos)
            if i == -1:
                break
            start = max(0, i - window_chars)
            end = min(len(blob), i + len(svc_l) + window_chars)
            win = blob[start:end]
            win_l = win.lower()
            if any(h in win_l for h in hosts):
                crit = len(re.findall(r"\bCRITICAL\b", win, re.I))
                warn = len(re.findall(r"\bWARNING\b", win, re.I))
                down = len(re.findall(r"\bDOWN\b", win, re.I))
                unr = len(re.findall(r"\bUNREACHABLE\b", win, re.I))
                return crit, warn, down, unr, None
            pos = i + 1

    return None, None, None, None, "scoped_service_row_not_found"


def count_scoped_status_keywords(
    html: str,
    service_substring: str,
    host_substrings: list[str],
    proximity_window_chars: int,
) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    """Prefer same-`<tr>` match; then proximity in HTML / visible text."""
    c, w, d, u, err = _count_in_rows(html, service_substring, host_substrings)
    if err is None:
        return c, w, d, u, None
    return _count_in_proximity(html, service_substring, host_substrings, proximity_window_chars)


def _default_creds_path(cfg: AppConfig) -> str:
    raw = cfg.thruk.env.get("MONITORING_CREDS_FILE", "~/.openclaw/credentials/monitoring-lan.json")
    return str(Path(raw).expanduser())


def _load_creds(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    base = data.get("baseUrl", "https://192.168.0.77/monitoring").rstrip("/")
    login = data.get("login")
    password = data.get("password")
    if not login or not password:
        raise ValueError("monitoring credentials must include login and password")
    return {"baseUrl": base, "login": login, "password": password}


def _build_opener() -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    jar = CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )


def _fetch(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: dict[str, str] | None = None,
    timeout_sec: float = 60.0,
) -> tuple[int, str]:
    if data:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
    else:
        req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=float(timeout_sec)) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, raw


def _count_in_rows(
    html: str,
    service_substring: str,
    host_substrings: list[str],
) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    svc = service_substring.lower()
    hosts = [h.lower() for h in host_substrings if h.strip()]
    if not svc or not hosts:
        return None, None, None, None, "scope_filter_incomplete"

    crit = warn = down = unr = 0
    matched = False
    for row in _ROW_RE.findall(html):
        low = row.lower()
        if svc not in low:
            continue
        if not any(h in low for h in hosts):
            continue
        matched = True
        crit += len(re.findall(r"\bCRITICAL\b", row, re.I))
        warn += len(re.findall(r"\bWARNING\b", row, re.I))
        down += len(re.findall(r"\bDOWN\b", row, re.I))
        unr += len(re.findall(r"\bUNREACHABLE\b", row, re.I))

    if not matched:
        return None, None, None, None, "scoped_service_row_not_found"
    return crit, warn, down, unr, None


def check_public_stream_health_scoped(cfg: AppConfig) -> PublicStreamHealth:
    scope = cfg.thruk.scope
    assert scope is not None and scope.enabled

    creds_path = _default_creds_path(cfg)
    start = time.perf_counter()
    err_out = ""

    try:
        creds = _load_creds(creds_path)
    except OSError as e:
        elapsed = time.perf_counter() - start
        return PublicStreamHealth(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=str(e),
            parse_error="scoped_creds_unreadable",
            elapsed_sec=elapsed,
        )
    except ValueError as e:
        elapsed = time.perf_counter() - start
        return PublicStreamHealth(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=str(e),
            parse_error="scoped_creds_invalid",
            elapsed_sec=elapsed,
        )

    opener = _build_opener()
    base = creds["baseUrl"]
    login_url = f"{base}/thruk/cgi-bin/login.cgi"
    timeout_sec = float(scope.request_timeout_sec)

    try:
        _fetch(opener, login_url, None, timeout_sec=timeout_sec)
        post = {
            "login": creds["login"],
            "password": creds["password"],
            "submit": "Login",
        }
        code, body = _fetch(opener, login_url, post, timeout_sec=timeout_sec)
        if code not in (200, 302):
            err_out = body[:800]
            elapsed = time.perf_counter() - start
            return PublicStreamHealth(
                ok=False,
                exit_code=1,
                stdout="",
                stderr=err_out,
                parse_error="scoped_login_failed",
                elapsed_sec=elapsed,
            )

        # Preferred deterministic path: fetch the service detail page directly.
        if scope.host_name.strip() and scope.service_name.strip():
            url = _build_service_detail_url(base, scope.host_name.strip(), scope.service_name.strip())
            code_s, html_s = _fetch(opener, url, timeout_sec=timeout_sec)
            if code_s != 200:
                elapsed = time.perf_counter() - start
                # Thruk often returns 404 with a useful error page; treat as object-not-found when it says so.
                if _NOT_FOUND_RE.search(_html_to_visible_text(html_s)):
                    return PublicStreamHealth(
                        ok=False,
                        exit_code=1,
                        stdout=f"extinfo_object_not_found url={url} code={code_s}\n",
                        stderr=_html_to_visible_text(html_s)[:1200],
                        parse_error="scoped_object_not_found",
                        elapsed_sec=elapsed,
                    )
                return PublicStreamHealth(
                    ok=False,
                    exit_code=1,
                    stdout=f"extinfo_http_error url={url} code={code_s}\n",
                    stderr=_html_to_visible_text(html_s)[:1200],
                    parse_error=f"scoped_service_http_error_{code_s}",
                    elapsed_sec=elapsed,
                )

            status, perr = _extract_service_status_from_html(html_s)
            elapsed = time.perf_counter() - start
            if perr or not status:
                # Fallback: status.cgi host view, then find the service line.
                if perr == "scoped_object_not_found":
                    url2 = _build_host_status_url(base, scope.host_name.strip())
                    code_h, html_h = _fetch(opener, url2, timeout_sec=timeout_sec)
                    if code_h == 200:
                        st2, err2 = _extract_service_status_from_status_html(html_h, scope.service_name.strip())
                        if err2 is None and st2:
                            summary = f"service_state (status.cgi): {scope.host_name!r}/{scope.service_name!r} -> {st2}\n"
                            return _service_health_from_status(st2, stdout=summary, elapsed=elapsed)
                return PublicStreamHealth(
                    ok=False,
                    exit_code=1,
                    stdout=f"extinfo_parse_error url={url}\n",
                    stderr=_html_to_visible_text(html_s)[:1200],
                    parse_error=perr or "service_status_not_found",
                    elapsed_sec=elapsed,
                )
            summary = f"service_state (extinfo): {scope.host_name!r}/{scope.service_name!r} -> {status}\n"
            return _service_health_from_status(status, stdout=summary, elapsed=elapsed)

        tac_url = f"{base}/thruk/cgi-bin/tac.cgi"
        code2, html = _fetch(opener, tac_url, timeout_sec=timeout_sec)
        if code2 != 200:
            elapsed = time.perf_counter() - start
            return PublicStreamHealth(
                ok=False,
                exit_code=1,
                stdout="",
                stderr=html[:1200],
                parse_error="scoped_tac_http_error",
                elapsed_sec=elapsed,
            )

        if scope.delegate_public_to_openclaw:
            max_c = max(1000, int(scope.openclaw_tac_html_max_chars))
            excerpt = html[:max_c]
            truncated = len(html) > max_c
            elapsed = time.perf_counter() - start
            summary = (
                f"Thruk TAC HTML delegated to OpenClaw (page_bytes={len(html)}, "
                f"excerpt_chars={len(excerpt)}, truncated={truncated})\n"
            )
            return PublicStreamHealth(
                ok=True,
                exit_code=0,
                stdout=summary,
                stderr="",
                critical_count=None,
                down_count=None,
                warning_count=None,
                unreachable_count=None,
                parse_error=None,
                elapsed_sec=elapsed,
                public_evaluation_delegated=True,
                tac_html_excerpt=excerpt,
                tac_html_truncated=truncated,
            )

        crit, warn, down, unr, perr = count_scoped_status_keywords(
            html,
            scope.service_substring,
            scope.host_substrings,
            scope.proximity_window_chars,
        )
        summary = (
            f"keyword hits (scoped): CRITICAL={crit} WARNING={warn} DOWN={down} "
            f"UNREACHABLE={unr} service={scope.service_substring!r}"
        )
        elapsed = time.perf_counter() - start
        ok = perr is None
        return PublicStreamHealth(
            ok=ok,
            exit_code=0 if ok else 1,
            stdout=summary + "\n",
            stderr="",
            critical_count=crit,
            down_count=down,
            warning_count=warn,
            unreachable_count=unr,
            parse_error=perr,
            elapsed_sec=elapsed,
        )
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        return PublicStreamHealth(
            ok=False,
            exit_code=1,
            stdout="",
            stderr=f"{type(e).__name__}: {e}",
            parse_error="scoped_fetch_error",
            elapsed_sec=elapsed,
        )
