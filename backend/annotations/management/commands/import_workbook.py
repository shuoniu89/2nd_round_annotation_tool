import hashlib
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from annotations.models import Code, Dataset, Record
from annotations.workbooks import annotation_lock, export_workbook


class Command(BaseCommand):
    help = "Import the study workbook once. Repeated imports of the same file are safe."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(settings.SOURCE_WORKBOOK))

    def handle(self, *args, **options):
        source = Path(options["path"]).resolve()
        if not source.is_file():
            raise CommandError(f"Workbook not found: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with annotation_lock():
            existing = Dataset.objects.first()
            if existing:
                if existing.sha256 != digest:
                    raise CommandError("A different dataset is already imported. Use a separate DATA_DIR for another study.")
                if not (Path(settings.DATA_DIR) / "source.xlsx").exists():
                    shutil.copy2(source, Path(settings.DATA_DIR) / "source.xlsx")
                if not export_workbook():
                    raise CommandError("Database is intact, but the workbook snapshot could not be refreshed.")
                self.stdout.write("Dataset already imported; workbook snapshot refreshed.")
                return
            workbook = load_workbook(source, read_only=True, data_only=True)
            sheet = workbook.active
            rows = list(sheet.iter_rows(values_only=True))
            headers = rows[0] if rows else []
            if len(headers) < 48 or any(not isinstance(value, str) or not value.strip() for value in headers[4:48]):
                raise CommandError("Expected title/text in B/C and 44 nonempty code headers in E–AV.")
            if [str(v).strip().lower() for v in headers[:4]] != ["id", "title", "text", "subreddit"]:
                raise CommandError("Expected columns A–D: id, title, text, subreddit.")
            codes = []
            for column in range(5, 49):
                header = headers[column - 1].strip()
                prefix, separator, label = header.partition(":")
                group = prefix.split("_")[0]
                if not separator or group not in {"RQ1", "RQ2", "RQ3"}:
                    raise CommandError(f"Unrecognized code header at {get_column_letter(column)}: {header}")
                codes.append(Code(key=get_column_letter(column), column=column, header=header, label=label,
                                  group=group, kind="detailed" if "_GPT" in prefix else "broad"))
            records = []
            seen = set()
            for row_number, row in enumerate(rows[1:], start=2):
                if not any(value is not None for value in row):
                    continue
                external_id = str(row[0] or "").strip()
                if not external_id or external_id in seen:
                    raise CommandError(f"Missing or duplicate record id on row {row_number}.")
                seen.add(external_id)
                for column in range(5, 49):
                    if row[column - 1] not in (None, "", 0, 1, "0", "1"):
                        raise CommandError(f"Expected binary AI annotation at {get_column_letter(column)}{row_number}.")
                records.append(Record(external_id=external_id, source_row=row_number, title=str(row[1] or ""),
                                      text=str(row[2] or ""), subreddit=str(row[3] or ""),
                                      ai_codes=[get_column_letter(c) for c in range(5, 49) if row[c - 1] in (1, "1")]))
            if not records:
                raise CommandError("The workbook has no records.")
            frozen = Path(settings.DATA_DIR) / "source.xlsx"
            shutil.copy2(source, frozen)
            with transaction.atomic():
                Dataset.objects.create(filename=source.name, sheet_name=sheet.title, sha256=digest)
                Code.objects.bulk_create(codes)
                Record.objects.bulk_create(records)
            workbook.close()
            if not export_workbook():
                raise CommandError("Import saved to database, but XLSX export failed. Run sync_workbook to retry.")
            self.stdout.write(self.style.SUCCESS(f"Imported {len(records)} posts and {len(codes)} codes. XLSX: {settings.EXPORT_WORKBOOK}"))

