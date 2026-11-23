import hashlib
import io
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from .models import ActionLog, Annotation, Dataset, Record


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AnnotationWorkflowTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "study.xlsx"
        self.output = root / "annotation_verification.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["id", "title", "text", "subreddit", *[f"RQ1_GPT:Code {get_column_letter(i)}" for i in range(5, 49)]])
        sheet.append(["post-1", "A post to annotate", "Body text\n\nWith a second paragraph.", "ChatGPT", 1, 0, 1, *([0] * 41)])
        sheet.append(["post-2", "No AI codes", "Another post.", "CharacterAI", *([0] * 44)])
        workbook.save(self.source)
        self.original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.settings_override = override_settings(DATA_DIR=root, SOURCE_WORKBOOK=self.source,
                                                   EXPORT_WORKBOOK=self.output, ANNOTATION_LOCK=root / "lock")
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        call_command("import_workbook", str(self.source), stdout=io.StringIO())
        self.user = get_user_model().objects.create_user("rater_one", "one@example.edu", "Study-testing-2026")
        self.other_user = get_user_model().objects.create_user("rater_two", "two@example.edu", "Study-testing-2026")
        self.client.force_login(self.user)
        self.record = Record.objects.get(external_id="post-1")
        self.url = f"/api/records/{self.record.pk}/annotation/"

    def action(self, action, revision, **payload):
        return self.client.post(self.url, {"action": action, "revision": revision, **payload}, content_type="application/json")

    def test_login_required_and_ai_hidden_until_confirmation(self):
        anonymous = Client()
        self.assertEqual(anonymous.get("/api/project/").status_code, 401)
        response = self.client.get(f"/api/records/{self.record.pk}/")
        self.assertIsNone(response.json()["record"]["ai_codes"])
        self.assertNotIn("ai_codes", self.client.get("/api/project/").json()["records"][0])
        self.assertEqual(self.action("review_ai", 0, ai_reviews={"E": "accepted"}).status_code, 400)
        self.assertEqual(self.action("complete", 0).status_code, 400)
        self.assertEqual(self.action("confirm_independent", 0).status_code, 400)
        self.assertFalse(Annotation.objects.exists())
        self.assertEqual(self.action("select_codes", 0, selected_codes=["F"], no_codes=False).status_code, 200)
        self.assertIsNone(self.client.get(f"/api/records/{self.record.pk}/").json()["record"]["ai_codes"])
        response = self.action("confirm_independent", 1)
        self.assertEqual(response.json()["ai_codes"], ["E", "G"])
        self.assertEqual(self.action("select_codes", 2, selected_codes=["E"]).status_code, 400)

    def test_complete_workflow_writes_every_action_and_preserves_source(self):
        self.action("select_codes", 0, selected_codes=["F", "G"], no_codes=False)
        self.action("confirm_independent", 1)
        self.assertEqual(self.action("complete", 2).status_code, 400)
        self.action("review_ai", 2, ai_reviews={"E": "rejected"})
        response = self.action("review_ai", 3, ai_reviews={"E": "rejected", "G": "accepted"})
        self.assertTrue(response.json()["workbook_synced"])
        response = self.action("complete", 4)
        self.assertEqual(response.json()["annotation"]["stage"], "complete")
        self.assertEqual(ActionLog.objects.count(), 5)
        workbook = load_workbook(self.output)
        self.assertEqual(workbook.worksheets[0]["E2"].value, 1)
        sheet = workbook["Rater annotations"]
        headers = [c.value for c in sheet[1]]
        row = dict(zip(headers, [c.value for c in sheet[2]]))
        self.assertEqual(row["rater_id"], self.user.pk)
        self.assertEqual(row["rater_email"], self.user.email)
        self.assertEqual(row["HUMAN:RQ1_GPT:Code F"], 1)
        self.assertEqual(row["HUMAN:RQ1_GPT:Code E"], 0)
        self.assertEqual(row["AI_REVIEW:RQ1_GPT:Code E"], "rejected")
        self.assertEqual(row["AI_REVIEW:RQ1_GPT:Code G"], "accepted")
        self.assertIsNone(row["AI_REVIEW:RQ1_GPT:Code F"])
        self.assertEqual(workbook["Action log"].max_row, 6)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        workbook.close()

    def test_accounts_are_isolated_in_api_and_download(self):
        self.action("select_codes", 0, selected_codes=["E"])
        self.action("save_notes", 1, notes="Rater one private note")
        self.client.force_login(self.other_user)
        response = self.client.get(f"/api/records/{self.record.pk}/").json()
        self.assertEqual(response["annotation"]["selected_codes"], [])
        self.assertEqual(response["annotation"]["notes"], "")
        self.action("select_codes", 0, selected_codes=["F"])
        download = self.client.get("/api/export.xlsx")
        workbook = load_workbook(io.BytesIO(download.content))
        raters = [r[1] for r in workbook["Rater annotations"].iter_rows(min_row=2, values_only=True)]
        self.assertEqual(raters, [self.other_user.email])
        self.assertEqual(workbook["Action log"].max_row, 2)
        workbook.close()
        master = load_workbook(self.output)
        self.assertEqual(master["Rater annotations"].max_row, 3)
        master.close()

    def test_conflicting_revision_cannot_overwrite_work(self):
        self.action("select_codes", 0, selected_codes=["E"])
        response = self.action("select_codes", 0, selected_codes=["F"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Annotation.objects.get().selected_codes, ["E"])
        self.assertEqual(ActionLog.objects.count(), 1)

    def test_retry_of_same_request_does_not_duplicate_action(self):
        request_id = str(uuid.uuid4())
        first = self.action("select_codes", 0, selected_codes=["E"], request_id=request_id)
        second = self.action("select_codes", 0, selected_codes=["E"], request_id=request_id)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["annotation"]["revision"], second.json()["annotation"]["revision"])
        self.assertEqual(ActionLog.objects.count(), 1)
        self.assertEqual(Annotation.objects.get().revision, 1)

    def test_personal_download_does_not_reveal_ai_before_independent_confirmation(self):
        download = self.client.get("/api/export.xlsx")
        workbook = load_workbook(io.BytesIO(download.content))
        self.assertIsNone(workbook.worksheets[0]["E2"].value)
        workbook.close()
        self.action("select_codes", 0, selected_codes=["E"])
        self.action("confirm_independent", 1)
        download = self.client.get("/api/export.xlsx")
        workbook = load_workbook(io.BytesIO(download.content))
        self.assertEqual(workbook.worksheets[0]["E2"].value, 1)
        self.assertIsNone(workbook.worksheets[0]["E3"].value)
        workbook.close()

    def test_invalid_payloads_cannot_bypass_stage_or_code_validation(self):
        for payload in ({}, "invalid", None, [123]):
            self.assertEqual(self.action("select_codes", 0, selected_codes=payload).status_code, 400)
        self.assertEqual(self.action("select_codes", 0, selected_codes=["AW"]).status_code, 400)
        self.assertEqual(self.action("select_codes", 0, selected_codes=["E"], no_codes=True).status_code, 400)
        self.assertEqual(self.action("save_notes", 0, notes=7).status_code, 400)
        self.action("select_codes", 0, selected_codes=[], no_codes=True)
        self.action("confirm_independent", 1)
        self.assertEqual(self.action("review_ai", 2, ai_reviews={"F": "accepted"}).status_code, 400)
        self.assertEqual(self.action("review_ai", 2, ai_reviews={"E": True}).status_code, 400)
        self.assertEqual(self.action("review_ai", 2, ai_reviews=["E"]).status_code, 400)

    def test_zero_ai_codes_can_be_completed(self):
        self.url = f'/api/records/{Record.objects.get(external_id="post-2").pk}/annotation/'
        self.action("select_codes", 0, selected_codes=["E"])
        self.action("confirm_independent", 1)
        self.assertEqual(self.action("complete", 2).json()["annotation"]["stage"], "complete")

    def test_revisiting_ai_decision_reopens_completion(self):
        self.action("select_codes", 0, selected_codes=["E"])
        self.action("confirm_independent", 1)
        self.action("review_ai", 2, ai_reviews={"E": "accepted", "G": "accepted"})
        self.action("complete", 3)
        response = self.action("review_ai", 4, ai_reviews={"E": "rejected", "G": "accepted"})
        self.assertEqual(response.json()["annotation"]["stage"], "verify")
        self.assertIsNone(response.json()["annotation"]["completed_at"])

    def test_export_failure_preserves_account_and_can_retry(self):
        with self.assertLogs("annotations.workbooks", level="ERROR"):
            with patch("annotations.workbooks.os.replace", side_effect=OSError("Disk unavailable")):
                response = self.action("select_codes", 0, selected_codes=["E"])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["workbook_synced"])
        self.assertEqual(Annotation.objects.get().selected_codes, ["E"])
        self.assertTrue(Dataset.objects.get().workbook_dirty)
        response = self.client.post("/api/workbook/sync/", {}, content_type="application/json")
        self.assertTrue(response.json()["workbook_synced"])
        self.assertFalse(Dataset.objects.get().workbook_dirty)

    def test_notes_export_as_literal_text_and_do_not_leak_to_another_account(self):
        self.action("save_notes", 0, notes='=HYPERLINK("https://example.com","test")')
        workbook = load_workbook(self.output)
        cell = workbook["Rater annotations"]["K2"]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("=HYPERLINK"))
        workbook.close()

    def test_import_is_idempotent_and_different_dataset_refused(self):
        self.action("select_codes", 0, selected_codes=["E"])
        call_command("import_workbook", str(self.source), stdout=io.StringIO())
        self.assertEqual(Record.objects.count(), 2)
        self.assertEqual(Annotation.objects.count(), 1)
        workbook = load_workbook(self.source)
        workbook.active["B2"] = "Changed source"
        workbook.save(self.source)
        with self.assertRaises(CommandError):
            call_command("import_workbook", str(self.source), stdout=io.StringIO())

    def test_email_only_registration_accepts_simple_passwords(self):
        for index, password in enumerate(["1", "password", "same"]):
            with self.subTest(password=password):
                email = f"simple{index}@example.edu"
                client = Client()
                response = client.post("/api/auth/register/", {
                    "email": email.upper(), "password": password,
                }, content_type="application/json")
                self.assertEqual(response.status_code, 201, response.content)
                self.assertNotIn("username", response.json()["user"])
                user = get_user_model().objects.get(email=email)
                self.assertTrue(user.check_password(password))
                self.assertNotEqual(user.password, password)
                client.post("/api/auth/logout/", {}, content_type="application/json")
                response = client.post("/api/auth/login/", {"email": email, "password": "incorrect"}, content_type="application/json")
                self.assertEqual(response.status_code, 400)
                response = client.post("/api/auth/login/", {"email": f"  {email.upper()}  ", "password": password}, content_type="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["user"]["email"], email)

    def test_existing_account_can_log_in_by_email_and_keep_annotations(self):
        self.action("select_codes", 0, selected_codes=["E"])
        client = Client()
        response = client.post("/api/auth/login/", {"email": "ONE@example.edu", "password": "Study-testing-2026"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], self.user.pk)
        detail = client.get(f"/api/records/{self.record.pk}/").json()
        self.assertEqual(detail["annotation"]["selected_codes"], ["E"])
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_registration_rejects_duplicate_email_ignoring_case(self):
        client = Client()
        response = client.post("/api/auth/register/", {"email": "ONE@EXAMPLE.EDU", "password": "1"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 2)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Study-testing-2026"))

    def test_ambiguous_email_does_not_choose_an_account(self):
        get_user_model().objects.create_user("legacy_duplicate", "ONE@EXAMPLE.EDU", "Study-testing-2026")
        client = Client()
        response = client.post("/api/auth/login/", {"email": "one@example.edu", "password": "Study-testing-2026"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(client.get("/api/session/").json()["user"])

    def test_real_login_registration_csrf_and_logout(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/api/auth/login/", {}, content_type="application/json").status_code, 403)
        client.get("/api/session/")
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post("/api/auth/login/", {"email": "one@example.edu", "password": "wrong"}, content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 400)
        response = client.post("/api/auth/register/", {"email": "new@example.edu", "password": "Research-coding-2026!"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(client.get("/api/session/").json()["user"]["email"], "new@example.edu")
        self.assertEqual(client.get("/api/project/")["Cache-Control"], "no-store, private")
        client.post("/api/auth/logout/", {}, content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(client.get("/api/project/").status_code, 401)
        response = client.post("/api/auth/login/", {"email": "new@example.edu", "password": "Research-coding-2026!"}, content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200)

