import json
import uuid
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import ActionLog, Annotation, Code, Dataset, Record
from .workbooks import annotation_lock, export_workbook, personal_workbook


def error(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def json_body(request):
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Send a valid JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Send a valid JSON object.")
    return payload


def account_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error("Please sign in to continue.", 401)
        return view(request, *args, **kwargs)
    return wrapped


def user_json(user):
    return {"id": user.pk, "email": user.email, "is_staff": user.is_staff}


def annotation_json(annotation):
    if annotation is None:
        return {"selected_codes": [], "no_codes": False, "ai_reviews": {}, "notes": "", "stage": "annotate",
                "revision": 0, "updated_at": None, "independent_confirmed_at": None, "completed_at": None}
    return {name: (value.isoformat() if hasattr(value, "isoformat") else value)
            for name in ("selected_codes", "no_codes", "ai_reviews", "notes", "stage", "revision",
                         "updated_at", "independent_confirmed_at", "completed_at")
            for value in [getattr(annotation, name)]}


@require_GET
@ensure_csrf_cookie
def session(request):
    return JsonResponse({"user": user_json(request.user) if request.user.is_authenticated else None,
                         "registration_open": settings.ALLOW_REGISTRATION})


@require_POST
def sign_in(request):
    try:
        data = json_body(request)
    except ValueError as exception:
        return error(str(exception))
    if not isinstance(data.get("email"), str) or not isinstance(data.get("password"), str):
        return error("Enter your email address and password.")
    email = data["email"].strip().lower()
    if not email or not data["password"]:
        return error("Enter your email address and password.")
    User = get_user_model()
    try:
        account = User.objects.get(email__iexact=email)
    except (User.DoesNotExist, User.MultipleObjectsReturned):
        User().set_password(data["password"])
        return error("The email address or password is incorrect.", 400)
    # Keep the existing Django account IDs and sessions, including saved annotations.
    user = authenticate(request, username=account.username, password=data["password"])
    if user is None:
        return error("The email address or password is incorrect.", 400)
    login(request, user)
    return JsonResponse({"user": user_json(user)})


@require_POST
def register(request):
    if not settings.ALLOW_REGISTRATION:
        return error("Registration is closed. Contact the study administrator for an account.", 403)
    try:
        data = json_body(request)
        if not all(isinstance(data.get(key), str) for key in ("email", "password")):
            raise ValueError("Enter an email address and password.")
        email = data["email"].strip().lower()
        if not email or not data["password"]:
            raise ValueError("Enter an email address and password.")
        User = get_user_model()
        validate_email(email)
        # The built-in Django model requires an internal username; raters only use email.
        user = User(username=uuid.uuid4().hex, email=email)
        with annotation_lock():
            if User.objects.filter(email__iexact=email).exists():
                return error("An account with that email address already exists.")
            user.set_password(data["password"])
            user.save()
        login(request, user)
        return JsonResponse({"user": user_json(user)}, status=201)
    except ValidationError as exception:
        return error(" ".join(exception.messages))
    except (ValueError, IntegrityError) as exception:
        return error(str(exception) if isinstance(exception, ValueError) else "Unable to create the account. Please try again.")


@require_POST
@account_required
def sign_out(request):
    logout(request)
    return JsonResponse({"ok": True})


@require_GET
@account_required
def project(request):
    dataset = Dataset.objects.first()
    if not dataset:
        return error("No dataset imported. Run python manage.py import_workbook on the server.", 503)
    annotations = {a.record_id: a for a in Annotation.objects.filter(user=request.user)}
    records = []
    for record in Record.objects.all():
        annotation = annotations.get(record.pk)
        records.append({"id": record.pk, "external_id": record.external_id, "source_row": record.source_row,
                        "title": record.title, "subreddit": record.subreddit,
                        "stage": annotation.stage if annotation else "not_started"})
    return JsonResponse({
        "dataset": {"filename": dataset.filename, "record_count": len(records), "code_count": Code.objects.count(),
                    "workbook_synced": not dataset.workbook_dirty,
                    "workbook_synced_at": dataset.workbook_synced_at.isoformat() if dataset.workbook_synced_at else None},
        "codes": list(Code.objects.values("key", "header", "label", "group", "kind", "column")),
        "records": records,
    })


@require_GET
@account_required
def record_detail(request, record_id):
    record = Record.objects.filter(pk=record_id).first()
    if not record:
        return error("Post not found.", 404)
    annotation = Annotation.objects.filter(record=record, user=request.user).first()
    # AI annotations are withheld at the API boundary, not just hidden in the UI.
    revealed = annotation is not None and annotation.independent_confirmed_at is not None
    return JsonResponse({"record": {"id": record.pk, "external_id": record.external_id,
                                   "source_row": record.source_row, "title": record.title,
                                   "text": record.text, "subreddit": record.subreddit,
                                   "ai_codes": record.ai_codes if revealed else None},
                         "annotation": annotation_json(annotation)})


@require_POST
@account_required
def save_annotation(request, record_id):
    try:
        data = json_body(request)
    except ValueError as exception:
        return error(str(exception))
    action = data.get("action")
    if action not in {"select_codes", "confirm_independent", "review_ai", "save_notes", "complete"}:
        return error("Unknown annotation action.")
    if type(data.get("revision")) is not int or data["revision"] < 0:
        return error("A valid annotation revision is required.")
    request_id = None
    if data.get("request_id") is not None:
        try:
            request_id = uuid.UUID(str(data["request_id"]))
        except (ValueError, AttributeError):
            return error("A valid request ID is required.")
    record = Record.objects.filter(pk=record_id).first()
    if record is None:
        return error("Post not found.", 404)
    with annotation_lock():
        # A lost HTTP response can be retried without recording the action twice.
        if request_id is not None:
            previous = ActionLog.objects.filter(request_id=request_id).select_related("annotation").first()
            if previous:
                saved = previous.annotation
                if saved.user_id != request.user.pk or saved.record_id != record.pk or previous.action != action:
                    return error("This request ID has already been used for another action.", 409)
                synced = export_workbook() if Dataset.objects.filter(workbook_dirty=True).exists() else True
                return JsonResponse({"annotation": annotation_json(saved), "workbook_synced": synced,
                                     "ai_codes": record.ai_codes if saved.independent_confirmed_at else None})
        with transaction.atomic():
            annotation = Annotation.objects.filter(user=request.user, record=record).first()
            current_revision = annotation.revision if annotation else 0
            if data["revision"] != current_revision:
                return error("This annotation changed in another tab. Reload the post before continuing.", 409,
                             annotation=annotation_json(annotation))
            if annotation is None:
                annotation = Annotation(user=request.user, record=record)
            if action == "select_codes":
                if annotation.independent_confirmed_at:
                    return error("Independent codes are locked after the AI annotations are revealed.")
                selected = data.get("selected_codes")
                no_codes = data.get("no_codes", False)
                if not isinstance(selected, list) or any(not isinstance(code, str) for code in selected):
                    return error("Select valid codes.")
                valid_codes = list(Code.objects.values_list("key", flat=True))
                if set(selected) - set(valid_codes) or type(no_codes) is not bool:
                    return error("Select valid codes.")
                if selected and no_codes:
                    return error("Choose codes or mark no applicable codes.")
                annotation.selected_codes = [code for code in valid_codes if code in selected]
                annotation.no_codes = no_codes
            elif action == "confirm_independent":
                if annotation.independent_confirmed_at:
                    return error("Your independent annotation is already confirmed.")
                if not annotation.selected_codes and not annotation.no_codes:
                    return error("Select at least one code, or explicitly mark no applicable codes.")
                annotation.independent_confirmed_at = timezone.now()
                annotation.stage = "verify"
            elif action == "review_ai":
                if not annotation.independent_confirmed_at:
                    return error("Confirm your independent annotation before reviewing AI codes.")
                reviews = data.get("ai_reviews")
                if not isinstance(reviews, dict) or set(reviews) - set(record.ai_codes):
                    return error("Review only the AI-positive codes for this post.")
                if any(value not in ("accepted", "rejected") for value in reviews.values()):
                    return error("Each AI code must be accepted or rejected.")
                annotation.ai_reviews = reviews
                annotation.stage = "verify"
                annotation.completed_at = None
            elif action == "save_notes":
                if not isinstance(data.get("notes"), str) or len(data["notes"]) > 10000:
                    return error("Notes must be text with at most 10,000 characters.")
                annotation.notes = data["notes"]
            elif action == "complete":
                if not annotation.independent_confirmed_at:
                    return error("Finish your independent annotation first.")
                if set(annotation.ai_reviews) != set(record.ai_codes):
                    return error("Accept or reject every AI-positive code before completing this post.")
                annotation.stage = "complete"
                annotation.completed_at = timezone.now()
            annotation.revision += 1
            annotation.save()
            snapshot = annotation_json(annotation)
            ActionLog.objects.create(annotation=annotation, action=action, revision=annotation.revision,
                                     snapshot=snapshot, request_id=request_id)
            Dataset.objects.update(workbook_dirty=True)
        synced = export_workbook()
        return JsonResponse({"annotation": snapshot, "workbook_synced": synced,
                             "ai_codes": record.ai_codes if annotation.independent_confirmed_at else None})


@require_POST
@account_required
def sync_workbook(request):
    if not Dataset.objects.exists():
        return error("No dataset imported.", 503)
    with annotation_lock():
        synced = export_workbook()
    return JsonResponse({"workbook_synced": synced}, status=200 if synced else 503)


@require_GET
@account_required
def download_workbook(request):
    if not Dataset.objects.exists():
        return error("No dataset imported.", 503)
    with annotation_lock():
        content = personal_workbook(request.user)
    response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="my_annotations.xlsx"'
    return response


@require_GET
def health(request):
    return JsonResponse({"ok": True})
