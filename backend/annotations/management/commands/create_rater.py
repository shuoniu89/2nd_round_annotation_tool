import csv
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from openpyxl import load_workbook

from annotations.models import Assignment, Record


class Command(BaseCommand):
    help = ("Create (or update) a rater account and assign posts to it by position, e.g. --posts 1-30. "
            "Optionally write the assigned rows of the study workbook to a CSV file.")

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--name", default="", help="Display name stored as the first name.")
        parser.add_argument("--posts", help="Post positions in workbook order, e.g. 1-30 or 31-60. Omit to show all posts.")
        parser.add_argument("--csv", help="Write the assigned workbook rows to this CSV path.")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError(f"Not a valid email address: {email}")
        records = list(Record.objects.order_by("source_row"))
        if not records:
            raise CommandError("No dataset imported. Run import_workbook first.")
        chosen = records
        if options["posts"]:
            try:
                first, last = (int(part) for part in options["posts"].split("-"))
            except ValueError:
                raise CommandError("--posts must look like 1-30.")
            if not 1 <= first <= last <= len(records):
                raise CommandError(f"--posts must be within 1-{len(records)}.")
            chosen = records[first - 1:last]

        User = get_user_model()
        with transaction.atomic():
            user = User.objects.filter(email__iexact=email).first()
            created = user is None
            if created:
                user = User(username=uuid.uuid4().hex, email=email)
            user.first_name = options["name"] or user.first_name
            user.set_password(options["password"])
            user.save()
            Assignment.objects.filter(user=user).delete()
            if options["posts"]:
                Assignment.objects.bulk_create(Assignment(user=user, record=r) for r in chosen)

        if options["csv"]:
            self.write_csv(Path(options["csv"]), {r.source_row for r in chosen})
        scope = f"posts {options['posts']} (rows {chosen[0].source_row}-{chosen[-1].source_row})" if options["posts"] else "all posts"
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} {email}: {scope}."))

    def write_csv(self, path, rows):
        source = Path(settings.DATA_DIR) / "source.xlsx"
        workbook = load_workbook(source, read_only=True, data_only=True)
        sheet = workbook.active
        path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig so Excel shows non-ASCII post text correctly.
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if index == 1 or index in rows:
                    writer.writerow(["" if value is None else value for value in row])
        workbook.close()
        self.stdout.write(f"Wrote {len(rows)} rows to {path}")
