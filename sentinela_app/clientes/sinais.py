from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from clientes.trilha import registrar


@receiver(user_logged_in)
def registrar_entrada(sender, request, user, **kwargs):
    registrar(user, None, "ENTRADA", "Acesso", "", "Entrou no Sentinela")


@receiver(user_logged_out)
def registrar_saida(sender, request, user, **kwargs):
    if user is None or not getattr(user, "is_authenticated", False):
        return
    registrar(user, None, "SAIDA", "Acesso", "", "Saiu do Sentinela")
