from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import include, path

from clientes.views import painel
from sentinela.forms import FormularioEntrada


def inicio(request):
    if request.user.is_authenticated:
        return redirect("painel")
    return redirect("login")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", auth_views.LoginView.as_view(template_name="login.html", authentication_form=FormularioEntrada), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="sair"),
    path("", inicio, name="inicio"),
    path("painel/", painel, name="painel"),
    path("clientes/", include("clientes.urls")),
    path("triagem/", include("triagem_xml.urls")),
    path("auditoria/", include("auditoria.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
