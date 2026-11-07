import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from clientes.models import DocumentoXML, Empresa

CNPJ = "45785442000390"
CHAVE = "31261045785442000390550010000001231123456789"
NS = "http://www.portalfiscal.inf.br/nfe"


def xml_cancelada() -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="{NS}"><NFe><infNFe Id="NFe{CHAVE}" versao="4.00">
<ide><mod>55</mod><serie>1</serie><nNF>123</nNF><dhEmi>2026-10-15T10:00:00-03:00</dhEmi><tpNF>1</tpNF></ide>
<emit><CNPJ>{CNPJ}</CNPJ></emit><dest><CNPJ>11222333000181</CNPJ></dest>
<total><ICMSTot><vBC>100.00</vBC><vICMS>18.00</vICMS><vBCST>0</vBCST><vST>0</vST><vIPI>0</vIPI><vPIS>1.65</vPIS><vCOFINS>7.60</vCOFINS><vNF>100.00</vNF></ICMSTot></total>
</infNFe></NFe><protNFe><infProt><cStat>101</cStat></infProt></protNFe></nfeProc>""".encode()


def sped_sem_a_nota() -> bytes:
    outra = "31261045785442000390550010000009991123456780"
    linha = (
        f"|C100|1|0||55|00|1|999|{outra}|15102026|15102026|10,00|0|0|0|10,00|9|0|0|0|10,00|1,80|0|0|0|0|0|0|0|"
    )
    return f"|0000|020|0|01102026|31102026|EMPRESA|{CNPJ}|\n{linha}\n".encode("latin-1")


class FluxoSentinelaTest(TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.ajuste = override_settings(PASTA_MAE=self.pasta.name, MEDIA_ROOT=self.pasta.name)
        self.ajuste.enable()
        User.objects.create_user("analista", password="senha-teste-123")
        self.client.login(username="analista", password="senha-teste-123")

    def tearDown(self):
        self.ajuste.disable()
        self.pasta.cleanup()

    def test_cadastro_triagem_e_cruzamento(self):
        self.assertEqual(len(CHAVE), 44)
        resposta = self.client.get("/", follow=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Escolha a empresa ativa")

        gravado = self.client.post("/clientes/nova/", {
            "codigo_dominio": "578",
            "cnpj": "45.785.442/0003-90",
            "tipo_estabelecimento": "Filial",
            "razao_social": "AFFONSO DESCARTAVEIS LTDA",
            "nome_fantasia": "AFFONSO",
            "status": "Ativo",
            "responsavel_apuracao": "Ismael",
            "responsavel_revisao": "Mariana Barbosa",
            "inscricao_estadual": "0047805660026",
            "cnae_principal": "4649499",
            "cep": "37646028",
            "logradouro": "ESTRADA VEREADOR LAMARTINE JOSE DE OLIVEIRA",
            "numero": "1137",
            "bairro": "RODEIO",
            "uf": "mg",
            "municipio": "EXTREMA",
            "codigo_ibge": "3125101",
            "regime_tributario": "Lucro Presumido",
            "regime_pis_cofins": "Cumulativo",
            "perfil_estabelecimento": "GERAL",
            "contribuinte_icms": "on",
        })
        self.assertEqual(gravado.status_code, 302, gravado.content.decode()[:1500] if gravado.status_code != 302 else "")
        empresa = Empresa.objects.get(codigo_dominio="578")
        self.assertEqual(empresa.cnpj, CNPJ)
        self.assertEqual(empresa.uf, "MG")
        pasta_mes = Path(self.pasta.name) / f"0578_AFFONSO_DESCARTAVEIS_LTDA_{CNPJ}" / "2026" / "10_OUTUBRO"
        self.assertTrue((pasta_mes / "02_XML_PROCESSADOS_SENTINELA" / "01_NFE_MOD55" / "03_SAIDAS_PROPRIAS" / "01_AUTORIZADAS").is_dir())

        self.client.post(f"/clientes/ativar/{empresa.pk}/", {"next": "/"})
        self.client.post("/clientes/competencia/", {"mes": "10", "ano": "2026", "next": "/triagem/"})
        arquivo = SimpleUploadedFile("nota.xml", xml_cancelada(), content_type="text/xml")
        leitura = self.client.post("/triagem/importar/", {"arquivos": arquivo})
        self.assertEqual(leitura.status_code, 302)
        nota = DocumentoXML.objects.get(chave_acesso=CHAVE)
        self.assertEqual(nota.status_sefa, "CANCELADA")
        self.assertEqual(nota.tipo_operacao, "SAIDA_PROPRIA")
        self.assertEqual(nota.competencia, "102026")
        self.assertEqual(str(nota.valor_icms), "18.00")
        self.assertTrue(list((pasta_mes / "02_XML_PROCESSADOS_SENTINELA" / "01_NFE_MOD55" / "03_SAIDAS_PROPRIAS" / "02_CANCELADAS").glob("NF000000123_*.xml")))

        tela = self.client.get("/triagem/")
        self.assertContains(tela, CHAVE)
        self.assertContains(tela, "Triagem de XML")

        cruzamento = self.client.post("/auditoria/sped/", {"sped": SimpleUploadedFile("sped.txt", sped_sem_a_nota(), content_type="text/plain")})
        self.assertEqual(cruzamento.status_code, 302)
        nota.refresh_from_db()
        self.assertTrue(nota.possui_divergencia)
        self.assertIn("AUSENTE no SPED", nota.mensagem_divergencia)
        auditoria = self.client.get("/auditoria/")
        self.assertContains(auditoria, "Lançar nota faltante na Domínio para evitar omissão.")
        painel = self.client.get("/", follow=True)
        self.assertContains(painel, "Débitos de ICMS")

    def test_apuracao_bloqueada_sem_cnae(self):
        empresa = Empresa.objects.create(
            codigo_dominio="1",
            cnpj="11222333000181",
            razao_social="SEM CNAE",
            responsavel_apuracao="Ismael",
            responsavel_revisao="Mariana Barbosa",
            regime_pis_cofins="Cumulativo",
            cnae_principal="",
            cep="01310930",
            logradouro="PAULISTA",
            numero="1",
            bairro="BELA VISTA",
            uf="SP",
            municipio="SAO PAULO",
            codigo_ibge="3550308",
            regime_tributario="Lucro Presumido",
        )
        self.client.post(f"/clientes/ativar/{empresa.pk}/", {"next": "/triagem/"})
        resposta = self.client.post("/triagem/importar/", {"arquivos": SimpleUploadedFile("nota.xml", xml_cancelada())}, follow=True)
        self.assertContains(resposta, "APURAÇÃO BLOQUEADA")
        self.assertEqual(DocumentoXML.objects.count(), 0)

    def test_regime_especial_pede_pdf_do_pta(self):
        base = {
            "codigo_dominio": "579",
            "cnpj": "11222333000181",
            "tipo_estabelecimento": "Matriz",
            "razao_social": "CLIENTE REGIME ESPECIAL",
            "status": "Ativo",
            "responsavel_apuracao": "Ismael",
            "responsavel_revisao": "Mariana Barbosa",
            "inscricao_estadual": "ISENTO",
            "cnae_principal": "4649499",
            "cep": "01310930",
            "logradouro": "PAULISTA",
            "numero": "1",
            "bairro": "BELA VISTA",
            "uf": "SP",
            "municipio": "SAO PAULO",
            "codigo_ibge": "3550308",
            "regime_tributario": "Lucro Real",
            "regime_pis_cofins": "Cumulativo",
            "perfil_estabelecimento": "REGIME_ESPECIAL_SP",
            "contribuinte_icms": "on",
        }
        sem_pdf = self.client.post("/clientes/nova/", base)
        self.assertEqual(sem_pdf.status_code, 200)
        self.assertContains(sem_pdf, "Anexe o PDF do PTA ou do regime especial.")
        self.assertContains(sem_pdf, "Regime Especial Concedido por SP")
        com_pdf = self.client.post("/clientes/nova/", {
            **base,
            "codigo_dominio": "580",
            "cnpj": "45785442000390",
            "documento_regime_especial": SimpleUploadedFile("pta.pdf", b"%PDF-1.4", content_type="application/pdf"),
        })
        self.assertEqual(com_pdf.status_code, 302, com_pdf.content.decode()[:1500] if com_pdf.status_code != 302 else "")
        empresa = Empresa.objects.get(codigo_dominio="580")
        self.assertTrue(empresa.documento_regime_especial.name.endswith(".pdf"))
        self.assertTrue(empresa.possui_regime_especial)
        self.assertEqual(empresa.regime_pis_cofins, "Não-Cumulativo")
