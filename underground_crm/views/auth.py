import logging

from django.contrib.auth import login, logout
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from ..forms.auth import LoginForm, SignupForm

logger = logging.getLogger(__name__)


def _get_safe_next_url(request: HttpRequest) -> str:
    """Where to send a visitor after they authenticate. We prevent this parameter
    being just anything, as an attacker could redirect them to their own site
    afterwards, tricking our users into thinking the attacking website is one of ours."""
    next_url = request.GET.get("next", None)
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    elif next_url:
        logger.warning(
            "%s was using an unsafe URL %s for redirection after login", request.user, next_url
        )
    return "/"


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            person = form.save()
            # A person fresh from a form has not been through authenticate(), which is
            # what would tie them to one of several backend options.
            login(request, person, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(_get_safe_next_url(request))
    else:
        form = SignupForm()
    return render(request, "underground_crm/auth/signup.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        form = LoginForm(request, request.POST)
        if form.is_valid():
            login(request, form.get_person())
            return redirect(_get_safe_next_url(request))
    else:
        form = LoginForm(request)
    return render(request, "underground_crm/auth/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("/")
