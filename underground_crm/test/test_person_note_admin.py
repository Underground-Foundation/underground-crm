import django.test
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from underground_crm.admin import PersonNoteInline
from underground_crm.models import PersonNote

Person = get_user_model()


class PersonNoteInlineAuthorTest(django.test.TestCase):
    """
    Covers the author menu of the PersonNote inline on the Person admin: a new
    note offers only the current user, preselected, and an existing note keeps
    the author that wrote it.
    """

    def setUp(self):
        self.staff_user = Person.objects.create_user(
            email="note-writer@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.other_author = Person.objects.create_user(
            email="other-author@example.com", password="password"
        )
        self.subject = Person.objects.create_user(email="subject@example.com", password="password")
        request = RequestFactory().get("/")
        request.user = self.staff_user
        self.inline = PersonNoteInline(Person, admin.site)
        self.formset_class = self.inline.get_formset(request, self.subject)
        self.request = request

    def _formset(self):
        return self.formset_class(instance=self.subject)

    def test_new_note_offers_only_the_current_user(self):
        author_field = self._formset().empty_form.fields["created_by"]

        self.assertEqual(
            list(author_field.queryset.values_list("pk", flat=True)),
            [self.staff_user.pk],
            "A new note should offer no author other than the current user",
        )

    def test_new_note_preselects_the_current_user(self):
        author_field = self._formset().empty_form.fields["created_by"]

        self.assertEqual(
            author_field.initial,
            self.staff_user.pk,
            "The current user should be preselected as the author of a new note",
        )

    def test_existing_note_keeps_an_author_that_is_not_the_current_user(self):
        note = PersonNote.objects.create(
            person=self.subject, text="Called back", created_by=self.other_author
        )

        form = self._formset().forms[0]

        self.assertEqual(form.instance, note)
        self.assertTrue(
            form.fields["created_by"].disabled,
            "The author of an existing note should not be editable",
        )
        self.assertEqual(
            list(form.fields["created_by"].queryset.values_list("pk", flat=True)),
            [self.other_author.pk],
            "The existing author should validate against the restricted queryset",
        )
