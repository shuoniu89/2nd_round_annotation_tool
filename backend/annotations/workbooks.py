"""Database-backed XLSX snapshots, serialized across threads and worker processes."""
import io
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from filelock import FileLock
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

from .models import ActionLog, Annotation, Code, Dataset

logger = logging.getLogger(__name__)


def annotation_lock():
    return FileLock(str(settings.ANNOTATION_LOCK), timeout=60)


def safe_append(sheet, values):
    sheet.append(values)
    # User-entered text must stay text when a researcher opens the XLSX.
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def build_workbook(user=None):
    # A frozen server copy keeps later changes in the input workbook from
    # silently changing the labels of an already imported study.
    source = Path(settings.DATA_DIR) / "source.xlsx"
    workbook = load_workbook(source)
    codes = list(Code.objects.all())
    annotations = Annotation.objects.select_related("user", "record").order_by("user__email", "record__source_row")
    events = ActionLog.objects.select_related("annotation__user", "annotation__record")
    if user is not None:
        annotations = annotations.filter(user=user)
        events = events.filter(annotation__user=user)
        # Personal downloads follow the same blind-coding rule as the API.
        revealed_rows = set(annotations.filter(independent_confirmed_at__isnull=False)
                            .values_list("record__source_row", flat=True))
        dataset = Dataset.objects.first()
        source_sheet = workbook[dataset.sheet_name]
        for row in source_sheet.iter_rows(min_row=2, min_col=5, max_col=48):
            if row[0].row not in revealed_rows:
                for cell in row:
                    cell.value = None
    for name in ("Rater annotations", "Action log", "Codebook"):
        if name in workbook.sheetnames:
            del workbook[name]
    sheet = workbook.create_sheet("Rater annotations")
    safe_append(sheet, [
        "rater_id", "rater_email", "record_id", "source_row", "stage", "revision",
        "no_applicable_codes", "independent_confirmed_at", "completed_at", "updated_at", "notes",
        *[f"HUMAN:{code.header}" for code in codes],
        *[f"AI_REVIEW:{code.header}" for code in codes],
    ])
    for annotation in annotations:
        def timestamp(value):
            return value.isoformat() if value else ""
        safe_append(sheet, [
            annotation.user_id, annotation.user.email, annotation.record.external_id,
            annotation.record.source_row, annotation.stage, annotation.revision,
            int(annotation.no_codes), timestamp(annotation.independent_confirmed_at),
            timestamp(annotation.completed_at), timestamp(annotation.updated_at), annotation.notes,
            *[int(code.key in annotation.selected_codes) for code in codes],
            *[annotation.ai_reviews.get(code.key, "") for code in codes],
        ])
    sheet.freeze_panes = "L2"
    sheet.auto_filter.ref = sheet.dimensions
    log = workbook.create_sheet("Action log")
    safe_append(log, ["event_id", "rater_email", "record_id", "action", "revision", "timestamp", "snapshot_json"])
    import json
    for event in events:
        safe_append(log, [event.pk, event.annotation.user.email, event.annotation.record.external_id,
                          event.action, event.revision, event.created_at.isoformat(), json.dumps(event.snapshot, ensure_ascii=False)])
    log.freeze_panes = "A2"
    book = workbook.create_sheet("Codebook")
    safe_append(book, ["source_column", "original_header", "research_question", "code_type"])
    for code in codes:
        safe_append(book, [code.key, code.header, code.group, code.kind])
    for table in (sheet, log, book):
        for cell in table[1]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="27395C")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        table.row_dimensions[1].height = 60
        for column in table.columns:
            table.column_dimensions[column[0].column_letter].width = 24
    return workbook


def export_workbook():
    """Call with annotation_lock held. A failed export never loses database work."""
    path = Path(settings.EXPORT_WORKBOOK)
    temporary = None
    try:
        workbook = build_workbook()
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as handle:
            temporary = handle.name
        workbook.save(temporary)
        workbook.close()
        # The reader gets either the previous complete XLSX or the new complete XLSX.
        os.replace(temporary, path)
        Dataset.objects.update(workbook_dirty=False, workbook_synced_at=timezone.now())
        return True
    except Exception:
        logger.exception("Workbook export failed; annotations remain in the database")
        Dataset.objects.update(workbook_dirty=True)
        return False
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def personal_workbook(user):
    workbook = build_workbook(user=user)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()

