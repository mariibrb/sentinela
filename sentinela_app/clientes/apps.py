from django.apps import AppConfig


class ClientesConfig(AppConfig):
    name = "clientes"

    def ready(self):
        from clientes import sinais  # noqa: F401
