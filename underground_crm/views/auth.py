from django.contrib.auth import login, logout
from django.shortcuts import redirect, render

from ..forms.auth import LoginForm, SignupForm


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            person = form.save()
            login(request, person)
            return redirect("/")
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
            next_url = request.GET.get("next", "/")
            return redirect(next_url)
    else:
        form = LoginForm(request)
    return render(request, "underground_crm/auth/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("/")
