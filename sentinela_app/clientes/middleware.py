from django.contrib import messages
from django.shortcuts import redirect

from clientes.acesso import administrador
from clientes.competencia import competencia_atual


class CompetenciaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.session.get("competencia"):
            request.session["competencia"] = competencia_atual()
        return self.get_response(request)


class AcessoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/") and request.user.is_authenticated and not administrador(request.user):
            messages.error(request, "O painel administrativo é restrito ao administrador.")
            return redirect("painel")
        return self.get_response(request)
