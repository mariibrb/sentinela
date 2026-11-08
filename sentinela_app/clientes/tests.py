import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from clientes.models import Colaborador, DocumentoXML, Empresa, LogAlteracao
from clientes.services import ServicoConsultaCNPJ

CNPJ = "45785442000390"
CHAVE = "31261045785442000390550010000001231123456789"
NS = "http://www.portalfiscal.inf.br/nfe"


def xml_cancelada() -> bytes:
    return xml_numero(123, CHAVE)


def xml_numero(numero: int, chave: str | None = None) -> bytes:
    chave = chave or (CHAVE[:25] + f"{numero:09d}" + CHAVE[34:])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="{NS}"><NFe><infNFe Id="NFe{chave}" versao="4.00">
<ide><mod>55</mod><serie>1</serie><nNF>{numero}</nNF><dhEmi>2026-10-15T10:00:00-03:00</dhEmi><tpNF>1</tpNF></ide>
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
        self.usuario = User.objects.create_user("analista", password="senha-teste-123")
        self.apuracao = Colaborador.objects.create(nome="Ismael", cargo="Analista", email="contato@nascel.com.br")
        self.revisao = Colaborador.objects.create(nome="Mariana Barbosa", cargo="Revisão", email="mariana.barbosa@nascel.com.br")
        Colaborador.objects.create(
            nome="Operador teste",
            cargo="Analista",
            usuario=self.usuario,
            pode_cadastrar_cliente=True,
            pode_editar_cliente=True,
            pode_inativar_cliente=True,
            pode_excluir_cliente=True,
            pode_triar=True,
            pode_auditar=True,
        )
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
            "responsavel_apuracao": self.apuracao.pk,
            "responsavel_revisao": self.revisao.pk,
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
            responsavel_apuracao=self.apuracao,
            responsavel_revisao=self.revisao,
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
            "responsavel_apuracao": self.apuracao.pk,
            "responsavel_revisao": self.revisao.pk,
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

    def test_acoes_respeitam_a_permissao_do_colaborador(self):
        empresa = Empresa.objects.create(
            codigo_dominio="2",
            cnpj="11222333000181",
            razao_social="CLIENTE PERMISSAO",
            responsavel_apuracao=self.apuracao,
            responsavel_revisao=self.revisao,
            cnae_principal="4772500",
            cep="01310930",
            logradouro="PAULISTA",
            numero="1",
            bairro="BELA VISTA",
            uf="SP",
            municipio="SAO PAULO",
            codigo_ibge="3550308",
            regime_tributario="Lucro Presumido",
        )
        sem = User.objects.create_user("sem", password="senha-teste-123")
        navegador = self.client.__class__()
        navegador.login(username="sem", password="senha-teste-123")
        bloqueado = navegador.post(f"/clientes/{empresa.pk}/excluir/", follow=True)
        self.assertContains(bloqueado, "não tem permissão")
        self.assertTrue(Empresa.objects.filter(pk=empresa.pk).exists())
        lista = self.client.get("/clientes/")
        self.assertContains(lista, "Inativar")
        self.assertContains(lista, "Excluir")
        self.client.post(f"/clientes/{empresa.pk}/situacao/")
        empresa.refresh_from_db()
        self.assertEqual(empresa.status, "Inativo")
        travado = navegador.post(f"/colaboradores/{self.apuracao.pk}/excluir/", follow=True)
        self.assertContains(travado, "restrita ao administrador")

    def test_colaborador_nao_entra_no_admin(self):
        bloqueado = self.client.get("/colaboradores/", follow=True)
        self.assertContains(bloqueado, "Essa área é restrita ao administrador.")
        admin = self.client.get("/admin/", follow=True)
        self.assertContains(admin, "O painel administrativo é restrito ao administrador.")
        User.objects.create_user("dev", password="senha-admin-123", is_staff=True)
        self.client.login(username="dev", password="senha-admin-123")
        liberado = self.client.get("/colaboradores/")
        self.assertEqual(liberado.status_code, 200)
        self.assertContains(liberado, "Ismael")

    def test_cadastro_mostra_situacao_e_acoes_reais(self):
        pessoa = Colaborador.objects.get(usuario=self.usuario)
        self.assertTrue(LogAlteracao.objects.filter(usuario=self.usuario, acao="ENTRADA").exists())
        User.objects.create_user("dev2", password="senha-admin-123", is_staff=True)
        self.client.login(username="dev2", password="senha-admin-123")
        tela = self.client.get(f"/colaboradores/{pessoa.pk}/")
        self.assertContains(tela, "Ativo")
        self.assertContains(tela, "Inativo")
        self.assertContains(tela, "Entrada")
        self.assertContains(tela, "Entrou no Sentinela")
        self.client.logout()
        self.assertTrue(LogAlteracao.objects.filter(usuario__username="dev2", acao="SAIDA").exists())
        pessoa.ativo = False
        pessoa.save(update_fields=["ativo"])
        recusa = self.client.post("/entrar/", {"username": "analista", "password": "senha-teste-123"})
        self.assertContains(recusa, "Este colaborador está inativo.")

    def test_lote_aceita_mais_de_cem_arquivos(self):
        from django.conf import settings
        self.assertGreaterEqual(settings.DATA_UPLOAD_MAX_NUMBER_FILES, 100000)
        self.assertGreaterEqual(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, 200000)

    def _empresa_pronta(self, codigo="582"):
        empresa = Empresa.objects.create(
            codigo_dominio=codigo,
            cnpj=CNPJ,
            razao_social="AFFONSO DESCARTAVEIS LTDA",
            responsavel_apuracao=self.apuracao,
            responsavel_revisao=self.revisao,
            regime_tributario="Lucro Presumido",
            regime_pis_cofins="Cumulativo",
            cnae_principal="4649499",
            cep="37646028",
            logradouro="ESTRADA",
            numero="1137",
            bairro="RODEIO",
            uf="MG",
            municipio="EXTREMA",
            codigo_ibge="3125101",
            inscricao_estadual="0047805660026",
        )
        self.client.post(f"/clientes/ativar/{empresa.pk}/", {"next": "/triagem/"})
        self.client.post("/clientes/competencia/", {"mes": "10", "ano": "2026", "next": "/triagem/"})
        return empresa

    def test_leitura_em_partes_grava_e_responde_json(self):
        self._empresa_pronta()
        resposta = self.client.post("/triagem/importar/", {
            "resposta": "json",
            "fim": "1",
            "arquivos": SimpleUploadedFile("nota.xml", xml_cancelada(), content_type="text/xml"),
        })
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["gravados"], 1)
        self.assertFalse(corpo["continua"])
        self.assertEqual(DocumentoXML.objects.filter(competencia="102026").count(), 1)
        tela = self.client.get("/triagem/")
        self.assertContains(tela, "partes de 30 notas")

    def test_zip_grande_anda_em_partes(self):
        from triagem_xml.fatias import avancar

        empresa = self._empresa_pronta("583")
        interno = BytesIO()
        with zipfile.ZipFile(interno, "w") as pacote:
            pacote.writestr("dentro.xml", xml_numero(204))
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as pacote:
            for numero in (201, 202, 203):
                pacote.writestr(f"n{numero}.xml", xml_numero(numero))
            pacote.writestr("../fora.xml", xml_numero(209))
            pacote.writestr("nivel.zip", interno.getvalue())
        arquivo = SimpleUploadedFile("lote.zip", buffer.getvalue(), content_type="application/zip")
        token = "a1b2c3d4e5f60789"
        continua = True
        primeira = True
        rodadas = 0
        while continua and rodadas < 8:
            resultado = avancar(
                empresa,
                "102026",
                self.usuario.pk,
                token,
                [arquivo] if primeira else [],
                por_vez=1,
            )
            primeira = False
            continua = resultado["continua"]
            rodadas += 1
        self.assertFalse(continua)
        self.assertLessEqual(rodadas, 6)
        self.assertEqual(DocumentoXML.objects.count(), 4)
        self.assertFalse(DocumentoXML.objects.filter(numero_nota="209").exists())

    def test_salva_sem_responsavel_e_bloqueia_o_uso(self):
        tela = self.client.get("/clientes/nova/")
        self.assertNotContains(tela, 'value="ISENTO"')
        self.assertContains(tela, "Selecione o regime")
        self.assertNotContains(tela, "selected>Lucro Presumido")
        self.assertNotContains(tela, "selected>Simples Nacional")
        self.assertContains(tela, "Inscrição de substituto tributário")
        self.assertContains(tela, "id_ie_st_SP")
        sem_regime = self.client.post("/clientes/nova/", {
            "codigo_dominio": "581",
            "cnpj": "45785442000390",
            "tipo_estabelecimento": "Filial",
            "razao_social": "AFFONSO DESCARTAVEIS LTDA",
            "status": "Ativo",
            "inscricao_estadual": "0047805660026",
            "cnae_principal": "4649499",
            "cep": "37646028",
            "logradouro": "ESTRADA",
            "numero": "1137",
            "bairro": "RODEIO",
            "uf": "MG",
            "municipio": "EXTREMA",
            "codigo_ibge": "3125101",
            "regime_tributario": "",
            "perfil_estabelecimento": "GERAL",
            "contribuinte_icms": "on",
        })
        self.assertEqual(sem_regime.status_code, 200)
        self.assertContains(sem_regime, "Selecione o regime tributário.")
        self.assertFalse(Empresa.objects.filter(codigo_dominio="581").exists())
        gravado = self.client.post("/clientes/nova/", {
            "codigo_dominio": "581",
            "cnpj": "45785442000390",
            "tipo_estabelecimento": "Filial",
            "razao_social": "AFFONSO DESCARTAVEIS LTDA",
            "status": "Ativo",
            "inscricao_estadual": "0047805660026",
            "ie_st_BA": "248025829",
            "ie_st_CE": "073307130",
            "ie_st_SP": "813035560114",
            "cnae_principal": "4649499",
            "cep": "37646028",
            "logradouro": "ESTRADA",
            "numero": "1137",
            "bairro": "RODEIO",
            "uf": "MG",
            "municipio": "EXTREMA",
            "codigo_ibge": "3125101",
            "regime_tributario": "Lucro Presumido",
            "perfil_estabelecimento": "GERAL",
            "contribuinte_icms": "on",
        })
        self.assertEqual(gravado.status_code, 302, gravado.content.decode()[:1500] if gravado.status_code != 302 else "")
        empresa = Empresa.objects.get(codigo_dominio="581")
        self.assertIsNone(empresa.responsavel_apuracao_id)
        self.assertIsNone(empresa.responsavel_revisao_id)
        self.assertEqual(
            set(empresa.substituicoes.values_list("uf", "numero")),
            {("BA", "248025829"), ("CE", "073307130"), ("SP", "813035560114")},
        )
        self.client.post(f"/clientes/ativar/{empresa.pk}/", {"next": "/triagem/"})
        uso = self.client.post("/triagem/importar/", {"arquivos": SimpleUploadedFile("nota.xml", xml_cancelada())}, follow=True)
        self.assertContains(uso, "APURAÇÃO BLOQUEADA")
        self.assertContains(uso, "Responsável pela apuração")
        self.assertEqual(DocumentoXML.objects.count(), 0)
        inscricoes = [
            {"inscricao_estadual": "248025829", "ativo": True, "estado": {"sigla": "BA"}},
            {"inscricao_estadual": "073307130", "ativo": True, "estado": {"sigla": "CE"}},
            {"inscricao_estadual": "0047805660026", "ativo": True, "estado": {"sigla": "MG"}},
            {"inscricao_estadual": "813035560114", "ativo": True, "estado": {"sigla": "SP"}},
            {"inscricao_estadual": "000", "ativo": False, "estado": {"sigla": "MG"}},
        ]
        self.assertEqual(ServicoConsultaCNPJ.escolher_inscricao_estadual(inscricoes, "MG"), "0047805660026")
        self.assertEqual(ServicoConsultaCNPJ.escolher_inscricao_estadual(inscricoes, "RJ"), "")
        self.assertEqual(
            ServicoConsultaCNPJ.substituicoes_das_inscricoes(inscricoes, "MG"),
            {"BA": "248025829", "CE": "073307130", "SP": "813035560114"},
        )


    def test_tela_do_garimpeiro_lista_o_que_foi_lido(self):
        empresa = Empresa.objects.create(
            codigo_dominio="580",
            cnpj=CNPJ,
            razao_social="AFFONSO DESCARTAVEIS LTDA",
            responsavel_apuracao=self.apuracao,
            responsavel_revisao=self.revisao,
            regime_pis_cofins="Cumulativo",
            cnae_principal="4649499",
            cep="37646028",
            logradouro="ESTRADA",
            numero="1137",
            bairro="RODEIO",
            uf="MG",
            municipio="EXTREMA",
            codigo_ibge="3125101",
            regime_tributario="Lucro Presumido",
            inscricao_estadual="0047805660026",
        )
        self.client.post(f"/clientes/ativar/{empresa.pk}/", {"next": "/triagem/garimpeiro/"})
        self.client.post("/clientes/competencia/", {"mes": "10", "ano": "2026", "next": "/triagem/garimpeiro/"})
        from datetime import date
        DocumentoXML.objects.create(
            empresa=empresa, competencia="102026", modelo="55", chave_acesso=CHAVE,
            numero_nota="1", serie="1", data_emissao=date(2026, 10, 15),
            tipo_operacao="SAIDA_PROPRIA", status_sefa="AUTORIZADA",
        )
        chave_cancelada = CHAVE[:-1] + "0"
        DocumentoXML.objects.create(
            empresa=empresa, competencia="102026", modelo="55", chave_acesso=chave_cancelada,
            numero_nota="3", serie="1", data_emissao=date(2026, 10, 16),
            tipo_operacao="SAIDA_PROPRIA", status_sefa="CANCELADA",
        )
        tela = self.client.get("/triagem/garimpeiro/")
        self.assertContains(tela, "O que foi lido")
        self.assertContains(tela, CHAVE)
        self.assertContains(tela, "Buracos")
        self.assertContains(tela, "Canceladas")
        self.assertContains(tela, "Extrair planilha")
        self.assertContains(tela, "Extrair XML")
        canceladas = self.client.get("/triagem/garimpeiro/?aba=canceladas")
        self.assertContains(canceladas, chave_cancelada)
        buracos = self.client.get("/triagem/garimpeiro/?aba=buracos")
        self.assertContains(buracos, ">2<")
        planilha = self.client.post("/triagem/garimpeiro/extrair/", {"aba": "lidos", "tipo": "planilha"})
        self.assertEqual(planilha.status_code, 200)
        self.assertIn("text/csv", planilha["Content-Type"])
        painel = self.client.get("/", follow=True)
        self.assertContains(painel, "Garimpeiro")


