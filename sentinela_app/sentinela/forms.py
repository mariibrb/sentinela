from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from clientes.models import Colaborador

CAMPO = (
    "mt-1 w-full rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-stone-900 "
    "shadow-sm outline-none focus:border-pink-500 focus:ring-2 focus:ring-pink-100"
)


class FormularioEntrada(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["class"] = CAMPO
        self.fields["password"].widget.attrs["class"] = CAMPO.replace("px-3", "pl-3 pr-12")

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        pessoa = Colaborador.objects.filter(usuario=user).first()
        if pessoa is not None and not pessoa.ativo:
            raise ValidationError("Este colaborador está inativo.", code="inactive")
