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
        # Like the study workbook: E–AG are subthemes ("_GPT"), AH–AV are themes.
        sheet.append(["id", "title", "text", "subreddit",
                      *[f"RQ1{'_GPT' if i < 34 else ''}:Code {get_column_letter(i)}" for i in range(5, 49)]])
        sheet.append(["post-1", "A post to annotate", "Body text\n\nWith a second paragraph.", "ChatGPT", 1, 0, 1, *([0] * 41)])
        sheet.append(["post-2", "No AI codes", "Another post.", "CharacterAI", *([0] * 44)])
        sheet.append(["post-3", "AI theme and subtheme", "Third post.", "ChatGPT", 1, *([0] * 28), 1, *([0] * 14)])
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

    @staticmethod
    def rows(workbook, name):
        sheet = workbook[name]
        headers = [c.value for c in sheet[1]]
        return [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]

    def download(self):
        return load_workbook(io.BytesIO(self.client.get("/api/export.xlsx").content))

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
        response = self.action("confirm_independent", 1)
        # G was chosen by both the rater and the AI, so it is agreed without review.
        self.assertEqual(response.json()["annotation"]["ai_reviews"], {"G": "agreed"})
        self.assertEqual(self.action("complete", 2).status_code, 400)
        self.action("review_ai", 2, ai_reviews={"E": "accepted"})
        response = self.action("review_ai", 3, ai_reviews={"E": "rejected", "G": "rejected"})
        self.assertEqual(response.json()["annotation"]["ai_reviews"], {"E": "rejected", "G": "agreed"})
        self.assertTrue(response.json()["workbook_synced"])
        response = self.action("complete", 4)
        self.assertEqual(response.json()["annotation"]["stage"], "complete")
        self.assertEqual(ActionLog.objects.count(), 5)
        workbook = load_workbook(self.output)
        self.assertEqual(workbook.sheetnames, ["Final annotations", "AI corrections", "Before AI reveal", "Interaction log"])
        [final] = self.rows(workbook, "Final annotations")
        self.assertEqual(final["rater_email"], self.user.email)
        self.assertEqual(final["post_id"], "post-1")
        self.assertEqual((final["RQ1_GPT:Code F"], final["RQ1_GPT:Code G"], final["RQ1_GPT:Code E"]), (1, 1, 0))
        self.assertEqual(final["edited_after_ai"], 0)
        [correction] = self.rows(workbook, "AI corrections")
        self.assertEqual(correction["RQ1_GPT:Code E"], "rejected")
        self.assertEqual(correction["RQ1_GPT:Code G"], "agreed")
        self.assertEqual(correction["RQ1_GPT:Code F"], "missed")  # rater yes, AI no
        self.assertIsNone(correction["RQ1_GPT:Code H"])  # neither
        [blind] = self.rows(workbook, "Before AI reveal")
        self.assertEqual((blind["RQ1_GPT:Code F"], blind["RQ1_GPT:Code G"], blind["RQ1_GPT:Code E"]), (1, 1, 0))
        self.assertEqual(len(self.rows(workbook, "Interaction log")), 5)
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
        raters = [r["rater_email"] for r in self.rows(workbook, "Final annotations")]
        self.assertEqual(raters, [self.other_user.email])
        self.assertEqual(len(self.rows(workbook, "Interaction log")), 1)
        workbook.close()
        master = load_workbook(self.output)
        self.assertEqual(len(self.rows(master, "Final annotations")), 2)
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
        self.action("select_codes", 0, selected_codes=["F"])
        workbook = self.download()
        self.assertEqual(len(self.rows(workbook, "Final annotations")), 1)
        # Comparisons with the AI and the blind record only appear after confirmation.
        self.assertEqual(self.rows(workbook, "AI corrections"), [])
        self.assertEqual(self.rows(workbook, "Before AI reveal"), [])
        workbook.close()
        self.action("confirm_independent", 1)
        workbook = self.download()
        self.assertEqual(self.rows(workbook, "AI corrections")[0]["RQ1_GPT:Code F"], "missed")
        self.assertEqual(self.rows(workbook, "Before AI reveal")[0]["RQ1_GPT:Code F"], 1)
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

    def test_only_unmatched_ai_codes_need_review(self):
        self.action("select_codes", 0, selected_codes=["E", "G"])
        response = self.action("confirm_independent", 1)
        self.assertEqual(response.json()["annotation"]["ai_reviews"], {"E": "agreed", "G": "agreed"})
        self.assertEqual(self.action("complete", 2).json()["annotation"]["stage"], "complete")

    def test_review_outcomes_hidden_until_confirmation(self):
        self.action("select_codes", 0, selected_codes=["E", "F"])
        def outcomes():
            workbook = self.download()
            rows = self.rows(workbook, "AI corrections")
            workbook.close()
            return [rows[0][f"RQ1_GPT:Code {key}"] for key in "EFG"] if rows else None
        self.assertIsNone(outcomes())  # "missed" would leak the AI label for F
        self.action("confirm_independent", 1)
        self.assertEqual(outcomes(), ["agreed", "missed", None])  # G awaits review
        self.action("review_ai", 2, ai_reviews={"G": "accepted"})
        self.assertEqual(outcomes(), ["agreed", "missed", "accepted"])

    def test_ai_theme_codes_need_no_review(self):
        self.url = f'/api/records/{Record.objects.get(external_id="post-3").pk}/annotation/'
        self.assertEqual(Record.objects.get(external_id="post-3").ai_codes, ["E", "AH"])
        self.action("select_codes", 0, selected_codes=["F"])
        self.action("confirm_independent", 1)
        self.assertEqual(self.action("complete", 2).status_code, 400)  # subtheme E still needs a decision
        response = self.action("review_ai", 2, ai_reviews={"E": "rejected", "AH": "accepted"})
        self.assertEqual(response.json()["annotation"]["ai_reviews"], {"E": "rejected"})
        self.assertEqual(self.action("complete", 3).json()["annotation"]["stage"], "complete")

    def test_edit_codes_after_reveal_keeps_blind_record(self):
        self.action("select_codes", 0, selected_codes=["F"])
        self.action("confirm_independent", 1)
        self.action("review_ai", 2, ai_reviews={"E": "rejected", "G": "accepted"})
        self.action("complete", 3)
        self.assertEqual(self.action("select_codes", 4, selected_codes=["E"]).status_code, 400)
        response = self.action("edit_codes", 4)
        self.assertEqual(response.json()["annotation"]["stage"], "annotate")
        self.assertIsNone(response.json()["annotation"]["completed_at"])
        self.assertEqual(response.json()["ai_codes"], ["E", "G"])  # AI stays revealed while editing
        self.assertEqual(self.action("review_ai", 5, ai_reviews={"E": "accepted"}).status_code, 400)
        self.assertEqual(self.action("complete", 5).status_code, 400)
        self.action("select_codes", 5, selected_codes=["E", "F"])
        response = self.action("confirm_independent", 6)
        # E now matches; the earlier decision on still-unmatched G is kept.
        self.assertEqual(response.json()["annotation"]["ai_reviews"], {"E": "agreed", "G": "accepted"})
        self.assertEqual(response.json()["annotation"]["stage"], "verify")
        self.assertEqual(self.action("complete", 7).json()["annotation"]["stage"], "complete")
        annotation = Annotation.objects.get()
        self.assertEqual(annotation.blind_selected_codes, ["F"])
        self.assertEqual(annotation.selected_codes, ["E", "F"])
        workbook = load_workbook(self.output)
        [final] = self.rows(workbook, "Final annotations")
        self.assertEqual((final["RQ1_GPT:Code E"], final["RQ1_GPT:Code F"], final["edited_after_ai"]), (1, 1, 1))
        [blind] = self.rows(workbook, "Before AI reveal")
        self.assertEqual((blind["RQ1_GPT:Code E"], blind["RQ1_GPT:Code F"]), (0, 1))
        workbook.close()

    def test_assigned_rater_sees_only_assigned_posts(self):
        csv_path = Path(self.temporary.name) / "alex.csv"
        call_command("create_rater", email="alex@example.edu", password="111", name="Alex", posts="2-3",
                     csv=str(csv_path), stdout=io.StringIO())
        alex = get_user_model().objects.get(email="alex@example.edu")
        self.assertTrue(alex.check_password("111"))
        self.client.force_login(alex)
        visible = [r["external_id"] for r in self.client.get("/api/project/").json()["records"]]
        self.assertEqual(visible, ["post-2", "post-3"])
        self.assertEqual(self.client.get(f"/api/records/{self.record.pk}/").status_code, 404)
        self.assertEqual(self.action("select_codes", 0, selected_codes=["E"]).status_code, 404)
        lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual([line.split(",")[0] for line in lines], ["id", "post-2", "post-3"])
        # Unassigned raters still see every post.
        self.client.force_login(self.user)
        self.assertEqual(len(self.client.get("/api/project/").json()["records"]), 3)

    def test_reset_deletes_only_own_annotations(self):
        self.action("select_codes", 0, selected_codes=["E"])
        self.client.force_login(self.other_user)
        self.action("select_codes", 0, selected_codes=["F"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post("/api/annotations/reset/", {}, content_type="application/json").status_code, 400)
        response = self.client.post("/api/annotations/reset/", {"confirm": "RESET"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(Annotation.objects.values_list("user__email", flat=True)), ["two@example.edu"])
        self.assertEqual(ActionLog.objects.count(), 1)
        master = load_workbook(self.output)
        self.assertEqual([r["rater_email"] for r in self.rows(master, "Final annotations")], ["two@example.edu"])
        master.close()

    def test_legacy_confirmed_annotation_gets_agreement_on_completion(self):
        self.action("select_codes", 0, selected_codes=["E"])
        self.action("confirm_independent", 1)
        Annotation.objects.update(ai_reviews={})  # confirmed before automatic agreement existed
        self.action("review_ai", 2, ai_reviews={"G": "accepted"})
        response = self.action("complete", 3)
        self.assertEqual(response.json()["annotation"]["ai_reviews"], {"E": "agreed", "G": "accepted"})

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
        sheet = workbook["Final annotations"]
        cell = sheet.cell(row=2, column=[c.value for c in sheet[1]].index("notes") + 1)
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("=HYPERLINK"))
        workbook.close()

    def test_import_is_idempotent_and_different_dataset_refused(self):
        self.action("select_codes", 0, selected_codes=["E"])
        call_command("import_workbook", str(self.source), stdout=io.StringIO())
        self.assertEqual(Record.objects.count(), 3)
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

