from django.contrib.auth.forms import AuthenticationForm

CAMPO = (
    "mt-1 w-full rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-stone-900 "
    "shadow-sm outline-none focus:border-pink-500 focus:ring-2 focus:ring-pink-100"
)


class FormularioEntrada(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["class"] = CAMPO
        self.fields["password"].widget.attrs["class"] = CAMPO
