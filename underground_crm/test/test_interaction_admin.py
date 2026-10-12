import django.test
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from underground_crm.admin import InteractionInline
from underground_crm.models import Interaction

Person = get_user_model()


class InteractionInlineAuthorTest(django.test.TestCase):
    """
    Covers the author menu of the Interaction inline on the Person admin: a new
    interaction offers only the current user, preselected, and an existing
    interaction keeps the author that logged it.
    """

    def setUp(self):
        self.staff_user = Person.objects.create_user(
            email="interaction-logger@example.com",
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
        inline = InteractionInline(Person, admin.site)
        self.formset_class = inline.get_formset(request, self.subject)

    def _formset(self):
        return self.formset_class(instance=self.subject)

    def test_new_interaction_offers_only_the_current_user(self):
        author_field = self._formset().empty_form.fields["author"]

        self.assertEqual(
            list(author_field.queryset.values_list("pk", flat=True)),
            [self.staff_user.pk],
            "A new interaction should offer no author other than the current user",
        )

    def test_new_interaction_preselects_the_current_user(self):
        author_field = self._formset().empty_form.fields["author"]

        self.assertEqual(
            author_field.initial,
            self.staff_user.pk,
            "The current user should be preselected as the author of a new interaction",
        )

    def test_existing_interaction_keeps_an_author_that_is_not_the_current_user(self):
        interaction = Interaction.objects.create(
            person=self.subject,
            author=self.other_author,
            method=Interaction.METHOD_PHONE_CALL,
        )

        form = self._formset().forms[0]

        self.assertEqual(form.instance, interaction)
        self.assertTrue(
            form.fields["author"].disabled,
            "The author of an existing interaction should not be editable",
        )
        self.assertEqual(
            list(form.fields["author"].queryset.values_list("pk", flat=True)),
            [self.other_author.pk],
            "The existing author should validate against the restricted queryset",
        )
