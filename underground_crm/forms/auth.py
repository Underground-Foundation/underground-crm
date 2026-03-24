from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model

Person = get_user_model()


class SignupForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        min_length=8,
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Confirm password",
    )

    class Meta:
        model = Person
        fields = ["email", "first_name", "last_name"]

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get("password")
        pw2 = cleaned.get("password_confirm")
        if pw and pw2 and pw != pw2:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        person = super().save(commit=False)
        person.set_password(self.cleaned_data["password"])
        if commit:
            person.save()
        return person


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self._person = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        if email and password:
            self._person = authenticate(self.request, username=email, password=password)
            if self._person is None:
                raise forms.ValidationError("Invalid email or password.")
            if not self._person.is_active:
                raise forms.ValidationError("This account has been deactivated.")
        return cleaned

    def get_person(self):
        return self._person