class MatrioscaZipTest(SimpleTestCase):
    def test_abre_oito_zips_dentro_de_pastas(self):
        from triagem_xml.garimpeiro_motor import MotorGarimpeiroSentinela

        conteudo = b"<nfe>dentro</nfe>"
        nome = "nota.xml"
        for nivel in range(8):
            pacote = BytesIO()
            with zipfile.ZipFile(pacote, "w") as arquivo:
                arquivo.writestr(f"pasta_{nivel}/{nome}", conteudo)
            conteudo = pacote.getvalue()
            nome = f"nivel_{nivel}.zip"
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            externo = raiz / "lote.zip"
            externo.write_bytes(conteudo)
            destino = raiz / "saida"
            empresa = type("Empresa", (), {"cnpj": CNPJ, "id": 1})()
            motor = MotorGarimpeiroSentinela(empresa, "112026", str(raiz / "mes"))
            motor.extrair_zip_recursivo(externo, destino)
            encontrados = list(destino.rglob("nota.xml"))
            self.assertEqual(len(encontrados), 1)
            self.assertEqual(encontrados[0].read_bytes(), b"<nfe>dentro</nfe>")


class GarimpeiroOriginalTest(SimpleTestCase):
    def test_motor_original_grava_arquivo_fora_da_pasta_do_escritorio(self):
        from garimpeiro_legado.ponte import pasta_resultados, processar_garimpo

        with tempfile.TemporaryDirectory() as pasta:
            os_environ = __import__("os").environ
            os_environ["GARIMPEIRO_DATA_ROOT"] = pasta
            nota = Path(pasta) / "nota.xml"
            nota.write_bytes(xml_cancelada())
            ok, mensagem, quantidade = processar_garimpo(CNPJ, [nota])
            raiz = pasta_resultados()
            self.assertTrue((raiz / "ArquivoFiscal").is_dir())
        self.assertNotIn("escritafiscal", str(raiz).lower())
        self.assertNotIn("onedrive", str(raiz).lower())
        self.assertTrue(ok, mensagem)
        self.assertGreaterEqual(quantidade, 1)
        self.assertIn("documento", mensagem.lower())

    def test_garimpeiro_fica_dentro_da_pasta_do_sentinela(self):
        from django.test import override_settings

        from garimpeiro_legado.ponte import pasta_do_sentinela

        with tempfile.TemporaryDirectory() as pasta:
            with override_settings(PASTA_MAE=pasta):
                destino = pasta_do_sentinela()
            self.assertEqual(destino, Path(pasta) / "Garimpeiro")
            self.assertTrue(destino.is_dir())
