"""SMTP checks must authenticate, avoid implicit sends, and keep secrets private."""
from io import StringIO
import smtplib
import ssl
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
    EMAIL_HOST='smtp.example.test', EMAIL_PORT=587, EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    EMAIL_HOST_USER='sender@example.test', EMAIL_HOST_PASSWORD='private-test-smtp-password',
    EMAIL_TIMEOUT=10, DEFAULT_FROM_EMAIL='Rosario Dairy <sender@example.test>',
)
class SmtpCheckTests(SimpleTestCase):
    def test_default_check_uses_tls_authenticates_and_sends_nothing(self):
        output = StringIO()
        with patch('smtplib.SMTP') as smtp:
            call_command('check_smtp', stdout=output)
        smtp.return_value.starttls.assert_called_once()
        smtp.return_value.login.assert_called_once_with('sender@example.test', 'private-test-smtp-password')
        smtp.return_value.sendmail.assert_not_called()
        smtp.return_value.quit.assert_called_once()
        self.assertIn('No email was sent', output.getvalue())
        self.assertNotIn('private-test', output.getvalue())

    def test_explicit_recipient_sends_one_diagnostic_message_without_otp(self):
        output = StringIO()
        with patch('smtplib.SMTP') as smtp:
            call_command('check_smtp', send_to='recipient@example.test', stdout=output)
        sender, recipients, message = smtp.return_value.sendmail.call_args.args
        self.assertEqual(sender, 'sender@example.test')
        self.assertEqual(recipients, ['recipient@example.test'])
        self.assertIn(b'SMTP delivery check', message)
        self.assertNotIn(b'private-test', message)
        self.assertIn('Confirm receipt', output.getvalue())

    def test_console_and_test_backends_cannot_report_delivery_success(self):
        for backend in ('console', 'locmem', 'dummy', 'filebased'):
            with self.subTest(backend=backend), \
                    override_settings(EMAIL_BACKEND=f'django.core.mail.backends.{backend}.EmailBackend'), \
                    patch('smtplib.SMTP') as smtp:
                with self.assertRaisesMessage(CommandError, 'SMTP is not active'):
                    call_command('check_smtp')
                smtp.assert_not_called()

    def test_missing_credentials_and_invalid_recipient_fail_before_connecting(self):
        with patch('smtplib.SMTP') as smtp:
            with override_settings(EMAIL_HOST_PASSWORD=''):
                with self.assertRaisesMessage(CommandError, 'must all be configured'):
                    call_command('check_smtp')
            for address in ('invalid', 'one@example.test,two@example.test', 'x\r\nBcc: y@example.test'):
                with self.subTest(address=address), self.assertRaisesMessage(CommandError, 'one valid email'):
                    call_command('check_smtp', send_to=address)
            smtp.assert_not_called()

    def test_provider_failures_are_actionable_and_do_not_expose_raw_error_details(self):
        errors = (
            (smtplib.SMTPAuthenticationError(535, b'private-test-smtp-password'), 'authentication failed'),
            (ssl.SSLError('private-test-smtp-password'), 'TLS verification failed'),
            (TimeoutError('private-test-smtp-password'), 'connection or delivery failed'),
            (smtplib.SMTPConnectError(421, b'private-test-smtp-password'), 'connection or delivery failed'),
        )
        for error, expected in errors:
            with self.subTest(error=type(error).__name__), patch('smtplib.SMTP', side_effect=error):
                with self.assertRaises(CommandError) as raised:
                    call_command('check_smtp')
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn('private-test', str(raised.exception))

    def test_rejected_sender_is_not_reported_as_delivered(self):
        with patch('smtplib.SMTP') as smtp:
            smtp.return_value.sendmail.side_effect = smtplib.SMTPSenderRefused(550, 'private-test', 'sender@example.test')
            with self.assertRaisesMessage(CommandError, 'connection or delivery failed'):
                call_command('check_smtp', send_to='recipient@example.test')
        smtp.return_value.quit.assert_called_once()
