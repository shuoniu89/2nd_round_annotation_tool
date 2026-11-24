from django.conf import settings
from django.db import models


class Dataset(models.Model):
    filename = models.CharField(max_length=255)
    sheet_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    imported_at = models.DateTimeField(auto_now_add=True)
    workbook_dirty = models.BooleanField(default=True)
    workbook_synced_at = models.DateTimeField(null=True, blank=True)


class Code(models.Model):
    key = models.CharField(max_length=3, unique=True)
    header = models.TextField()
    label = models.TextField()
    group = models.CharField(max_length=3)
    kind = models.CharField(max_length=10)
    column = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        ordering = ["column"]


class Record(models.Model):
    external_id = models.CharField(max_length=255, unique=True)
    source_row = models.PositiveIntegerField(unique=True)
    title = models.TextField()
    text = models.TextField()
    subreddit = models.CharField(max_length=255, blank=True)
    ai_codes = models.JSONField(default=list)

    class Meta:
        ordering = ["source_row"]


class Annotation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    record = models.ForeignKey(Record, on_delete=models.CASCADE)
    selected_codes = models.JSONField(default=list)
    no_codes = models.BooleanField(default=False)
    ai_reviews = models.JSONField(default=dict)
    # The rater's first confirmed choices, made before seeing any AI label. Later edits
    # (allowed after the reveal) change selected_codes but never this blind record.
    blind_selected_codes = models.JSONField(null=True, blank=True)
    blind_no_codes = models.BooleanField(null=True, blank=True)
    notes = models.TextField(blank=True)
    stage = models.CharField(max_length=12, default="annotate")
    revision = models.PositiveIntegerField(default=0)
    independent_confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "record"], name="one_annotation_per_rater_record")]


class Assignment(models.Model):
    """Posts assigned to a rater. A rater with no assignments sees every post."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    record = models.ForeignKey(Record, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "record"], name="one_assignment_per_rater_record")]


class ActionLog(models.Model):
    annotation = models.ForeignKey(Annotation, on_delete=models.CASCADE)
    action = models.CharField(max_length=30)
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    request_id = models.UUIDField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

