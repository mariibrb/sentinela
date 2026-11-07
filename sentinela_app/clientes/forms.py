from django import forms
from django.db.models import Q

from clientes.cnpj import somente_digitos
from clientes.models import Colaborador, Empresa

CAMPO = {
    "class": (
        "w-full rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-stone-900 "
        "shadow-sm outline-none focus:border-pink-500 focus:ring-2 focus:ring-pink-100"
    )
}


class EmpresaForm(forms.ModelForm):
    cnpj = forms.CharField(
        label="CNPJ",
        max_length=18,
        widget=forms.TextInput(attrs={**CAMPO, "maxlength": "18", "inputmode": "numeric", "placeholder": "45785442000390"}),
    )

    class Meta:
        model = Empresa
        exclude = ("data_cadastro",)
        widgets = {
            "codigo_dominio": forms.TextInput(attrs={**CAMPO, "maxlength": "4", "inputmode": "numeric"}),
            "tipo_estabelecimento": forms.Select(attrs=CAMPO),
            "razao_social": forms.TextInput(attrs=CAMPO),
            "nome_fantasia": forms.TextInput(attrs=CAMPO),
            "status": forms.TextInput(attrs=CAMPO),
            "responsavel_apuracao": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "responsavel_revisao": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "inscricao_estadual": forms.TextInput(attrs=CAMPO),
            "inscricao_municipal": forms.TextInput(attrs=CAMPO),
            "suframa": forms.TextInput(attrs=CAMPO),
            "cnae_principal": forms.TextInput(attrs=CAMPO),
            "cep": forms.TextInput(attrs=CAMPO),
            "logradouro": forms.TextInput(attrs=CAMPO),
            "numero": forms.TextInput(attrs=CAMPO),
            "complemento": forms.TextInput(attrs=CAMPO),
            "bairro": forms.TextInput(attrs=CAMPO),
            "uf": forms.TextInput(attrs={**CAMPO, "maxlength": "2"}),
            "municipio": forms.TextInput(attrs=CAMPO),
            "codigo_ibge": forms.TextInput(attrs={**CAMPO, "maxlength": "7"}),
            "regime_tributario": forms.Select(attrs=CAMPO),
            "anexo_simples": forms.Select(attrs=CAMPO),
            "regime_pis_cofins": forms.Select(attrs={**CAMPO, "aria-readonly": "true"}),
            "perfil_estabelecimento": forms.Select(attrs={**CAMPO, "id": "id_perfil_estabelecimento"}),
            "documento_regime_especial": forms.ClearableFileInput(attrs={"class": CAMPO["class"], "accept": "application/pdf,.pdf"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["regime_tributario"].choices = [("", "Selecione o regime")] + list(Empresa.REGIMES_TRIBUTARIOS)
        self.fields["regime_pis_cofins"].required = False
        self.fields["regime_pis_cofins"].choices = list(Empresa.REGIMES_PIS_COFINS)
        self.fields["anexo_simples"].choices = [("", "Selecione o anexo")] + list(Empresa.ANEXOS_SIMPLES)
        self.fields["perfil_estabelecimento"].choices = list(Empresa.PERFIS_ESTABELECIMENTO)
        for nome in ("responsavel_apuracao", "responsavel_revisao"):
            atual = getattr(self.instance, f"{nome}_id", None)
            opcoes = Colaborador.objects.filter(ativo=True)
            if atual:
                opcoes = Colaborador.objects.filter(Q(ativo=True) | Q(pk=atual))
            self.fields[nome].queryset = opcoes.order_by("nome")
            self.fields[nome].empty_label = "Buscar colaborador"

    def clean(self):
        limpo = super().clean()
        tributario = limpo.get("regime_tributario")
        vinculado = Empresa.pis_cofins_do_regime(tributario)
        if vinculado:
            limpo["regime_pis_cofins"] = vinculado
        if tributario == "Simples Nacional" and limpo.get("anexo_simples") not in dict(Empresa.ANEXOS_SIMPLES):
            self.add_error("anexo_simples", "No Simples Nacional, informe o anexo. O PIS/COFINS segue esse anexo.")
        perfil = limpo.get("perfil_estabelecimento")
        arquivo = limpo.get("documento_regime_especial")
        if perfil in Empresa.PERFIS_COM_PDF:
            limpo["possui_regime_especial"] = True
            if not arquivo and "documento_regime_especial" not in self.errors:
                self.add_error("documento_regime_especial", "Anexe o PDF do PTA ou do regime especial.")
        return limpo

    def clean_documento_regime_especial(self):
        arquivo = self.cleaned_data.get("documento_regime_especial")
        nome = getattr(arquivo, "name", "") or ""
        if nome and not nome.lower().endswith(".pdf"):
            raise forms.ValidationError("Anexe o regime especial em PDF.")
        return arquivo

    def clean_codigo_dominio(self):
        codigo = somente_digitos(self.cleaned_data["codigo_dominio"])
        if not codigo or len(codigo) > 4:
            raise forms.ValidationError("O código Domínio tem até 4 números.")
        return codigo

    def clean_cnpj(self):
        cnpj = somente_digitos(self.cleaned_data["cnpj"])
        if len(cnpj) != 14:
            raise forms.ValidationError("O CNPJ precisa ter 14 números.")
        return cnpj

    def clean_uf(self):
        return self.cleaned_data["uf"].strip().upper()

    def clean_codigo_ibge(self):
        codigo = somente_digitos(self.cleaned_data["codigo_ibge"])
        if len(codigo) != 7:
            raise forms.ValidationError("O código IBGE do município tem 7 números.")
        return codigo


class ColaboradorForm(forms.ModelForm):
    class Meta:
        model = Colaborador
        fields = ("nome", "cargo", "email", "ativo")
        widgets = {
            "nome": forms.TextInput(attrs=CAMPO),
            "cargo": forms.TextInput(attrs=CAMPO),
            "email": forms.EmailInput(attrs=CAMPO),
        }
