import smtplib
import ssl
import sys
import time

from .config import parse_recipients
from .email_message import build_message


class ConsoleNotifier:
    def __init__(self, stream=None):
        self.stream = stream

    def send(self, row):
        # Authored text may contain protocol-looking lines. Keep human-readable
        # notifications off the desktop event channel (stdout).
        print('\n' + row['title'] + '\n' + row['body'],
              file=self.stream if self.stream is not None else sys.stderr, flush=True)


class SMTPNotifier:
    def __init__(self, config, factory=smtplib.SMTP_SSL):
        self.config = config
        self.settings = config.section('notifications')
        self.factory = factory

    def send(self, row):
        for recipient in self.config.recipients:
            self.send_to(row, recipient)

    def send_to(self, row, recipient):
        n = self.settings
        self.config.validate_smtp_auth()
        auth_code = self.config.smtp_auth_code
        message = build_message(self.config, row, recipient)
        accepted = False
        try:
            with self.factory(n.get('host', 'smtp.qq.com'), n.get('port', 465),
                              context=ssl.create_default_context(), timeout=20) as smtp:
                smtp.login(n['username'], auth_code)
                refused = smtp.send_message(message)
                if refused:
                    raise OSError('SMTP recipient refused')
                accepted = True
        except Exception:
            if not accepted:
                raise
            # A failed QUIT after a successful DATA response is not a send failure.


def deliver(store, notifier, now=None):
    store.configure_delivery(notifier.config.destination if isinstance(notifier, SMTPNotifier) else 'console:')
    if isinstance(notifier, SMTPNotifier) and notifier.config.recipient_contacts is not None:
        from .delivery import deliver_subscriptions
        return deliver_subscriptions(store, notifier, now)
    fixed_now = now
    now = time.time() if now is None else now
    ok = failed = 0
    for row in store.due(now):
        check_at = time.time() if fixed_now is None else fixed_now
        if not store.is_pending(row['id'], check_at):
            continue
        try:
            if isinstance(notifier, SMTPNotifier):
                error = None
                recipients = parse_recipients(row['destination'].partition(':')[2])
                for recipient in recipients:
                    check_at = time.time() if fixed_now is None else fixed_now
                    if not store.is_pending(row['id'], check_at):
                        break
                    if store.recipient_sent(row['id'], recipient):
                        continue
                    try:
                        notifier.send_to(row, recipient)
                        store.mark_recipient_sent(row['id'], recipient)
                    except Exception as exc:
                        error = exc
                if error is not None:
                    raise error
                if not all(store.recipient_sent(row['id'], r) for r in recipients):
                    continue
            else:
                notifier.send(row)
        except Exception as exc:
            # Exception messages may include credentials, server data, or recipient addresses.
            store.failed(row['id'], now)
            store.set('last_delivery_error', type(exc).__name__)
            failed += 1
        else:
            store.sent(row['id'])
            store.set('last_delivery_at', now)
            ok += 1
    return ok, failed
