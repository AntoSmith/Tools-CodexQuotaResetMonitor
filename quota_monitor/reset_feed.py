"""Optional discovery hints; every candidate still needs first-party X verification.

The public feed is a curated radar, not a complete account timeline. Its text and
classifications must never become monitor evidence or notification content.
"""

import json
import math
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE = 'https://codex-reset.com/'
ENDPOINT = SOURCE + 'api/feed'
DEFAULT_USER_AGENT = 'CodexQuotaResetMonitor/0.1 (personal local monitor)'
MAX_BYTES = 2 * 1024 * 1024
HANDLE = 'thsottiaux'
_ID = re.compile(r'[0-9]{1,25}', re.ASCII)
_HANDLE = re.compile(r'[A-Za-z0-9_]{1,15}', re.ASCII)
_RESET = re.compile(r'reset|重置|刷新额度', re.IGNORECASE)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request, *, timeout):
    # No auth handler or redirects: credentials and off-host fetches are excluded.
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _number(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value if math.isfinite(value) else default


def _result(status, payload=None, reason=None):
    fetched = payload.get('fetched_at') if isinstance(payload, dict) else None
    result = {'status': status, 'source': SOURCE,
              'fetched_at': fetched if _timestamp(fetched) is not None else None,
              'candidates': []}
    if reason:
        result['reason'] = reason
    return result


def _valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _candidates(payload, expires, now, config):
    fetched = _timestamp(payload.get('fetched_at'))
    expiry = _timestamp(expires) if expires is not None else None
    if (payload.get('stale') is not False or fetched is None
            or not now - 900 <= fetched <= now + 300
            or (expires is not None and (expiry is None or expiry <= now))):
        return _result('stale', payload, 'feed_not_fresh')
    profile = payload.get('profile')
    if not isinstance(profile, dict) or str(profile.get('handle', '')).lower() != HANDLE:
        return _result('error', payload, 'unexpected_profile')
    age = max(0, _number(config.section('monitor').get('max_age_hours'), 24)) * 3600
    limit = max(1, min(100, int(_number(config.section('browser').get('max_posts'), 5))))
    merged, priority, parent_conflicts = {}, set(), set()
    for collection in ('tweets', 'radar_context'):
        rows = payload.get(collection, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            ident = row.get('id')
            if not _valid_id(ident):
                continue
            url = row.get('url')
            if not isinstance(url, str) or url.lower() != f'https://x.com/{HANDLE}/status/{ident}':
                continue
            declared = row.get('declared_at')
            at = _timestamp(declared if declared is not None else row.get('at'))
            if at is None or not now - age <= at <= now + 300:
                continue
            candidate = {'id': ident, 'username': HANDLE,
                         'url': f'https://x.com/{HANDLE}/status/{ident}'}
            parent = row.get('in_reply_to_tweet_id')
            parent_handle = row.get('replying_to')
            if (_valid_id(parent) and int(parent) < int(ident)
                    and isinstance(parent_handle, str) and _HANDLE.fullmatch(parent_handle)):
                candidate.update(parent_id=parent, parent_username=parent_handle)
            existing = merged.setdefault(ident, candidate)
            if ident not in parent_conflicts and 'parent_id' in candidate:
                if 'parent_id' not in existing:
                    existing.update(parent_id=candidate['parent_id'],
                                    parent_username=candidate['parent_username'])
                elif (existing['parent_id'] != candidate['parent_id']
                      or existing['parent_username'].lower() != candidate['parent_username'].lower()):
                    # Contradictory hints cannot establish a reply association.
                    existing.pop('parent_id')
                    existing.pop('parent_username')
                    parent_conflicts.add(ident)
            text = row.get('text')
            if collection == 'tweets' and isinstance(text, str) and _RESET.search(text):
                priority.add(ident)
    selected = sorted(priority, key=int, reverse=True)[:(limit + 1) // 2]
    selected += [ident for ident in sorted(merged, key=int, reverse=True)
                 if ident not in selected][:limit - len(selected)]
    result = _result('ok', payload)
    result['candidates'] = [merged[ident] for ident in selected]
    return result


def _read_cache(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 4097)
        if len(raw) > MAX_BYTES + 4096:
            return {}
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _write_cache(path, value):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist the attempted request before networking, including across restarts.
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_BYTES + 4096:
            return False
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(raw)
        temporary.replace(path)
        return True
    except (OSError, ValueError, RecursionError):
        return False


def _retry_until(header, now):
    try:
        seconds = float(header)
        if math.isfinite(seconds):
            return now + max(60, seconds)
    except (TypeError, ValueError):
        pass
    try:
        date = parsedate_to_datetime(header)
        if date.tzinfo is not None:
            return max(now + 60, date.timestamp())
    except (TypeError, ValueError, OverflowError):
        pass
    return now + 60


def _user_agent(config):
    value = config.section('browser').get('reset_feed_user_agent', DEFAULT_USER_AGENT)
    # Explicit public identifier only; never derive this from notification settings.
    # Syntax validation cannot establish whether user-provided text is secret.
    if (not isinstance(value, str) or not 10 <= len(value) <= 250
            or not re.fullmatch(r'[\x20-\x7e]+', value)):
        return DEFAULT_USER_AGENT
    return value


def collect_candidates(config, now=None):
    """Return only validated discovery IDs/URLs, with no third-party prose."""
    if (config.section('browser').get('reset_feed_enabled', False) is not True
            or not any(str(account.get('username', '')).lower() == HANDLE for account in config.accounts)):
        return _result('disabled')
    now = time.time() if now is None else float(now)
    path = Path(config.database).parent / 'reset-feed-cache.json'
    cache = _read_cache(path)
    walltime = _number(cache.get('cache_walltime'), 0)
    retry = _number(cache.get('retry_until'), 0)
    if retry > now:
        return _result('backoff', reason='retry_wait')
    if walltime > 0 and now - walltime < 60:
        payload = cache.get('payload')
        if isinstance(payload, dict):
            return _candidates(payload, cache.get('expires_at'), now, config)
        return _result('backoff', reason='minimum_interval')
    attempt = {'cache_walltime': now, 'retry_until': now + 60}
    if not _write_cache(path, attempt):
        return _result('error', reason='cache_unavailable')
    try:
        request = Request(ENDPOINT, headers={'User-Agent': _user_agent(config),
                                            'Accept': 'application/json'}, method='GET')
        with _open(request, timeout=12) as response:
            raw = response.read(MAX_BYTES + 1)
            expires = response.headers.get('x-published-expires-at')
        if len(raw) > MAX_BYTES:
            return _result('error', reason='response_too_large')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return _result('error', reason='invalid_payload')
        # Compact JSON avoids excessive whitespace occupying the cache budget.
        cached = {'cache_walltime': now, 'retry_until': 0,
                  'payload': payload, 'expires_at': expires}
        _write_cache(path, cached)
        return _candidates(payload, expires, now, config)
    except HTTPError as error:
        error.close()
        if error.code == 429:
            attempt['retry_until'] = _retry_until(error.headers.get('Retry-After') if error.headers else None, now)
            _write_cache(path, attempt)
            return _result('backoff', reason='rate_limited')
        return _result('error', reason='http_error')
    except (OSError, HTTPException, ValueError, TypeError, RecursionError):
        return _result('error', reason='feed_unavailable')
