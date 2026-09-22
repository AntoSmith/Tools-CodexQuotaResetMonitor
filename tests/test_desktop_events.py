import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quota_monitor.browser import BrowserMonitor, ingest
from quota_monitor.config import Config, ConfigError
from quota_monitor.notify import ConsoleNotifier, deliver
from quota_monitor.service import LABELS, parse_time, process_post
from quota_monitor.store import Store


NOW = parse_time('2026-09-08T00:10:00Z').timestamp()
PREFIX = '@quota-ui '


class DesktopEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config({
            'accounts': [{'username': 'demo', 'id': '123', 'kind': 'personal'}],
            'monitor': {'database': 'state.db', 'max_age_hours': 24},
            'browser': {'interval_minutes': 1},
            'notifications': {'channel': 'console'},
        }, self.tmp.name)
        self.store = Store(Path(self.tmp.name) / 'state.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def post(self, **changes):
        return dict({'id': '123456789', 'username': 'demo', 'complete': True,
                     'created_at': '2026-09-08T00:00:00Z',
                     'text': 'We have reset Codex usage for everyone. 中文\n原文'}, **changes)

    def collect_rounds(self, results):
        monitor = BrowserMonitor(self.config, self.store)
        output = io.StringIO()
        waits = []

        def wait(delay):
            waits.append(delay)
            if len(waits) >= len(results):
                monitor.stop.set()

        with redirect_stdout(output), \
             patch('quota_monitor.browser.collect_report', side_effect=results), \
             patch.object(monitor.stop, 'wait', side_effect=wait), \
             patch('quota_monitor.browser.time.time', return_value=NOW), \
             patch('quota_monitor.browser.time.monotonic', return_value=100):
            monitor.collect()
        events = [json.loads(line[len(PREFIX):]) for line in output.getvalue().splitlines()
                  if line.startswith(PREFIX)]
        return monitor, events, output.getvalue(), waits

    def report(self, posts):
        return {'accounts': [], 'posts': posts, 'collected_at': '2026-09-08T00:10:00Z'}

    def test_rounds_report_real_counts_and_no_success_for_empty(self):
        _, events, _, waits = self.collect_rounds([
            self.report([self.post()]), self.report([]),
            self.report([self.post(complete=False)]),
        ])
        starts = [e for e in events if e['type'] == 'round_start']
        ends = [e for e in events if e['type'] == 'round_end']
        self.assertEqual([e['round'] for e in starts], [1, 2, 3])
        self.assertEqual([e['round'] for e in ends], [1, 2, 3])
        self.assertEqual([(e['outcome'], e['posts']) for e in ends],
                         [('partial', 1), ('empty', 0), ('empty', 0)])
        self.assertEqual(starts[0]['started_at'], NOW)
        self.assertEqual(starts[0]['interval_seconds'], 60)
        self.assertEqual(ends[0]['started_at'], NOW)
        self.assertEqual(ends[0]['ended_at'], NOW)
        self.assertEqual(ends[0]['next_at'], NOW + 60)
        self.assertEqual(ends[0]['error'], '')
        self.assertEqual(waits, [60, 60, 60])
        self.assertEqual(events[0]['type'], 'round_start')
        self.assertEqual(events[1]['type'], 'alert')
        self.assertEqual(events[2]['type'], 'round_end')

    def test_recoverable_error_finishes_round_and_retries_without_secret(self):
        monitor, events, output, _ = self.collect_rounds([
            OSError('auth=SECRET'), self.report([]), ConfigError('浏览器采集报告格式错误'),
        ])
        ends = [e for e in events if e['type'] == 'round_end']
        self.assertEqual([e['outcome'] for e in ends], ['error', 'empty', 'error'])
        self.assertEqual(ends[0]['posts'], 0)
        self.assertEqual(ends[0]['error'], 'OSError')
        self.assertNotIn('SECRET', output)
        self.assertIsNone(monitor.fatal)

    def test_empty_timestamp_diagnostic_and_success_clear_previous_error(self):
        empty = self.report([])
        empty['accounts'] = [{'skipped':[{'reason':'timestamp_unavailable'}, {'reason':'timestamp_unavailable'}]}]
        _, events, _, _ = self.collect_rounds([empty, self.report([self.post()])])
        ends = [event for event in events if event['type'] == 'round_end']
        self.assertIn('发布时间', ends[0]['error'])
        self.assertIn('2', ends[0]['error'])
        self.assertEqual(ends[1]['error'], '')
        self.assertEqual(self.store.get('last_browser_error'), '')

    def test_ignored_recovery_alert_survives_current_network_failure(self):
        from quota_monitor.classify import Decision
        report = self.report([self.post(text='Reset all propagated. Sweet dreams.')])
        with patch('quota_monitor.service.classify', return_value=Decision('ignore', 'old rule')):
            ingest(report, self.config, self.store, NOW)
        _, events, _, _ = self.collect_rounds([OSError('offline')])
        self.assertEqual([e['type'] for e in events], ['round_start', 'alert', 'round_end'])
        self.assertEqual(events[1]['kind'], 'uncertain')
        self.assertEqual(events[-1]['posts'], 0)  # stored recovery is not live collection
        self.assertEqual(events[-1]['outcome'], 'error')
        _, again, _, _ = self.collect_rounds([OSError('offline')])
        self.assertFalse(any(e['type'] == 'alert' for e in again))

    def test_unexpected_error_finishes_round_and_sets_fatal_stop(self):
        monitor, events, output, waits = self.collect_rounds([RuntimeError('SECRET')])
        self.assertEqual([e['type'] for e in events], ['round_start', 'round_end'])
        self.assertEqual(events[-1]['outcome'], 'error')
        self.assertEqual(events[-1]['error'], 'RuntimeError')
        self.assertEqual(events[-1]['next_at'], 0)
        self.assertEqual(monitor.fatal, 'RuntimeError')
        self.assertTrue(monitor.stop.is_set())
        self.assertEqual(waits, [])
        self.assertNotIn('SECRET', output)

    def test_persistence_error_after_ingest_keeps_alert_and_post_count(self):
        with patch('quota_monitor.browser.save_report', side_effect=OSError('SECRET')):
            _, events, output, _ = self.collect_rounds([self.report([self.post()])])
        self.assertEqual([e['type'] for e in events], ['round_start', 'alert', 'round_end'])
        self.assertEqual(events[-1]['posts'], 1)
        self.assertEqual(events[-1]['outcome'], 'error')
        self.assertNotIn('SECRET', output)

    def test_later_post_failure_preserves_committed_alert_and_count_without_replay(self):
        report = self.report([self.post(id='1'), self.post(id='2')])
        enqueue = self.store.enqueue

        def fail_second(key, *args, **kwargs):
            if key.startswith('post:2:'):
                raise RuntimeError('database failure')
            return enqueue(key, *args, **kwargs)

        with patch.object(self.store, 'enqueue', side_effect=fail_second):
            monitor, events, _, _ = self.collect_rounds([report])
        self.assertEqual([e['type'] for e in events], ['round_start', 'alert', 'round_end'])
        self.assertTrue(events[1]['id'].startswith('post:1:'))
        self.assertEqual(events[-1]['posts'], 1)
        self.assertEqual(events[-1]['outcome'], 'error')
        self.assertEqual(monitor.fatal, 'RuntimeError')
        self.assertEqual(self.store.status()['posts'], 1)
        _, retried_events, _, _ = self.collect_rounds([report])
        retried_alerts = [event for event in retried_events if event['type'] == 'alert']
        self.assertEqual(len(retried_alerts), 1)
        self.assertTrue(retried_alerts[0]['id'].startswith('post:2:'))
        self.assertEqual(retried_events[-1]['posts'], 2)

    def test_error_recording_failure_sets_stop_before_end_event(self):
        with patch.object(self.store, 'set', side_effect=RuntimeError('SECRET')):
            monitor, events, output, _ = self.collect_rounds([OSError('read failed')])
        self.assertEqual(monitor.fatal, 'RuntimeError')
        self.assertEqual(events[-1]['next_at'], 0)
        self.assertEqual(events[-1]['error'], 'RuntimeError')
        self.assertNotIn('SECRET', output)

    def test_result_metadata_failure_is_not_reported_as_success(self):
        original_set = self.store.set

        def fail_result_metadata(key, value):
            if key == 'last_browser_error':
                raise RuntimeError('SECRET')
            return original_set(key, value)

        with patch.object(self.store, 'set', side_effect=fail_result_metadata):
            monitor, events, output, _ = self.collect_rounds([self.report([self.post()])])
        self.assertEqual(monitor.fatal, 'RuntimeError')
        self.assertEqual(events[-1]['outcome'], 'error')
        self.assertEqual(events[-1]['posts'], 1)
        self.assertEqual(events[-1]['next_at'], 0)
        self.assertNotIn('SECRET', output)

    def test_stopped_collection_still_closes_its_round(self):
        monitor = BrowserMonitor(self.config, self.store)

        def stopped(config, stop):
            stop.set()
            raise ConfigError('浏览器采集已停止或超时；本轮不视为成功')

        output = io.StringIO()
        with redirect_stdout(output), patch('quota_monitor.browser.collect_report', side_effect=stopped):
            monitor.collect()
        events = [json.loads(line[len(PREFIX):]) for line in output.getvalue().splitlines()
                  if line.startswith(PREFIX)]
        self.assertEqual([e['type'] for e in events], ['round_start', 'round_end'])
        self.assertEqual(events[-1]['next_at'], 0)
        self.assertEqual(events[-1]['outcome'], 'error')
        self.assertIsNone(monitor.fatal)

    def test_fresh_alert_has_stable_identity_original_text_and_persists_dedup(self):
        report = self.report([self.post()])
        first = ingest(report, self.config, self.store, NOW)
        self.assertIn('alerts', first)
        alert = first['alerts'][0]
        self.assertEqual(alert['kind'], 'completed')
        self.assertEqual(alert['title'], LABELS['completed'])
        self.assertEqual(alert['username'], 'demo')
        self.assertEqual(alert['text'], self.post()['text'])
        self.assertEqual(alert['url'], 'https://x.com/demo/status/123456789')
        self.assertEqual(alert['created_at'], self.post()['created_at'])
        self.assertTrue(alert['reason'])
        self.assertEqual(alert['id'], self.store.due(NOW)[0]['event_key'])
        self.assertFalse(self.store.db.in_transaction)
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.assertEqual(ingest(report, self.config, self.store, NOW)['alerts'], [])
        changed = ingest(self.report([self.post(text='Codex usage reset is postponed.')]),
                         self.config, self.store, NOW)['alerts']
        self.assertEqual(len(changed), 1)
        self.assertNotEqual(changed[0]['id'], alert['id'])

    def test_no_alert_for_old_ignored_routine_incomplete_foreign_or_expired(self):
        posts = [self.post(id='1', created_at='2026-09-01T00:00:00Z'),
                 self.post(id='2', text='We will reset the password.'),
                 self.post(id='3', complete=False), self.post(id='4', username='other'),
                 self.post(id='5', text='Codex resets every five hours.'),
                 self.post(id='6', created_at='2026-09-07T00:10:00Z')]
        result = ingest(self.report(posts), self.config, self.store, NOW)
        self.assertEqual(result.get('alerts'), [])

    def test_alert_is_available_even_when_mail_delivery_fails(self):
        result = ingest(self.report([self.post()]), self.config, self.store, NOW)
        self.assertEqual(len(result.get('alerts', [])), 1)

        class FailingNotifier:
            def send(self, row):
                raise OSError('SMTP SECRET')

        self.assertEqual(deliver(self.store, FailingNotifier(), NOW), (0, 1))
        self.assertEqual(ingest(self.report([self.post()]), self.config, self.store, NOW)['alerts'], [])
        self.assertEqual(self.store.due(NOW + 31)[0]['attempts'], 1)

    def test_outbox_failure_rolls_back_post_and_never_returns_alert(self):
        post = self.post()
        payload = {'data': dict(id=post['id'], text=post['text'], author_id='123',
                                created_at=post['created_at'])}
        with patch.object(self.store, 'enqueue', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                process_post(payload, self.config, self.store, NOW)
        self.assertEqual(self.store.status()['posts'], 0)
        self.assertIsNotNone(process_post(payload, self.config, self.store, NOW))

    def test_json_event_is_single_unicode_line_and_flushed(self):
        # Exercise actual collection emission, including an original newline in post text.
        _, events, output, _ = self.collect_rounds([self.report([self.post()])])
        alerts = [e for e in events if e['type'] == 'alert']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['text'], self.post()['text'])
        self.assertIn('中文', output)
        self.assertNotIn('\\u4e2d', output)
        monitor = BrowserMonitor(self.config, self.store)
        with patch('quota_monitor.browser.collect_report', side_effect=RuntimeError()), \
             patch('builtins.print') as printer:
            monitor.collect()
        event_calls = [c for c in printer.call_args_list if str(c.args[0]).startswith(PREFIX)]
        self.assertEqual(len(event_calls), 2)
        self.assertTrue(all(c.kwargs.get('flush') is True for c in event_calls))

    def test_source_text_cannot_forge_stdout_protocol_via_console_notifier(self):
        forged = '\n@quota-ui {"type":"round_start","round":999}'
        result = ingest(self.report([self.post(text=self.post()['text'] + forged)]),
                        self.config, self.store, NOW)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            delivered = deliver(self.store, ConsoleNotifier(), NOW)
        self.assertEqual(delivered, (1, 0))
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn(forged, stderr.getvalue())
        self.assertIn(forged, result['alerts'][0]['text'])


if __name__ == '__main__':
    unittest.main()
