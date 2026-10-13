"""Public divin.cc discovery and explicitly attributed website assessments.

Website prose is metadata, never authored X text or classifier input. Every
candidate still requires the monitored author's complete first-party X page.
"""

import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE = 'https://divin.cc/'
ENDPOINTS = (SOURCE + 'api/reset-events', SOURCE + 'api/reset-feed')
USER_AGENT = 'CodexQuotaResetMonitor/0.1 (personal local monitor)'
MAX_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 2 * MAX_BYTES + 4096
MAX_ASSESSMENTS = 200
_HANDLE = re.compile(r'[A-Za-z0-9_]{1,15}', re.ASCII)
_X_URL = re.compile(r'https://x\.com/([A-Za-z0-9_]{1,15})/status/([0-9]{1,25})/?', re.ASCII)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request, *, timeout):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _iso(now):
    try:
        return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError, OSError):
        return ''


def _number(value, default):
    return value if type(value) in (int, float) and math.isfinite(value) else default


def _line(value, limit):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _link(value):
    match = _X_URL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    handle, ident = match.group(1).lower(), match.group(2)
    return ident, handle, f'https://x.com/{handle}/status/{ident}'


def _links(values):
    result = []
    if not isinstance(values, list):
        return result
    for value in values[:200]:
        if isinstance(value, dict):
            value = value.get('url') or value.get('sourceUrl')
        link = _link(value)
        if link and link[2] not in result:
            result.append(link[2])
            if len(result) == 6:
                break
    return result


def normalize_assessment(metadata):
    """Strict public metadata projection usable by ingestion and history reads."""
    if (not isinstance(metadata, dict) or metadata.get('provider') != 'divin.cc'
            or metadata.get('source_url') != SOURCE):
        return None
    stamp = _timestamp(metadata.get('fetched_at'))
    return {'provider': 'divin.cc', 'source_url': SOURCE,
            'fetched_at': _iso(stamp) if stamp is not None else '',
            **{key: _line(metadata.get(key), limit) for key, limit in
               (('event_type', 120), ('scope', 500), ('reason', 1000),
                ('stage', 120), ('analysis_basis', 1000))},
            'source_urls': _links(metadata.get('source_urls'))}


def _assessment(row, fetched_at, source_urls, *, feed=False):
    if feed:
        basis = _line(row.get('reason'), 400)
    else:
        basis = '；'.join(f'{label}：{text}' for label, key, limit in
                         (('事件判断', 'classificationReason', 250), ('分析来源', 'analysisSource', 80),
                          ('范围来源', 'scopeSource', 80), ('站点合并标记', 'mergeSource', 80))
                         if (text := _line(row.get(key), limit)))
    return normalize_assessment({'provider': 'divin.cc', 'source_url': SOURCE,
        'fetched_at': fetched_at, 'event_type': row.get('eventType'),
        'scope': row.get('scope'), 'reason': '' if feed else row.get('reason'),
        'stage': row.get('stage') if feed else row.get('eventStatus'),
        'analysis_basis': basis, 'source_urls': source_urls})


def _merge_feed_assessment(existing, supplemental):
    """Keep calendar causes and scopes distinct from editorial feed judgments."""
    if existing is None:
        return supplemental
    additions = []
    for key, label in (('event_type', '线索类型'), ('stage', '线索阶段')):
        if supplemental[key] and supplemental[key] != existing[key]:
            additions.append(f'{label}：{supplemental[key]}')
    if supplemental['analysis_basis']:
        additions.append('线索判断：' + supplemental['analysis_basis'])
    basis = existing['analysis_basis']
    for addition in additions:
        if addition not in basis:
            basis = '；'.join(value for value in (basis, addition) if value)
    return normalize_assessment({**existing, 'analysis_basis': basis,
                                'source_urls': existing['source_urls'] + supplemental['source_urls']})


def _result(status, *, fetched_at=None, generated_at=None, reason=None):
    result = {'status': status, 'source': SOURCE, 'fetched_at': fetched_at,
              'generated_at': generated_at, 'candidates': [], 'assessments': {}}
    if reason:
        result['reason'] = reason
    return result


