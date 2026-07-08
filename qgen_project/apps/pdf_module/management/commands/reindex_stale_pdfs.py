from django.core.management.base import BaseCommand

from apps.pdf_module.tasks import reindex_stale_contexts


class Command(BaseCommand):
    help = "Queue reindex jobs for stale PDF contexts."

    def handle(self, *args, **options):
        reindex_stale_contexts.delay()
        self.stdout.write(self.style.SUCCESS("Queued stale PDF contexts for reindexing."))
