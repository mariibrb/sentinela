from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def administrador(usuario):
    return bool(
        usuario
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_staff or usuario.is_superuser)
    )


def apenas_administrador(view):
    @login_required
    @wraps(view)
    def protegida(request, *args, **kwargs):
        if not administrador(request.user):
            messages.error(request, "Essa área é restrita ao administrador.")
            return redirect("painel")
        return view(request, *args, **kwargs)

    return protegida
