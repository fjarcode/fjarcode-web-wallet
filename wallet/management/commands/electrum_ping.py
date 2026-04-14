from django.core.management.base import BaseCommand

from wallet.services.electrum import ElectrumClient, ElectrumConnectionError


class Command(BaseCommand):
    help = 'Check Electrum server connectivity and print server.version.'

    def handle(self, *args, **options):
        client = ElectrumClient()
        try:
            version = client.server_version()
            self.stdout.write(self.style.SUCCESS(f'Electrum OK: {version}'))
        except ElectrumConnectionError as exc:
            self.stderr.write(self.style.ERROR(f'Electrum failed: {exc}'))
            raise SystemExit(1)
