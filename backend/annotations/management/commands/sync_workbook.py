from django.core.management.base import BaseCommand, CommandError
from annotations.workbooks import annotation_lock, export_workbook


class Command(BaseCommand):
    help = "Rebuild the server XLSX snapshot from all saved account annotations."

    def handle(self, *args, **options):
        with annotation_lock():
            if not export_workbook():
                raise CommandError("Workbook sync failed. See server log; database annotations are intact.")
        self.stdout.write(self.style.SUCCESS("Server XLSX is current."))

