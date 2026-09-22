import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from quota_monitor.config import Config
from quota_monitor.reset_feed import collect_candidates, _NoRedirect


NOW = 1789992000


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def row(number, **changes):
    return dict(id=str(number), url=f'https://x.com/thsottiaux/status/{number}',
                text='Ordinary update', at=iso(NOW - 120), **changes)


def feed(**changes):
    return dict(profile={'handle': 'thsottiaux'}, stale=False,
                fetched_at=iso(NOW - 30), tweets=[row(100)], **changes)


class Response(io.BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = headers or {}


class ResetFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config({'accounts': [{'username': 'thsottiaux'}],
                              'browser': {'reset_feed_enabled': True}}, self.tmp.name)

    def call(self, payload=None, headers=None, now=NOW):
        with patch('quota_monitor.reset_feed._open', return_value=Response(payload or feed(), headers)) as opener:
            result = collect_candidates(self.config, now=now)
        return result, opener

    def test_disabled_and_unmonitored_do_not_request(self):
        for change in ('disabled', 'unmonitored'):
            if change == 'disabled':
                self.config.data['browser'] = {}
            else:
                self.config.data['browser'] = {'reset_feed_enabled': True}
                self.config.data['accounts'] = [{'username': 'other'}]
            result, opener = self.call()
            self.assertEqual(result['status'], 'disabled')
            opener.assert_not_called()

    def test_fixed_request_and_only_candidate_metadata_leaves_module(self):
        payload = feed()
        payload['tweets'][0].update(kind='reset', prediction='SECRET', text='Reset soon')
        result, opener = self.call(payload)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['candidates'], [{'id': '100', 'username': 'thsottiaux',
                                               'url': 'https://x.com/thsottiaux/status/100'}])
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, 'https://codex-reset.com/api/feed')
        self.assertEqual(request.get_method(), 'GET')
        self.assertLessEqual(opener.call_args.kwargs['timeout'], 12)
        self.assertIn('CodexQuotaResetMonitor', request.get_header('User-agent'))

    def test_stale_missing_or_future_fetch_rejected(self):
        for stale, fetched in [(True, iso(NOW)), (None, iso(NOW)),
                               (False, iso(NOW - 901)), (False, iso(NOW + 301)),
                               (False, 'bad'), (False, '2026-09-21T12:00:00')]:
            with self.subTest(stale=stale, fetched=fetched):
                payload = feed()
                payload.update(stale=stale, fetched_at=fetched)
                with patch('quota_monitor.reset_feed._open', return_value=Response(payload)):
                    result = collect_candidates(self.config, now=NOW)
                self.assertEqual(result['status'], 'stale')
                self.assertEqual(result['candidates'], [])
                self.cache().unlink(missing_ok=True)

    def cache(self):
        return self.config.database.parent / 'reset-feed-cache.json'

    def test_expired_publish_header_is_stale(self):
        result, _ = self.call(headers={'x-published-expires-at': iso(NOW - 1)})
        self.assertEqual(result['status'], 'stale')

    def test_bad_urls_ids_author_and_row_dates_ignored(self):
        payload = feed()
        payload['tweets'] = [row(100), row(101), row(102), row(103), row(104), row(105), row(106)]
        payload['tweets'][1]['url'] = 'https://x.com/evil/status/101'
        payload['tweets'][2]['id'] = '١٠٢'
        payload['tweets'][3]['url'] = 'https://x.com/thsottiaux/status/999'
        payload['tweets'][4].update(at=iso(NOW - 90000), inferred_at=iso(NOW))
        payload['tweets'][5]['at'] = iso(NOW + 301)
        payload['tweets'][6]['url'] += '?redirect=evil'
        result, _ = self.call(payload)
        self.assertEqual([x['id'] for x in result['candidates']], ['100'])

    def test_declared_time_is_used_not_inferred(self):
        payload = feed()
        payload['tweets'] = [row(100, declared_at=iso(NOW - 90000), inferred_at=iso(NOW))]
        result, _ = self.call(payload)
        self.assertEqual(result['candidates'], [])

    def test_priority_reserved_then_newest_with_radar_and_duplicates(self):
        payload = feed(radar_context=[row(300), row(299), row(100)])
        payload['tweets'] = [row(i) for i in range(100, 106)]
        for item in payload['tweets']:
            item['text'] = '额度重置 Reset'
        payload['tweets'][0]['prediction'] = 'do not copy'
        result, _ = self.call(payload)
        self.assertEqual([x['id'] for x in result['candidates']], ['105', '104', '103', '300', '299'])
        self.assertNotIn('text', str(result))

    def test_parent_requires_valid_older_id_and_handle(self):
        payload = feed()
        payload['tweets'] = [row(100, in_reply_to_tweet_id='90', replying_to='OpenAI'),
                             row(101, in_reply_to_tweet_id='102', replying_to='OpenAI'),
                             row(102, in_reply_to_tweet_id='91', replying_to='evil/path')]
        result, _ = self.call(payload)
        items = {x['id']: x for x in result['candidates']}
        self.assertEqual(items['100']['parent_id'], '90')
        self.assertEqual(items['100']['parent_username'], 'OpenAI')
        self.assertNotIn('parent_id', items['101'])
        self.assertNotIn('parent_id', items['102'])

    def test_duplicate_rows_fill_missing_parent_without_losing_existing_parent(self):
        payload = feed(radar_context=[row(100, in_reply_to_tweet_id='90', replying_to='OpenAI'),
                                     row(101), row(102, in_reply_to_tweet_id='92', replying_to='openai')])
        payload['tweets'] = [row(100), row(101, in_reply_to_tweet_id='91', replying_to='OpenAI'),
                             row(102, in_reply_to_tweet_id='92', replying_to='OpenAI')]
        result, _ = self.call(payload)
        items = {item['id']: item for item in result['candidates']}
        self.assertEqual(items['100']['parent_id'], '90')
        self.assertEqual(items['101']['parent_id'], '91')
        self.assertEqual(items['102']['parent_username'], 'OpenAI')

    def test_conflicting_duplicate_parents_removed_and_not_reenriched(self):
        payload = feed(radar_context=[row(100, in_reply_to_tweet_id='91', replying_to='OpenAI'),
                                     row(100, in_reply_to_tweet_id='90', replying_to='OpenAI'),
                                     row(101, in_reply_to_tweet_id='90', replying_to='other')])
        payload['tweets'] = [row(100, in_reply_to_tweet_id='90', replying_to='OpenAI'),
                             row(101, in_reply_to_tweet_id='90', replying_to='OpenAI')]
        result, _ = self.call(payload)
        self.assertEqual({item['id'] for item in result['candidates']}, {'100', '101'})
        for item in result['candidates']:
            self.assertNotIn('parent_id', item)
            self.assertNotIn('parent_username', item)

    def test_cache_throttles_across_calls_and_rechecks_freshness(self):
        self.call()
        result, opener = self.call(now=NOW + 30)
        opener.assert_not_called()
        self.assertEqual(result['status'], 'ok')
        _, opener = self.call(now=NOW + 61)
        opener.assert_called_once()

    def test_invalid_json_and_oversize_are_safe_errors_with_backoff(self):
        for raw in (b'not json secret', b'x' * (2 * 1024 * 1024 + 1)):
            with patch('quota_monitor.reset_feed._open', return_value=Response(raw)):
                result = collect_candidates(self.config, now=NOW)
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('secret', str(result))
            result, opener = self.call(now=NOW + 30)
            opener.assert_not_called()
            self.assertEqual(result['status'], 'backoff')
            self.cache().unlink(missing_ok=True)

    def test_429_retry_after_and_network_error(self):
        error = HTTPError('https://codex-reset.com/api/feed', 429, 'secret', {'Retry-After': '180'}, None)
        with patch('quota_monitor.reset_feed._open', side_effect=error):
            self.assertEqual(collect_candidates(self.config, now=NOW)['status'], 'backoff')
        result, opener = self.call(now=NOW + 120)
        self.assertEqual(result['status'], 'backoff')
        opener.assert_not_called()
        self.cache().unlink()
        with patch('quota_monitor.reset_feed._open', side_effect=URLError('secret credential')):
            result = collect_candidates(self.config, now=NOW)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('credential', str(result))

    def test_retry_after_http_date_persists_and_5xx_falls_back(self):
        retry_date = format_datetime(datetime.fromtimestamp(NOW + 240, timezone.utc), usegmt=True)
        error = HTTPError('https://codex-reset.com/api/feed', 429, 'busy', {'Retry-After': retry_date}, None)
        with patch('quota_monitor.reset_feed._open', side_effect=error):
            self.assertEqual(collect_candidates(self.config, now=NOW)['status'], 'backoff')
        result, opener = self.call(now=NOW + 200)
        self.assertEqual(result['status'], 'backoff')
        opener.assert_not_called()
        self.cache().unlink()
        error = HTTPError('https://codex-reset.com/api/feed', 503, 'busy', {}, None)
        with patch('quota_monitor.reset_feed._open', side_effect=error):
            self.assertEqual(collect_candidates(self.config, now=NOW)['status'], 'error')

    def test_prediction_and_radar_text_never_affect_priority(self):
        payload = feed(radar_context=[row(90)])
        payload['radar_context'][0]['text'] = 'Reset'
        payload['tweets'] = [row(100, prediction='reset', kind='reset'), row(101), row(102)]
        self.config.data['browser']['max_posts'] = 2
        result, _ = self.call(payload)
        self.assertEqual([item['id'] for item in result['candidates']], ['102', '101'])

    def test_invalid_profile_rejected_and_missing_dates_not_inferred(self):
        payload = feed()
        payload['profile']['handle'] = 'other'
        result, _ = self.call(payload)
        self.assertEqual(result['status'], 'error')
        self.cache().unlink()
        payload = feed()
        payload['tweets'][0].pop('at')
        payload['tweets'][0]['inferred_at'] = iso(NOW)
        result, _ = self.call(payload)
        self.assertEqual(result['candidates'], [])

    def test_redirects_are_refused_even_same_host(self):
        handler = _NoRedirect()
        for target in ('https://evil.example/', 'https://codex-reset.com/login'):
            self.assertIsNone(handler.redirect_request(None, None, 302, 'redirect', {}, target))

    def test_cache_write_failure_prevents_unthrottled_network(self):
        with patch('quota_monitor.reset_feed._write_cache', return_value=False):
            result, opener = self.call()
        self.assertEqual(result['status'], 'error')
        opener.assert_not_called()

    def test_truncated_http_response_is_a_safe_fallback(self):
        with patch('quota_monitor.reset_feed._open', side_effect=IncompleteRead(b'secret')):
            result = collect_candidates(self.config, now=NOW)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('secret', str(result))

    def test_unsafe_user_agent_falls_back_without_leaking(self):
        self.config.data['browser']['reset_feed_user_agent'] = 'agent\r\nAuthorization: secret'
        _, opener = self.call()
        self.assertNotIn('secret', opener.call_args.args[0].get_header('User-agent'))

    def test_public_project_user_agent_contact_is_preserved(self):
        value = 'CodexQuotaResetMonitor/0.1 (+https://github.com/owner/repo)'
        self.config.data['browser']['reset_feed_user_agent'] = value
        _, opener = self.call()
        self.assertEqual(opener.call_args.args[0].get_header('User-agent'), value)


if __name__ == '__main__':
    unittest.main()
