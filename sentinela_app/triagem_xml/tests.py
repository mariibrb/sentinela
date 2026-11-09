import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from auditoria.apuracao_motor import MotorApuracaoSentinela
from clientes.models import Empresa, NotaServico
from triagem_xml.coletor_nfse import (
    VERSAO_COLETOR,
    _carregar_modulos_coletor_145,
    gravar_planilha_empresa,
    importar_planilha_coletor,
    salvar_notas_no_banco,
)


class ColetorNFSeSentinelaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin_teste", password="123", email="a@b.com")
        self.empresa = Empresa.objects.create(
            codigo_dominio="0042",
            cnpj="12345678000195",
            razao_social="CLINICA EXEMPLO LTDA",
            status="Ativo",
            cnae_principal="8630503",
            cep="30140071",
            logradouro="Rua Teste",
            numero="100",
            bairro="Centro",
            uf="MG",
            municipio="Belo Horizonte",
            codigo_ibge="3106200",
            regime_tributario="Lucro Presumido",
            regime_pis_cofins="Cumulativo",
        )
        self.outra_empresa = Empresa.objects.create(
            codigo_dominio="0099",
            cnpj="98765432000110",
            razao_social="OUTRA EMPRESA S/A",
            status="Ativo",
            cnae_principal="6201501",
            cep="01001000",
            logradouro="Av Paulista",
            numero="200",
            bairro="Bela Vista",
            uf="SP",
            municipio="São Paulo",
            codigo_ibge="3550308",
            regime_tributario="Lucro Real",
            regime_pis_cofins="Não-Cumulativo",
        )

    def test_carrega_motor_coletor_145(self):
        self.assertEqual(VERSAO_COLETOR, "1.4.5")
        portal, ui = _carregar_modulos_coletor_145()
        ui.aplicar_tipo("prestados")
        self.assertIn("Emitidas", portal.URL_RECEBIDAS)
        ui.aplicar_tipo("tomados")
        self.assertIn("Recebidas", portal.URL_RECEBIDAS)

    def test_identificacao_empresa_planilha_e_bloqueio_outro_cnpj(self):
        notas_prestadas = [
            {
                "numero": "10",
                "chave_acesso": "31062002212345678000195000000000001026101234567890",
                "data_emissao": "15/10/2026",
                "competencia_emissao": "10/2026",
                "razao_emitente": "TOMADOR CLIENTE UM LTDA",
                "codigo_tributacao_nacional": "010101 - Análise de sistemas",
                "valor_servico": "5.000,00",
                "valor_liquido": "4.692,50",
                "retencao": "ISSQN Não Retido",
                "valor_issqn": "250,00",
                "contrib_sociais_retidas": "232,50",
                "contrib_previdenciaria_retida": "0,00",
                "irrf": "75,00",
                "status": "Ativa",
                "data_cancelamento": "",
            },
            {
                # Nota emitida em novembro (mês seguinte) dentro do lote ou transição
                "numero": "12",
                "chave_acesso": "31062002212345678000195000000000001226111234567890",
                "data_emissao": "02/11/2026",
                "competencia_emissao": "10/2026",
                "razao_emitente": "TOMADOR TRANSICAO LTDA",
                "codigo_tributacao_nacional": "010101 - Análise de sistemas",
                "valor_servico": "2.000,00",
                "valor_liquido": "2.000,00",
                "retencao": "Não",
                "valor_issqn": "100,00",
                "contrib_sociais_retidas": "",
                "contrib_previdenciaria_retida": "",
                "irrf": "",
                "status": "Ativa",
                "data_cancelamento": "",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "prestadas.xlsx"
            gravar_planilha_empresa(
                notas_prestadas,
                caminho,
                rotulo_periodo="Outubro de 2026",
                tipo="prestados",
                empresa=self.empresa,
            )
            conteudo = caminho.read_bytes()

        # Tentar importar na empresa errada deve bloquear pelo CNPJ gravado na planilha
        res_errado = importar_planilha_coletor(
            empresa=self.outra_empresa,
            competencia="102026",
            arquivo_bytes=conteudo,
            nome_arquivo="prestadas.xlsx",
        )
        self.assertFalse(res_errado["ok"])
        self.assertIn("pertence ao CNPJ", res_errado["erro"])

        # Importar na empresa certa deve funcionar e separar o mês seguinte
        res_certo = importar_planilha_coletor(
            empresa=self.empresa,
            competencia="102026",
            arquivo_bytes=conteudo,
            nome_arquivo="prestadas.xlsx",
        )
        self.assertTrue(res_certo["ok"])
        self.assertEqual(res_certo["gravadas"], 2)

        motor_out = MotorApuracaoSentinela(self.empresa, "102026")
        nfse_out = motor_out.calcular_apuracao_nfse()

        # Nota 10 fica em outubro (R$ 5000), Nota 12 foi separada para novembro (112026) por ter emissão em 02/11/2026
        self.assertEqual(nfse_out["prestados"]["qtd_ativas"], 1)
        self.assertEqual(str(nfse_out["prestados"]["valor_servico"]), "5000.00")
        self.assertEqual(str(nfse_out["prestados"]["csrf_retido"]), "232.50")
        self.assertEqual(nfse_out["escrituracao"]["qtd_mes_seguinte"], 1)
        self.assertEqual(str(nfse_out["escrituracao"]["valor_mes_seguinte"]), "2000.00")

        # Salto de numeração entre 10 e 12 (falta a nota 11!)
        self.assertEqual(len(nfse_out["buracos_prestados"]), 1)
        self.assertEqual(nfse_out["buracos_prestados"][0]["faixa"], "11")

        # Quando avançamos para novembro (112026), a nota 12 aparece como vinda do mês anterior!
        motor_nov = MotorApuracaoSentinela(self.empresa, "112026")
        nfse_nov = motor_nov.calcular_apuracao_nfse()
        self.assertEqual(nfse_nov["escrituracao"]["qtd_vindas_anterior"], 1)
        self.assertEqual(str(nfse_nov["prestados"]["valor_servico"]), "2000.00")

    def test_views_coletor_e_auditoria(self):
        self.client.force_login(self.user)
        sessao = self.client.session
        sessao["empresa_id"] = self.empresa.pk
        sessao["competencia"] = "102026"
        sessao.save()

        salvar_notas_no_banco(
            empresa=self.empresa,
            competencia="102026",
            tipo_grupo="tomados",
            notas=[
                {
                    "numero": "55",
                    "chave_acesso": "31062002299999999000199000000000005526101234567890",
                    "data_emissao": "10/10/2026",
                    "competencia_emissao": "10/2026",
                    "razao_emitente": "FORNECEDOR SERVICOS LTDA",
                    "valor_servico": "1.000,00",
                    "valor_liquido": "950,00",
                    "retencao": "ISSQN Retido",
                    "valor_issqn": "50,00",
                    "status": "Ativa",
                }
            ],
        )

        resp_coletor = self.client.get(reverse("coletor_nfse"))
        self.assertEqual(resp_coletor.status_code, 200)
        self.assertContains(resp_coletor, "Apanhador de NFS-e")
        self.assertContains(resp_coletor, "FORNECEDOR SERVICOS LTDA")

        nota = NotaServico.objects.get(empresa=self.empresa, numero="55")
        self.client.post(reverse("alterar_etapa_nfse", args=[nota.pk]), {"etapa": "MES_SEGUINTE"})
        nota.refresh_from_db()
        self.assertEqual(nota.etapa_escrituracao, "MES_SEGUINTE")
        self.assertEqual(nota.competencia_escrituracao, "112026")

        resp_auditoria = self.client.get(reverse("auditoria"))
        self.assertEqual(resp_auditoria.status_code, 200)
        self.assertContains(resp_auditoria, "Etapas do Fechamento Mensal")
        self.assertContains(resp_auditoria, "Apanhador de NFS-e v1.4.5")

        # Desenvolvedor/admin enxerga a opção de importar planilha/XMLs
        self.assertContains(resp_coletor, "Importar Planilha (.xlsx) / XMLs já baixados")

        # Usuário comum (colaborador sem is_staff/is_superuser) NÃO enxerga e NÃO pode acessar a importação manual
        from clientes.models import Colaborador

        usr_comum = User.objects.create_user(username="colaborador_comum", password="123")
        Colaborador.objects.create(
            nome="Analista Comum",
            cargo="Analista",
            usuario=usr_comum,
            ativo=True,
            pode_triar=True,
        )
        self.client.force_login(usr_comum)
        sessao = self.client.session
        sessao["empresa_id"] = self.empresa.pk
        sessao["competencia"] = "102026"
        sessao.save()
        resp_comum = self.client.get(reverse("coletor_nfse"))
        self.assertEqual(resp_comum.status_code, 200)
        self.assertNotContains(resp_comum, "Importar Planilha (.xlsx) / XMLs já baixados")
        resp_post_bloqueado = self.client.post(reverse("importar_relatorio_nfse"), follow=True)
        self.assertContains(resp_post_bloqueado, "restrita ao administrador")

    def test_cadastro_escopo_onboarding_e_pesquisa_equipe(self):
        self.client.force_login(self.user)
        dados_post = {
            "codigo_dominio": "0077",
            "cnpj": "45785442000129",
            "tipo_estabelecimento": "Matriz",
            "status": "Ativo",
            "data_abertura": "18/08/2020",
            "data_inicio_cliente": "03/2025",
            "razao_social": "INDUSTRIA METALURGICA TESTE LTDA",
            "nome_fantasia": "METALURGICA TESTE",
            "inscricao_estadual": "0012345670088",
            "cnae_principal": "2511000",
            "cep": "30140071",
            "uf": "MG",
            "logradouro": "Av Brasil",
            "numero": "500",
            "bairro": "Funcionarios",
            "municipio": "Belo Horizonte",
            "codigo_ibge": "3106200",
            "regime_tributario": "Lucro Real",
            "perfil_estabelecimento": "INDUSTRIA",
            "contribuinte_icms": "on",
            "contribuinte_ipi": "on",
            "resumo_cliente": "Cliente com exportação e regime de corredor de importação.",
            "contatos_json": '[{"Nome": "Carlos Diretor", "Setor": "Sócio/Diretor", "E-mail": "carlos@metal.com.br", "Telefone": "(31) 98888-7777", "WhatsApp": true}]',
            "escopo_fiscal": [
                "Escrituração de Notas Fiscais de Entrada",
                "ICMS Incentivado",
                "Apuração: ICMS-ST",
                "Entrega: SPED Contribuições",
            ],
            "qtd_notas_entrada": "320",
            "tipos_icms_incentivado": ["TTS/INDUSTRIA"],
            "estados_icms_st": ["MG", "SP"],
            "escopo_contabil": ["Processamento e Conciliação Bancária", "Entrega de SPED Contábil (ECD)"],
            "escopo_dp": ["Processamento de Folha de Pagamento Mensal", "Entrega: EFD-Reinf"],
            "escopo_regularizacao": ["Inscrições de Substituto Tributário (ST)"],
            "estados_st": ["RJ", "ES"],
        }
        resp = self.client.post(reverse("nova_empresa"), dados_post)
        self.assertEqual(resp.status_code, 302)

        emp = Empresa.objects.get(codigo_dominio="0077")
        self.assertEqual(emp.data_abertura, "18/08/2020")
        self.assertEqual(emp.data_inicio_cliente, "03/2025")
        self.assertEqual(len(emp.contatos), 1)
        self.assertEqual(emp.contatos[0]["Nome"], "Carlos Diretor")
        self.assertTrue(emp.escopo_fiscal.get("ICMS Incentivado"))
        self.assertEqual(emp.tipos_icms_incentivado, ["TTS/INDUSTRIA"])
        self.assertEqual(emp.estados_icms_st, ["MG", "SP"])
        self.assertTrue(emp.escopo_contabil.get("Processamento e Conciliação Bancária"))
        self.assertTrue(emp.escopo_dp.get("Processamento de Folha de Pagamento Mensal"))
        self.assertEqual(emp.estados_st, ["RJ", "ES"])

        # Pesquisa na lista de clientes pelo item do checklist de escopo ou pelo nome do contato
        resp_busca_escopo = self.client.get(reverse("lista_empresas"), {"q": "TTS/INDUSTRIA"})
        codigos_escopo = [e.codigo_dominio for e in resp_busca_escopo.context["empresas"]]
        self.assertEqual(codigos_escopo, ["0077"])

        resp_busca_contato = self.client.get(reverse("lista_empresas"), {"q": "Carlos Diretor"})
        codigos_contato = [e.codigo_dominio for e in resp_busca_contato.context["empresas"]]
        self.assertEqual(codigos_contato, ["0077"])

        resp_filtro_combo = self.client.get(reverse("lista_empresas"), {"escopo": "Entrega: SPED Contribuições"})
        codigos_combo = [e.codigo_dominio for e in resp_filtro_combo.context["empresas"]]
        self.assertEqual(codigos_combo, ["0077"])
