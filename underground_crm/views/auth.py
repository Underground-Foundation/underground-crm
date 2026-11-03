from django.contrib.auth import login, logout
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from ..forms.auth import LoginForm, SignupForm


def _safe_next_url(request: HttpRequest) -> str:
    """Where to send a visitor who has just logged in or signed up: the `next`
    query parameter if it stays on this site, otherwise the home page. Anybody
    can craft such a link, so following `next` unchecked would let an attacker
    use our own pages to deliver visitors to a site of their choosing."""
    next_url = request.GET.get("next", "/")
    if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return "/"


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            person = form.save()
            # A person fresh from a form has not been through authenticate(), which is
            # what would record a backend on them, and several backends are configured.
            login(request, person, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(_safe_next_url(request))
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
            return redirect(_safe_next_url(request))
    else:
        form = LoginForm(request)
    return render(request, "underground_crm/auth/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("/")
