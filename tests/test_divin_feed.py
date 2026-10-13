import importlib
import importlib.util
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


NOW = 1791849600
SOURCE = 'https://divin.cc/'
ROOT = Path(__file__).resolve().parents[1]


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def event(number=100, **changes):
    return dict({'tool': 'codex', 'sourceUrl': f'https://x.com/thsottiaux/status/{number}',
                'eventTimeUtc': iso(NOW - 120), 'eventType': 'global_reset',
                'eventStatus': 'completed', 'scope': 'Weekly limits for Plus users',
                'reason': 'Compensation after an incident', 'analysisSource': 'Author announcement',
                'title': 'SECRET TITLE', 'summary': 'SECRET SUMMARY', 'credibility': 'SECRET SCORE',
                'relatedSources': ['https://x.com/OpenAI/status/90']}, **changes)


def post(number=100, **changes):
    return dict({'id': str(number), 'handle': 'thsottiaux', 'url': f'https://x.com/thsottiaux/status/{number}',
                'at': iso(NOW - 120), 'original': 'SECRET AUTHOR TEXT', 'reason': 'Website inference',
                'eventType': 'possible_reset', 'stage': 'pending', 'strength': 'SECRET STRENGTH',
                'context': {'text': 'SECRET CONTEXT', 'url': 'https://x.com/OpenAI/status/90'}}, **changes)


def radar(**changes):
    return dict({'generatedAt': iso(NOW - 30), 'source': 'local-codex', 'posts': [post()],
                'signal': 'SECRET SIGNAL'}, **changes)


