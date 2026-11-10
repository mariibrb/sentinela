import json

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from clientes.cnpj import somente_digitos
from clientes.escopo import (
    ESTADOS_BR,
    ITENS_CONTABEIS,
    ITENS_DP,
    ITENS_FISCAIS,
    ITENS_REGULARIZACAO,
    OPCOES_TTS,
)
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
        exclude = (
            "data_cadastro",
            "contatos",
            "escopo_fiscal",
            "tipos_icms_incentivado",
            "estados_icms_st",
            "escopo_contabil",
            "escopo_dp",
            "escopo_regularizacao",
            "estados_st",
            "busca_escopo",
        )
        widgets = {
            "codigo_dominio": forms.TextInput(attrs={**CAMPO, "maxlength": "4", "inputmode": "numeric"}),
            "tipo_estabelecimento": forms.Select(attrs=CAMPO),
            "razao_social": forms.TextInput(attrs=CAMPO),
            "nome_fantasia": forms.TextInput(attrs=CAMPO),
            "status": forms.Select(choices=[("Ativo", "Ativo"), ("Inativo", "Inativo")], attrs={**CAMPO, "id": "id_status"}),
            "responsavel_apuracao": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "responsavel_revisao": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "responsavel_operacional_contabil": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "responsavel_revisador_contabil": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "responsavel_operacional_dp": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "responsavel_revisador_dp": forms.Select(attrs={**CAMPO, "data-busca": "colaborador"}),
            "inscricao_estadual": forms.TextInput(attrs={**CAMPO, "placeholder": "Preenchida pela UF do CNPJ, ou ISENTO"}),
            "inscricao_municipal": forms.TextInput(attrs=CAMPO),
            "suframa": forms.TextInput(attrs=CAMPO),
            "cnae_principal": forms.TextInput(attrs=CAMPO),
            "cnae_descricao": forms.TextInput(attrs={**CAMPO, "placeholder": "Preenchida automaticamente pela busca do CNPJ"}),
            "ramo_atividade": forms.Select(attrs=CAMPO),
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
            "data_abertura": forms.TextInput(attrs={**CAMPO, "placeholder": "DD/MM/AAAA", "maxlength": "10"}),
            "data_inicio_cliente": forms.TextInput(attrs={**CAMPO, "placeholder": "MM/AAAA", "maxlength": "7"}),
            "data_saida_cliente": forms.TextInput(attrs={**CAMPO, "id": "id_data_saida_cliente", "placeholder": "MM/AAAA ou DD/MM/AAAA (obrigatório se Inativo)", "maxlength": "20"}),
            "resumo_cliente": forms.Textarea(
                attrs={
                    **CAMPO,
                    "rows": 4,
                    "placeholder": "Escreva um breve resumo da empresa, histórico, segmento ou pontos de atenção…",
                }
            ),
            "qtd_notas_entrada": forms.TextInput(attrs={**CAMPO, "placeholder": "Ex.: 80"}),
            "qtd_notas_saida": forms.TextInput(attrs={**CAMPO, "placeholder": "Ex.: 150"}),
            "qtd_emissao_notas_servico": forms.TextInput(attrs={**CAMPO, "placeholder": "Ex.: 25"}),
            "qtd_emissao_notas_produto": forms.TextInput(attrs={**CAMPO, "placeholder": "Ex.: 40"}),
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
        self.fields["ramo_atividade"].choices = [("", "Automático pelo CNAE ou selecione")] + list(Empresa.RAMOS_ATIVIDADE)
        self.fields["ramo_atividade"].required = False
        for nome in (
            "responsavel_apuracao",
            "responsavel_revisao",
            "responsavel_operacional_contabil",
            "responsavel_revisador_contabil",
            "responsavel_operacional_dp",
            "responsavel_revisador_dp",
        ):
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
        status = (limpo.get("status") or "Ativo").strip()
        data_saida = (limpo.get("data_saida_cliente") or "").strip()
        if status == "Inativo" and not data_saida:
            self.add_error(
                "data_saida_cliente",
                "Para cliente inativo, informe obrigatoriamente a data de saída (MM/AAAA ou DD/MM/AAAA) para sabermos a partir de quando cessam as obrigações nos setores.",
            )
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

    def aplicar_escopo_do_post(self, post_data):
        """Extrai o checklist de escopo (Fiscal, Contábil, DP, Regularização) e Contatos enviados no POST."""
        if not post_data:
            return
        empresa = self.instance

        # Contatos enviados como JSON ou listas paralelas
        contatos_json = (post_data.get("contatos_json") or "").strip()
        contatos_limpos = []
        if contatos_json:
            try:
                brutos = json.loads(contatos_json)
                if isinstance(brutos, list):
                    for item in brutos:
                        if not isinstance(item, dict):
                            continue
                        nome = str(item.get("Nome") or "").strip()
                        setor = str(item.get("Setor") or "Outro").strip()
                        email = str(item.get("E-mail") or "").strip()
                        telefone = str(item.get("Telefone") or "").strip()
                        whatsapp = bool(item.get("WhatsApp", False))
                        if any((nome, email, telefone)):
                            contatos_limpos.append({
                                "Nome": nome,
                                "Setor": setor,
                                "E-mail": email,
                                "Telefone": telefone,
                                "WhatsApp": whatsapp,
                            })
            except ValueError:
                pass
        else:
            nomes = post_data.getlist("contato_nome")
            setores = post_data.getlist("contato_setor")
            emails = post_data.getlist("contato_email")
            telefones = post_data.getlist("contato_telefone")
            whatsapps = set(post_data.getlist("contato_whatsapp_idx"))
            for idx, nome in enumerate(nomes):
                n = (nome or "").strip()
                s = (setores[idx] if idx < len(setores) else "Outro").strip()
                e = (emails[idx] if idx < len(emails) else "").strip()
                t = (telefones[idx] if idx < len(telefones) else "").strip()
                if any((n, e, t)):
                    contatos_limpos.append({
                        "Nome": n,
                        "Setor": s,
                        "E-mail": e,
                        "Telefone": t,
                        "WhatsApp": str(idx) in whatsapps,
                    })
        empresa.contatos = contatos_limpos

        marcados_fisc = set(post_data.getlist("escopo_fiscal"))
        empresa.escopo_fiscal = {item: (item in marcados_fisc) for item in ITENS_FISCAIS}

        marcados_cont = set(post_data.getlist("escopo_contabil"))
        empresa.escopo_contabil = {item: (item in marcados_cont) for item in ITENS_CONTABEIS}

        marcados_dp = set(post_data.getlist("escopo_dp"))
        empresa.escopo_dp = {item: (item in marcados_dp) for item in ITENS_DP}

        marcados_reg = set(post_data.getlist("escopo_regularizacao"))
        empresa.escopo_regularizacao = {item: (item in marcados_reg) for item in ITENS_REGULARIZACAO}

        tts = [x for x in post_data.getlist("tipos_icms_incentivado") if x in OPCOES_TTS]
        empresa.tipos_icms_incentivado = tts if empresa.escopo_fiscal.get("ICMS Incentivado") else []

        uf_icms_st = [x for x in post_data.getlist("estados_icms_st") if x in ESTADOS_BR]
        empresa.estados_icms_st = uf_icms_st if empresa.escopo_fiscal.get("Apuração: ICMS-ST") else []

        uf_st = [x for x in post_data.getlist("estados_st") if x in ESTADOS_BR]
        empresa.estados_st = uf_st if empresa.escopo_regularizacao.get("Inscrições de Substituto Tributário (ST)") else []

    def save(self, commit=True):
        empresa = super().save(commit=False)
        if self.data:
            self.aplicar_escopo_do_post(self.data)
        if commit:
            empresa.save()
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
