"""Verify the effective SMTP connection without issuing an OTP or exposing secrets."""
import smtplib
import ssl

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


class Command(BaseCommand):
    help = 'Check SMTP connection/authentication. Send a test email only with --send-to.'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument('--send-to', help='Explicitly send a diagnostic email to this address.')

    def handle(self, *args, **options):
        if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
            raise CommandError(
                'SMTP is not active. Console/test email does not deliver OTPs. '
                'Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD and DEFAULT_FROM_EMAIL, '
                'then recreate the application containers.'
            )
        if not all((settings.EMAIL_HOST, settings.EMAIL_HOST_USER,
                    settings.EMAIL_HOST_PASSWORD, settings.DEFAULT_FROM_EMAIL)):
            raise CommandError('SMTP host, credentials and sender must all be configured.')
        recipient = options.get('send_to')
        if recipient:
            try:
                validate_email(recipient)
            except ValidationError:
                raise CommandError('--send-to must be one valid email address.') from None
        try:
            with get_connection(fail_silently=False) as connection:
                if recipient:
                    sent = EmailMessage(
                        subject='Rosario Dairy email delivery check',
                        body='This is an SMTP delivery check. It is not a password reset and contains no OTP.',
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[recipient], connection=connection,
                    ).send(fail_silently=False)
                    if sent != 1:
                        raise CommandError('SMTP did not accept the test email.')
        except smtplib.SMTPAuthenticationError:
            raise CommandError(
                'SMTP authentication failed. Check the sender account and its App Password. '
                'For Gmail, a changed Google password revokes existing App Passwords.'
            ) from None
        except ssl.SSLError:
            raise CommandError('SMTP TLS verification failed. Check the host, port and TLS/SSL settings.') from None
        except (smtplib.SMTPException, OSError):
            raise CommandError(
                'SMTP connection or delivery failed. Check network access, provider status, '
                'TLS/SSL settings and permitted sender/recipient addresses.'
            ) from None
        if recipient:
            self.stdout.write(self.style.SUCCESS(
                'SMTP accepted the test email. Confirm receipt in the destination inbox/spam folder.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('SMTP connection and authentication passed. No email was sent.'))