class Response(io.BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = headers or {}


class DivinFeedTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('quota_monitor.divin_feed'),
                             'A separate divin discovery and assessment adapter is required')
        self.api = importlib.import_module('quota_monitor.divin_feed')
        (ROOT / 'data').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='test-divin-feed-', dir=ROOT / 'data')
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(ROOT / 'data'))
        self.addCleanup(self.tmp.cleanup)
        self.config = Config({'accounts': [{'username': 'thsottiaux'}], 'browser': {}}, self.tmp.name)

    def call(self, events=None, feed=None, now=NOW, *, errors=None):
        answers = [Response([event()] if events is None else events),
                   Response(radar() if feed is None else feed)]
        if errors:
            for position, error in errors.items():
                answers[position] = error
        with patch('quota_monitor.divin_feed._open', side_effect=answers) as opener:
            result = self.api.collect_candidates(self.config, now=now)
        return result, opener

    def cache(self):
        return self.config.database.parent / 'divin-feed-cache.json'

    def test_default_enabled_uses_fixed_public_gets_and_only_allowed_metadata(self):
        result, opener = self.call()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['source'], SOURCE)
        self.assertEqual(result['fetched_at'], iso(NOW))
        self.assertEqual(result['generated_at'], iso(NOW - 30))
        self.assertEqual(len(opener.call_args_list), 2)
        self.assertEqual([call.args[0].full_url for call in opener.call_args_list],
                         [SOURCE + 'api/reset-events', SOURCE + 'api/reset-feed'])
        for call in opener.call_args_list:
            request = call.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertLessEqual(call.kwargs['timeout'], 8)
            self.assertNotIn('Authorization', dict(request.header_items()))
            self.assertNotIn('Cookie', dict(request.header_items()))
        candidate = result['candidates'][0]
        self.assertEqual((candidate['id'], candidate['username'], candidate['url']),
                         ('100', 'thsottiaux', 'https://x.com/thsottiaux/status/100'))
        self.assertEqual((candidate['parent_id'], candidate['parent_username']), ('90', 'openai'))
        self.assertEqual(candidate['third_party'], result['assessments']['100'])
        metadata = candidate['third_party']
        self.assertEqual(metadata['event_type'], 'global_reset')
        self.assertEqual(metadata['scope'], 'Weekly limits for Plus users')
        self.assertEqual(metadata['reason'], 'Compensation after an incident')
        self.assertEqual(metadata['stage'], 'completed')
        self.assertIn('Author announcement', metadata['analysis_basis'])
        self.assertIn('Website inference', metadata['analysis_basis'])
        self.assertEqual(metadata['source_urls'], ['https://x.com/thsottiaux/status/100',
                                                  'https://x.com/openai/status/90'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_disabled_or_no_valid_monitored_handle_never_requests(self):
        for accounts, browser in [([{'username': 'thsottiaux'}], {'divin_feed_enabled': False}),
                                  ([], {}), ([{'username': 'evil/path'}], {})]:
            with self.subTest(accounts=accounts, browser=browser):
                self.config.data.update(accounts=accounts, browser=browser)
                result, opener = self.call()
                self.assertEqual(result['status'], 'disabled')
                opener.assert_not_called()
                self.assertFalse(self.cache().exists())

    def test_adapter_does_not_read_notification_settings(self):
        original = self.config.section
        def section(name):
            self.assertNotEqual(name, 'notifications')
            return original(name)
        with patch.object(self.config, 'section', side_effect=section):
            result, _ = self.call()
        self.assertEqual(result['status'], 'ok')

    def test_foreign_tools_authors_and_bad_source_links_are_excluded(self):
        rows = [event()]
        for number, changes in enumerate([{'tool': 'claude'}, {'sourceUrl': 'https://x.com/other/status/101'},
                                        {'sourceUrl': 'https://evil.example/thsottiaux/status/102'},
                                        {'sourceUrl': 'https://x.com/thsottiaux/status/١٠٣'},
                                        {'sourceUrl': 'https://x.com/thsottiaux/status/104?redirect=evil'},
                                        {'sourceUrl': 'http://x.com/thsottiaux/status/105'},
                                        {'sourceUrl': 'https://user@x.com/thsottiaux/status/106'}], 101):
            row = event(number)
            row.update(changes)
            rows.append(row)
        result, _ = self.call(rows, radar(posts=[]))
        self.assertEqual(list(result['assessments']), ['100'])
        self.assertEqual([candidate['id'] for candidate in result['candidates']], ['100'])

    def test_only_configured_handles_are_accepted_and_case_normalized(self):
        self.config.data['accounts'] = [{'username': 'OpenAI'}]
        row = event()
        row['sourceUrl'] = 'https://x.com/OPENAI/status/100'
        result, _ = self.call([row], radar(posts=[post()]))
        self.assertEqual(result['candidates'][0]['username'], 'openai')
        self.assertEqual(result['candidates'][0]['url'], 'https://x.com/openai/status/100')

    def test_historical_assessments_are_retained_but_not_candidates(self):
        old = event(101)
        old['eventTimeUtc'] = iso(NOW - 90000)
        future = event(102)
        future['eventTimeUtc'] = iso(NOW + 301)
        missing = event(103)
        missing.pop('eventTimeUtc')
        missing['eventTimeOriginal'] = 'next Tuesday'
        result, _ = self.call([event(), old, future, missing], radar(posts=[]))
        self.assertEqual(set(result['assessments']), {'100', '101', '102', '103'})
        self.assertEqual([candidate['id'] for candidate in result['candidates']], ['100'])

    def test_feed_generated_date_must_be_fresh_without_invalidating_events(self):
        for generated in (iso(NOW - 86401), iso(NOW + 301), 'unknown', '2026-10-13T08:00:00'):
            with self.subTest(generated=generated):
                result, _ = self.call([event()], radar(generatedAt=generated, posts=[post(101)]))
                self.assertEqual(result['status'], 'ok')
                self.assertEqual([candidate['id'] for candidate in result['candidates']], ['100'])
                self.assertEqual(set(result['assessments']), {'100'})
                self.cache().unlink()

    def test_feed_only_candidates_require_matching_id_handle_url_and_dates(self):
        rows = [post()]
        for number, changes in enumerate([{'id': '١٠١'}, {'handle': 'other'}, {'url': 'https://x.com/thsottiaux/status/999'},
                                        {'at': iso(NOW - 90000)}, {'at': None}, {'at': iso(NOW + 301)}], 101):
            row = post(number)
            row.update(changes)
            rows.append(row)
        result, _ = self.call([], radar(posts=rows))
        self.assertEqual([candidate['id'] for candidate in result['candidates']], ['100'])
        self.assertEqual(result['candidates'][0]['third_party']['reason'], '')
        self.assertEqual(result['candidates'][0]['third_party']['analysis_basis'], 'Website inference')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_related_links_are_bounded_canonical_and_never_parent_assertions(self):
        row = event()
        row['relatedSources'] = ['https://x.com/OpenAI/status/90', {'url': 'https://x.com/OpenAI/status/89'},
                                 {'sourceUrl': 'https://x.com/Other/status/88'}, 'javascript:bad',
                                 'https://evil.example/', 'https://x.com/OpenAI/status/90',
                                 'https://x.com/OpenAI/status/87', 'https://x.com/OpenAI/status/86',
                                 'https://x.com/OpenAI/status/85', 'https://x.com/OpenAI/status/84']
        result, _ = self.call([row], radar(posts=[]))
        item = result['candidates'][0]
        self.assertNotIn('parent_id', item)
        self.assertEqual(len(item['third_party']['source_urls']), 6)
        self.assertTrue(all(url.startswith('https://x.com/') for url in item['third_party']['source_urls']))

    def test_context_parent_requires_older_valid_link_and_conflicts_are_removed(self):
        for link in ('https://x.com/OpenAI/status/101', 'https://evil.example/status/90',
                     'https://x.com/evil/path/status/90'):
            row = post()
            row['context']['url'] = link
            result, _ = self.call([], radar(posts=[row]))
            self.assertNotIn('parent_id', result['candidates'][0])
            self.cache().unlink()
        one, two = post(), post()
        two['context']['url'] = 'https://x.com/OpenAI/status/89'
        result, _ = self.call([], radar(posts=[one, two, one]))
        self.assertNotIn('parent_id', result['candidates'][0])

    def test_candidates_and_assessments_are_bounded_and_duplicates_merge(self):
        self.config.data['browser']['max_posts'] = 2000
        rows = [event(number) for number in range(1, 250)]
        result, _ = self.call(rows, radar(posts=[post(249)]))
        self.assertEqual(len(result['assessments']), 200)
        self.assertEqual(len(result['candidates']), 20)
        self.assertEqual(len({candidate['id'] for candidate in result['candidates']}), 20)
        self.assertEqual(result['candidates'][0]['id'], '249')

    def test_full_historical_annotation_budget_cannot_hide_a_new_feed_candidate(self):
        rows = [event(number, eventTimeUtc=iso(NOW - 90000)) for number in range(1, 201)]
        result, _ = self.call(rows, radar(posts=[post(1000)]))
        self.assertEqual([candidate['id'] for candidate in result['candidates']], ['1000'])
        self.assertEqual(len(result['assessments']), 200)
        self.assertIn('1000', result['assessments'])
        self.assertEqual(result['candidates'][0]['third_party'], result['assessments']['1000'])

    def test_normalizer_strictly_allowlists_and_flattens_untrusted_prose(self):
        value = {'provider': 'divin.cc', 'source_url': SOURCE, 'fetched_at': iso(NOW),
                 'event_type': 'all\r\n分类：已重置', 'scope': '<script>range</script>',
                 'reason': 'x' * 5000, 'stage': True, 'analysis_basis': ['not text'],
                 'source_urls': ['https://x.com/OPENAI/status/90', 'javascript:bad'],
                 'original': 'SECRET', 'recipient': 'PRIVATE'}
        result = self.api.normalize_assessment(value)
        self.assertEqual(set(result), {'provider', 'source_url', 'fetched_at', 'event_type',
                                       'scope', 'reason', 'stage', 'analysis_basis', 'source_urls'})
        self.assertEqual(result['provider'], 'divin.cc')
        self.assertEqual(result['source_url'], SOURCE)
        self.assertNotIn('\n', result['event_type'])
        self.assertNotIn('\r', result['event_type'])
        self.assertLessEqual(len(result['reason']), 1000)
        self.assertEqual((result['stage'], result['analysis_basis']), ('', ''))
        self.assertEqual(result['source_urls'], ['https://x.com/openai/status/90'])
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('PRIVATE', str(result))
        self.assertIsNone(self.api.normalize_assessment({'provider': 'evil', 'source_url': SOURCE}))
        self.assertIsNone(self.api.normalize_assessment({'provider': 'divin.cc', 'source_url': 'https://evil.example/'}))

    def test_normalizer_canonicalizes_fetch_time_instead_of_echoing_oversize_timestamp(self):
        metadata = {'provider': 'divin.cc', 'source_url': SOURCE,
                    'fetched_at': '2026-10-13T00:00:00.' + '0' * 5000 + 'Z'}
        self.assertEqual(self.api.normalize_assessment(metadata)['fetched_at'], '2026-10-13T00:00:00Z')

    def test_normalizer_rejects_a_timestamp_outside_the_utc_datetime_range(self):
        metadata = {'provider': 'divin.cc', 'source_url': SOURCE,
                    'fetched_at': '0001-01-01T00:00:00+14:00'}
        self.assertEqual(self.api.normalize_assessment(metadata)['fetched_at'], '')

    def test_success_cache_prevents_both_repeated_source_calls_across_restarts(self):
        self.call()
        result, opener = self.call(now=NOW + 30)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['fetched_at'], iso(NOW))
        opener.assert_not_called()
        _, opener = self.call(now=NOW + 61)
        self.assertEqual(opener.call_count, 2)

    def test_failed_second_endpoint_preserves_valid_event_assessments(self):
        result, opener = self.call(errors={1: URLError('SECRET')})
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([candidate['id'] for candidate in result['candidates']], ['100'])
        self.assertNotIn('SECRET', str(result))
        result, opener = self.call(now=NOW + 30)
        self.assertEqual(result['status'], 'ok')
        opener.assert_not_called()

    def test_cached_event_survives_refresh_failure_without_falsifying_its_fetch_time(self):
        self.call()
        result, _ = self.call(events=[], now=NOW + 61, errors={0: URLError('SECRET')})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['assessments']['100']['fetched_at'], iso(NOW))
        self.assertEqual(result['assessments']['100']['reason'], 'Compensation after an incident')

    def test_failed_first_endpoint_can_still_use_fresh_feed_candidates(self):
        result, _ = self.call(errors={0: URLError('SECRET')})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([candidate['id'] for candidate in result['candidates']], ['100'])
        self.assertEqual(result['assessments']['100']['reason'], '')
        self.assertEqual(result['assessments']['100']['analysis_basis'], 'Website inference')

    def test_feed_judgment_enriches_historical_event_without_becoming_a_cause(self):
        row = event(eventTimeUtc=iso(NOW - 90000), classificationReason='Evidence does not establish a global reset')
        feed = radar(posts=[post(at=iso(NOW - 90000), reason='Only an editorial judgment')])
        result, _ = self.call([row], feed)
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['assessments']['100']['reason'], 'Compensation after an incident')
        self.assertIn('Evidence does not establish a global reset', result['assessments']['100']['analysis_basis'])
        self.assertIn('Only an editorial judgment', result['assessments']['100']['analysis_basis'])

    def test_scope_and_merge_descriptors_and_type_disagreement_remain_attributed(self):
        row = event(scopeSource='owner_correction', mergeSource='owner_confirmed')
        result, _ = self.call([row])
        metadata = result['assessments']['100']
        self.assertEqual(metadata['event_type'], 'global_reset')
        self.assertEqual(metadata['reason'], 'Compensation after an incident')
        for descriptor in ('owner_correction', 'owner_confirmed', 'possible_reset'):
            self.assertIn(descriptor, metadata['analysis_basis'])

    def test_feed_context_link_enriches_event_evidence_without_copying_context_text(self):
        row = event(relatedSources=[])
        result, _ = self.call([row])
        self.assertIn('https://x.com/openai/status/90', result['assessments']['100']['source_urls'])
        self.assertNotIn('SECRET CONTEXT', str(result))

    def test_historical_feed_only_context_is_an_assessment_without_candidate_or_crash(self):
        result, _ = self.call([], radar(posts=[post(at=iso(NOW - 90000))]))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['candidates'], [])
        self.assertIn('100', result['assessments'])
        self.assertEqual(result['assessments']['100']['reason'], '')

    def test_both_failures_and_oversize_are_safe_and_throttled(self):
        for error in (URLError('SECRET'), IncompleteRead(b'SECRET')):
            result, _ = self.call(errors={0: error, 1: URLError('SECRET')})
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('SECRET', str(result))
            result, opener = self.call(now=NOW + 30)
            self.assertEqual(result['status'], 'backoff')
            opener.assert_not_called()
            self.cache().unlink()
        result, _ = self.call(events=b'x' * (2 * 1024 * 1024 + 1), feed=b'bad')
        self.assertEqual(result['status'], 'error')

    def test_429_retry_after_persists_and_keeps_partial_valid_results(self):
        error = HTTPError(SOURCE + 'api/reset-feed', 429, 'SECRET', {'Retry-After': '180'}, None)
        result, _ = self.call(errors={1: error})
        self.assertEqual(result['status'], 'ok')
        result, opener = self.call(now=NOW + 120)
        self.assertEqual(result['status'], 'ok')
        opener.assert_not_called()

    def test_first_endpoint_429_stops_remaining_requests_to_the_rate_limited_host(self):
        error = HTTPError(SOURCE + 'api/reset-events', 429, 'SECRET', {'Retry-After': '180'}, None)
        result, opener = self.call(errors={0: error})
        self.assertEqual(result['status'], 'backoff')
        self.assertEqual(opener.call_count, 1)
        self.cache().unlink()
        date = format_datetime(datetime.fromtimestamp(NOW + 240, timezone.utc), usegmt=True)
        error = HTTPError(SOURCE + 'api/reset-events', 429, 'SECRET', {'Retry-After': date}, None)
        result, _ = self.call(errors={0: error, 1: URLError('SECRET')})
        self.assertEqual(result['status'], 'backoff')
        result, opener = self.call(now=NOW + 200)
        self.assertEqual(result['status'], 'backoff')
        opener.assert_not_called()

    def test_bad_shapes_fail_closed(self):
        result, _ = self.call(events={'events': [event()]}, feed={'posts': 'invalid'})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['assessments'], {})

    def test_feed_source_descriptor_is_not_treated_as_a_url_or_forwarded(self):
        result, _ = self.call(events=[], feed=radar(source='SECRET backend descriptor'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['source'], SOURCE)
        self.assertNotIn('SECRET', str(result))

    def test_cache_write_failure_prevents_unthrottled_network(self):
        with patch('quota_monitor.divin_feed._write_cache', return_value=False):
            result, opener = self.call()
        self.assertEqual(result['status'], 'error')
        opener.assert_not_called()

    def test_redirects_are_refused_and_cache_is_size_bounded(self):
        handler = self.api._NoRedirect()
        for target in ('https://evil.example/', SOURCE + 'login'):
            self.assertIsNone(handler.redirect_request(None, None, 302, 'redirect', {}, target))
        self.cache().parent.mkdir(parents=True)
        self.cache().write_bytes(b'x' * (4 * 1024 * 1024 + 4097))
        result, opener = self.call()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(opener.call_count, 2)


if __name__ == '__main__':
    unittest.main()
