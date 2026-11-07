from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from clientes.cnpj import somente_digitos
from clientes.models import Colaborador, Empresa, InscricaoSubstituto

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
            "inscricao_estadual": forms.TextInput(attrs={**CAMPO, "placeholder": "Preenchida pela UF do CNPJ, ou ISENTO"}),
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
        self.fields["regime_tributario"].required = True
        self.fields["regime_tributario"].error_messages = {"required": "Selecione o regime tributário."}
        self.fields["regime_pis_cofins"].required = False
        self.fields["regime_pis_cofins"].choices = [("", "Aguarda o regime tributário")] + list(Empresa.REGIMES_PIS_COFINS)
        if not self.instance.pk:
            self.initial["regime_tributario"] = ""
            self.initial["regime_pis_cofins"] = ""
        self.fields["anexo_simples"].choices = [("", "Selecione o anexo")] + list(Empresa.ANEXOS_SIMPLES)
        self.fields["perfil_estabelecimento"].choices = list(Empresa.PERFIS_ESTABELECIMENTO)
        for nome in ("responsavel_apuracao", "responsavel_revisao"):
            atual = getattr(self.instance, f"{nome}_id", None)
            opcoes = Colaborador.objects.filter(ativo=True)
            if atual:
                opcoes = Colaborador.objects.filter(Q(ativo=True) | Q(pk=atual))
            self.fields[nome].queryset = opcoes.order_by("nome")
            self.fields[nome].required = False
            self.fields[nome].empty_label = "Definir depois"
        gravadas = {}
        if self.instance.pk:
            gravadas = {item.uf: item.numero for item in self.instance.substituicoes.all()}
        for sigla, nome in InscricaoSubstituto.UFS:
            self.fields[f"ie_st_{sigla}"] = forms.CharField(
                label=sigla,
                required=False,
                initial=gravadas.get(sigla, ""),
                widget=forms.TextInput(attrs={**CAMPO, "placeholder": nome, "inputmode": "numeric"}),
            )

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

    def clean_regime_tributario(self):
        regime = (self.cleaned_data.get("regime_tributario") or "").strip()
        if regime not in dict(Empresa.REGIMES_TRIBUTARIOS):
            raise forms.ValidationError("Selecione o regime tributário.")
        return regime

    def clean_documento_regime_especial(self):
        arquivo = self.cleaned_data.get("documento_regime_especial")
        nome = getattr(arquivo, "name", "") or ""
        if nome and not nome.lower().endswith(".pdf"):
            raise forms.ValidationError("Anexe o regime especial em PDF.")
        return arquivo

    def save(self, commit=True):
        empresa = super().save(commit=commit)
        if commit:
            self.gravar_substituicoes(empresa)
        return empresa

    def gravar_substituicoes(self, empresa):
        for sigla, _nome in InscricaoSubstituto.UFS:
            numero = somente_digitos(self.cleaned_data.get(f"ie_st_{sigla}") or "")
            atual = empresa.substituicoes.filter(uf=sigla).first()
            if numero and atual:
                atual.numero = numero
                atual.save(update_fields=["numero"])
            elif numero:
                InscricaoSubstituto.objects.create(empresa=empresa, uf=sigla, numero=numero)
            elif atual:
                atual.delete()

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
        fields = (
            "nome",
            "cargo",
            "email",
            "usuario",
            "ativo",
            *(codigo for codigo, _rotulo in Colaborador.PERMISSOES),
        )
        widgets = {
            "nome": forms.TextInput(attrs=CAMPO),
            "cargo": forms.TextInput(attrs=CAMPO),
            "email": forms.EmailInput(attrs=CAMPO),
            "usuario": forms.Select(attrs=CAMPO),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Usuario = get_user_model()
        ocupados = Colaborador.objects.exclude(usuario=None)
        if self.instance.pk:
            ocupados = ocupados.exclude(pk=self.instance.pk)
        opcoes = Usuario.objects.filter(is_staff=False, is_superuser=False).exclude(
            pk__in=ocupados.values("usuario_id")
        )
        atual = self.instance.usuario_id
        if atual:
            opcoes = Usuario.objects.filter(Q(pk__in=opcoes.values("pk")) | Q(pk=atual))
        self.fields["usuario"].queryset = opcoes.order_by("username")
        self.fields["usuario"].required = False
        self.fields["usuario"].empty_label = "Sem acesso ao sistema"
        self.fields["ativo"] = forms.ChoiceField(
            label="Situação",
            choices=[("1", "Ativo"), ("0", "Inativo")],
            widget=forms.Select(attrs=CAMPO),
            initial="1" if self.instance.ativo else "0",
        )

    def clean_ativo(self):
        return self.cleaned_data["ativo"] == "1"
