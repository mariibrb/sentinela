from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from clientes.models import Colaborador


def administrador(usuario):
    return bool(
        usuario
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_staff or usuario.is_superuser)
    )


def mapa_permissoes(usuario):
    if administrador(usuario):
        return {codigo: True for codigo, _rotulo in Colaborador.PERMISSOES}
    pessoa = None
    if usuario is not None and getattr(usuario, "is_authenticated", False):
        pessoa = Colaborador.objects.filter(usuario=usuario, ativo=True).first()
    return {codigo: bool(pessoa and getattr(pessoa, codigo)) for codigo, _rotulo in Colaborador.PERMISSOES}


def pode(usuario, codigo):
    return mapa_permissoes(usuario).get(codigo, False)


def exige(codigo):
    def decorador(view):
        @login_required
        @wraps(view)
        def protegida(request, *args, **kwargs):
            if not pode(request.user, codigo):
                messages.error(request, "Seu usuário não tem permissão para esta ação.")
                return redirect("painel")
            return view(request, *args, **kwargs)

        return protegida

    return decorador


def apenas_administrador(view):
    @login_required
    @wraps(view)
    def protegida(request, *args, **kwargs):
        if not administrador(request.user):
            messages.error(request, "Essa área é restrita ao administrador.")
            return redirect("painel")
        return view(request, *args, **kwargs)

    return protegida
