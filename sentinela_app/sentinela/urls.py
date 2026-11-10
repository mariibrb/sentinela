from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin, messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.shortcuts import redirect
from django.urls import include, path
from django.views.defaults import bad_request

from clientes.views import area_restrita, exportar_alertas_emissao, exportar_sharepoint, historico, painel
from sentinela.forms import FormularioEntrada


def inicio(request):
    if request.user.is_authenticated:
        return redirect("painel")
    return redirect("login")


def erro_de_envio(request, exception):
    if isinstance(exception, (TooManyFilesSent, TooManyFieldsSent, RequestDataTooBig)):
        try:
            messages.error(
                request,
                f"O lote passou de {settings.DATA_UPLOAD_MAX_NUMBER_FILES} arquivos de uma vez. "
                "Junte os XML em um ZIP ou envie em partes.",
            )
            return redirect("triagem")
        except Exception:
            return bad_request(request, exception)
    return bad_request(request, exception)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", auth_views.LoginView.as_view(template_name="login.html", authentication_form=FormularioEntrada), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="sair"),
    path("", inicio, name="inicio"),
    path("painel/", painel, name="painel"),
    path("painel/alertas-emissao.xlsx", exportar_alertas_emissao, name="exportar_alertas_emissao"),
    path("restrita/", area_restrita, name="area_restrita"),
    path("historico/", historico, name="historico"),
    path("historico/sharepoint/", exportar_sharepoint, name="exportar_sharepoint"),
    path("clientes/", include("clientes.urls")),
    path("colaboradores/", include("clientes.urls_colaboradores")),
    path("triagem/", include("triagem_xml.urls")),
    path("auditoria/", include("auditoria.urls")),
]
handler400 = erro_de_envio

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
