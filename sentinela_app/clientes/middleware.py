from clientes.competencia import competencia_atual


class CompetenciaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.session.get("competencia"):
            request.session["competencia"] = competencia_atual()
        return self.get_response(request)