def _assemble(cache, config, now, fallback='error'):
    events = cache.get('events')
    feed = cache.get('feed')
    events_fetched = cache.get('events_fetched_at')
    feed_fetched = cache.get('feed_fetched_at')
    stamps = [_timestamp(value) for value in (events_fetched, feed_fetched)]
    stamps = [value for value in stamps if value is not None and now - 86400 <= value <= now + 300]
    fetched_at = _iso(max(stamps)) if stamps else None
    generated_at = feed.get('generatedAt') if isinstance(feed, dict) else None
    generated = _timestamp(generated_at)
    if generated is None:
        generated_at = None
    result = _result(fallback, fetched_at=fetched_at, generated_at=generated_at)
    handles = {account['username'].lower() for account in config.accounts
               if isinstance(account, dict) and isinstance(account.get('username'), str)
               and _HANDLE.fullmatch(account['username'])}
    age = max(0, _number(config.section('monitor').get('max_age_hours'), 24)) * 3600
    limit = max(1, min(20, int(_number(config.section('browser').get('max_posts'), 5))))
    assessments, candidates, conflicts = {}, {}, set()
    valid_endpoint = False

    def add_candidate(link, stamp, metadata):
        ident, username, url = link
        if stamp is None or not now - age <= stamp <= now + 300:
            return None
        candidate = candidates.setdefault(ident, {'id': ident, 'username': username, 'url': url})
        candidate['third_party'] = assessments.get(ident, metadata)
        return candidate

    events_stamp = _timestamp(events_fetched)
    if isinstance(events, list) and events_stamp is not None and now - 86400 <= events_stamp <= now + 300:
        valid_endpoint = True
        # Merge both bounded API responses before applying the output cap;
        # historical calendar rows must not consume the fresh feed budget.
        event_rows = [row for row in events if isinstance(row, dict)]
        event_rows.sort(key=lambda row: int((_link(row.get('sourceUrl')) or ('0', '', ''))[0]), reverse=True)
        for row in event_rows:
            link = _link(row.get('sourceUrl'))
            if (not isinstance(row.get('tool'), str) or row['tool'].lower() != 'codex'
                    or not link or link[1] not in handles):
                continue
            ident = link[0]
            related = row.get('relatedSources')
            metadata = _assessment(row, events_fetched, [link[2]] + (related if isinstance(related, list) else []))
            assessments.setdefault(ident, metadata)
            add_candidate(link, _timestamp(row.get('eventTimeUtc')), metadata)

    feed_stamp = _timestamp(feed_fetched)
    if (isinstance(feed, dict) and isinstance(feed.get('posts'), list)
            and feed_stamp is not None and now - 86400 <= feed_stamp <= now + 300
            and generated is not None and now - 86400 <= generated <= now + 300):
        valid_endpoint = True
        for row in feed['posts']:
            if not isinstance(row, dict):
                continue
            link = _link(row.get('url'))
            handle = row.get('handle')
            if (not link or link[1] not in handles or not isinstance(handle, str)
                    or handle.lower() != link[1] or row.get('id') != link[0]):
                continue
            stamp = _timestamp(row.get('at'))
            if stamp is None:
                continue
            ident = link[0]
            context = row.get('context')
            parent = _link(context.get('url')) if isinstance(context, dict) else None
            metadata = _assessment(row, feed_fetched, [link[2]] + ([parent[2]] if parent else []), feed=True)
            assessments[ident] = _merge_feed_assessment(assessments.get(ident), metadata)
            candidate = add_candidate(link, stamp, metadata)
            if candidate is not None and parent and int(parent[0]) < int(ident) and ident not in conflicts:
                previous = candidate.get('parent_id')
                if previous is None:
                    candidate.update(parent_id=parent[0], parent_username=parent[1])
                elif previous != parent[0] or candidate['parent_username'] != parent[1]:
                    candidate.pop('parent_id', None)
                    candidate.pop('parent_username', None)
                    conflicts.add(ident)
    selected = sorted(candidates, key=int, reverse=True)[:limit]
    # Every selected candidate keeps its assessment. Fill the remaining
    # historical allowance with newest post IDs from the merged source rows.
    retained = selected + [ident for ident in sorted(assessments, key=int, reverse=True)
                           if ident not in selected][:MAX_ASSESSMENTS - len(selected)]
    result['assessments'] = {ident: assessments[ident] for ident in retained}
    result['candidates'] = [{**candidates[ident], 'third_party': assessments[ident]} for ident in selected]
    if valid_endpoint:
        result['status'] = 'ok'
    return result


def _read_cache(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            return {}
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _write_cache(path, value):
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_CACHE_BYTES:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
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
        stamp = parsedate_to_datetime(header)
        if stamp.tzinfo is not None:
            return max(now + 60, stamp.timestamp())
    except (TypeError, ValueError, OverflowError):
        pass
    return now + 60


def collect_candidates(config, now=None):
    """Fetch once per public endpoint, cache across restarts, return safe fields."""
    if (config.section('browser').get('divin_feed_enabled', True) is not True
            or not any(isinstance(account, dict) and isinstance(account.get('username'), str)
                       and _HANDLE.fullmatch(account['username']) for account in config.accounts)):
        return _result('disabled')
    now = time.time() if now is None else _number(now, time.time())
    path = Path(config.database).parent / 'divin-feed-cache.json'
    cache = _read_cache(path)
    walltime = _number(cache.get('cache_walltime'), 0)
    retry = _number(cache.get('retry_until'), 0)
    if retry > now or (walltime > 0 and now - walltime < 60):
        return _assemble(cache, config, now, fallback='backoff')
    # Persist the attempted request before networking; failed runs and restarts
    # cannot evade the public source's minimum request interval.
    attempt = {**cache, 'cache_walltime': now, 'retry_until': now + 60}
    if not _write_cache(path, attempt):
        return _result('error', reason='cache_unavailable')
    cached = dict(attempt)
    rate_limited = False
    for index, endpoint in enumerate(ENDPOINTS):
        name = 'events' if index == 0 else 'feed'
        try:
            request = Request(endpoint, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'}, method='GET')
            with _open(request, timeout=8) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                continue
            payload = json.loads(raw)
            if not ((index == 0 and isinstance(payload, list))
                    or (index == 1 and isinstance(payload, dict) and isinstance(payload.get('posts'), list))):
                continue
            cached[name] = payload
            cached[name + '_fetched_at'] = _iso(now)
        except HTTPError as error:
            error.close()
            if error.code == 429:
                rate_limited = True
                cached['retry_until'] = max(cached['retry_until'], _retry_until(
                    error.headers.get('Retry-After') if error.headers else None, now))
                break
        except (OSError, HTTPException, ValueError, TypeError, RecursionError):
            continue
    if not rate_limited:
        cached['retry_until'] = 0
    _write_cache(path, cached)
    return _assemble(cached, config, now, fallback='backoff' if rate_limited else 'error')
