import json
import os
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class APIError(RuntimeError):
    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


class Transport:
    def open(self, method, path, params=None, body=None, timeout=25):
        url = 'https://api.x.com' + path
        if params:
            url += '?' + urlencode(params)
        headers = {'Authorization': 'Bearer ' + os.environ['X_BEARER_TOKEN'], 'Content-Type': 'application/json'}
        req = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            return urlopen(req, timeout=timeout, context=ssl.create_default_context())
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise APIError(f'X API HTTP {code}', permanent=code in (400, 401, 402, 403, 404, 422)) from None
        except (OSError, URLError):
            raise APIError('X API 网络连接失败') from None

    def request(self, method, path, params=None, body=None):
        with self.open(method, path, params, body) as response:
            try:
                return json.load(response)
            except (ValueError, OSError):
                raise APIError('X API 返回无效 JSON') from None

    def stream(self, params, stop):
        with self.open('GET', '/2/tweets/search/stream', params) as response:
            yield {'_connected': True}
            while not stop.is_set():
                line = response.readline(2_000_001)
                if not line:
                    raise APIError('X 流连接已关闭')
                if len(line) > 2_000_000:
                    raise APIError('X 流消息超过大小限制')
                if not line.strip():
                    yield None
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    raise APIError('X 流返回无效 JSON') from None


class XClient:
    TAG = 'codex-quota-monitor-v1'
    FIELDS = {'tweet.fields': 'id,text,author_id,created_at,conversation_id,referenced_tweets,edit_history_tweet_ids,note_tweet',
              'expansions': 'referenced_tweets.id', 'user.fields': 'id,username'}

    def __init__(self, config, store, transport=None):
        self.config, self.store = config, store
        self.transport = transport or Transport()
        self.rule = '(' + ' OR '.join('from:' + a['username'] for a in config.accounts) + ') -is:retweet'

    def checked(self, result):
        if not isinstance(result, dict) or result.get('errors'):
            raise APIError('X API 返回错误或不完整结果')
        return result

    def reserve(self, count):
        self.store.reserve(count, self.config.resource_limit, datetime.now(timezone.utc).strftime('%Y-%m'))

    def ensure_rules(self):
        rules = self.checked(self.transport.request('GET', '/2/tweets/search/stream/rules')).get('data', [])
        if any(r.get('tag') != self.TAG for r in rules):
            raise APIError('此 X App 含其他流规则；请使用独立 App，避免额外流量', permanent=True)
        if len(rules) == 1 and rules[0].get('value') == self.rule:
            return
        if rules:
            self.checked(self.transport.request('POST', '/2/tweets/search/stream/rules', body={'delete': {'ids': [r['id'] for r in rules]}}))
        self.checked(self.transport.request('POST', '/2/tweets/search/stream/rules', body={'add': [{'value': self.rule, 'tag': self.TAG}]}))

    def backfill(self, start, end):
        # Reserve a whole page + possible referenced posts before each paid request.
        # Conservatively charge unused slots; do not refund failed/ambiguous requests.
        params = dict(self.FIELDS, query=self.rule, start_time=start, end_time=end, max_results=10)
        tokens = set()
        while True:
            self.reserve(40)
            result = self.checked(self.transport.request('GET', '/2/tweets/search/recent', params=params.copy()))
            for post in result.get('data', []):
                yield {'data': post, 'includes': result.get('includes', {})}
            token = result.get('meta', {}).get('next_token')
            if not token:
                return
            if token in tokens:
                raise APIError('X 补拉分页标记重复')
            tokens.add(token)
            params['next_token'] = token

    def stream(self, stop):
        # Reserve before connecting as well, so exhausted budgets never reconnect.
        self.reserve(1)
        for payload in self.transport.stream(self.FIELDS, stop):
            if payload is not None and payload.get('_connected'):
                yield payload
                continue
            if payload is not None:
                self.checked(payload)
                if not payload.get('data'):
                    raise APIError('X 流消息缺少 data')
                count = 1 + sum(len(v) for v in payload.get('includes', {}).values() if isinstance(v, list))
                self.reserve(count)
            yield payload
