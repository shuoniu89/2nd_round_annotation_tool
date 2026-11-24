"""Database-backed XLSX snapshots, serialized across threads and worker processes."""
import io
import json
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from filelock import FileLock
from openpyxl import Workbook
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


def review_outcome(annotation, code_key):
    """"AI corrections" cell value, comparing the rater's independent choice with the AI label.

    agreed   – AI marked 1 and the rater chose the code independently
    missed   – the rater chose the code but the AI marked 0
    rejected – AI marked 1 and the rater rejected it during verification
    accepted – AI marked 1, the rater had not chosen it, and accepted it during verification
    blank    – neither marked it, the AI code awaits review, or the AI labels are not yet revealed
    """
    if not annotation.independent_confirmed_at:
        return ""
    ai_yes = code_key in annotation.record.ai_codes
    human_yes = code_key in annotation.selected_codes
    if ai_yes and human_yes:
        return "agreed"
    if human_yes:
        return "missed"
    if ai_yes:
        return annotation.ai_reviews.get(code_key, "")
    return ""


def was_edited_after_ai(annotation):
    if annotation.blind_selected_codes is None:
        return ""
    return int(annotation.blind_selected_codes != annotation.selected_codes
               or bool(annotation.blind_no_codes) != annotation.no_codes)


def timestamp(value):
    return value.isoformat() if value else ""


def style_sheet(sheet, freeze):
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="27395C")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[1].height = 60
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 24
    sheet.freeze_panes = freeze
    if sheet.max_row > 1:
        sheet.auto_filter.ref = sheet.dimensions


def build_workbook(user=None):
    """Four tabs, one row per rater and post; code columns use the input workbook headers (E–AV order).

    1. Final annotations  – the rater's current code selection (1/0)
    2. AI corrections     – agreed / missed / rejected / accepted per code (see review_outcome)
    3. Before AI reveal   – the blind selection as first confirmed, before AI labels were shown (1/0)
    4. Interaction log    – every saved action with a JSON snapshot
    Tabs 2 and 3 only list posts whose codes have been confirmed, so no AI label leaks before the reveal.
    """
    codes = list(Code.objects.order_by("column"))
    headers = [code.header for code in codes]
    annotations = Annotation.objects.select_related("user", "record").order_by("user__email", "record__source_row")
    events = ActionLog.objects.select_related("annotation__user", "annotation__record")
    if user is not None:
        annotations = annotations.filter(user=user)
        events = events.filter(annotation__user=user)
    annotations = list(annotations)
    confirmed = [a for a in annotations if a.independent_confirmed_at]

    def post_columns(a):
        return [a.user.email, a.user.first_name, a.record.external_id, a.record.source_row, a.record.title]
    post_headers = ["rater_email", "rater_name", "post_id", "source_row", "title"]

    workbook = Workbook()
    final = workbook.active
    final.title = "Final annotations"
    safe_append(final, [*post_headers, "stage", "no_applicable_codes", "edited_after_ai",
                        "completed_at", "updated_at", "notes", *headers])
    for a in annotations:
        safe_append(final, [*post_columns(a), a.stage, int(a.no_codes), was_edited_after_ai(a),
                            timestamp(a.completed_at), timestamp(a.updated_at), a.notes,
                            *[int(code.key in a.selected_codes) for code in codes]])
    style_sheet(final, "L2")

    corrections = workbook.create_sheet("AI corrections")
    safe_append(corrections, [*post_headers, "stage", "independent_confirmed_at", "completed_at", *headers])
    for a in confirmed:
        safe_append(corrections, [*post_columns(a), a.stage, timestamp(a.independent_confirmed_at),
                                  timestamp(a.completed_at), *[review_outcome(a, code.key) for code in codes]])
    style_sheet(corrections, "I2")

    blind = workbook.create_sheet("Before AI reveal")
    safe_append(blind, [*post_headers, "no_applicable_codes", "independent_confirmed_at", *headers])
    for a in confirmed:
        chosen = a.blind_selected_codes if a.blind_selected_codes is not None else a.selected_codes
        no_codes = a.blind_no_codes if a.blind_no_codes is not None else a.no_codes
        safe_append(blind, [*post_columns(a), int(bool(no_codes)), timestamp(a.independent_confirmed_at),
                            *[int(code.key in chosen) for code in codes]])
    style_sheet(blind, "H2")

    log = workbook.create_sheet("Interaction log")
    safe_append(log, ["event_id", "rater_email", "post_id", "source_row", "action", "revision", "timestamp", "snapshot_json"])
    for event in events:
        safe_append(log, [event.pk, event.annotation.user.email, event.annotation.record.external_id,
                          event.annotation.record.source_row, event.action, event.revision,
                          event.created_at.isoformat(), json.dumps(event.snapshot, ensure_ascii=False)])
    style_sheet(log, "A2")
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

