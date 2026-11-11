import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Colaborador, Empresa
from fechamento.models import ChecklistFechamento


class FechamentoSentinelaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin_fc", "a@b.com", "123")
        self.revisor = User.objects.create_user("revisor_fc", password="senha-revisor")
        self.empresa = Empresa.objects.create(
            codigo_dominio="0145",
            cnpj="12345678000195",
            razao_social="OC ALIMENTOS LTDA",
            status="Ativo",
            cnae_principal="1091102",
            cep="12926210",
            logradouro="AV TESTE",
            numero="100",
            bairro="CENTRO",
            uf="SP",
            municipio="Braganca Paulista",
            codigo_ibge="3507605",
            regime_tributario="Simples Nacional",
            regime_pis_cofins="Simples Nacional",
            inscricao_estadual="ISENTO",
        )
        Colaborador.objects.create(
            nome="Admin",
            cargo="Admin",
            usuario=self.user,
            ativo=True,
            pode_triar=True,
            pode_auditar=True,
        )
        Colaborador.objects.create(
            nome="Revisor Teste",
            cargo="Revisor",
            usuario=self.revisor,
            ativo=True,
            pode_auditar=True,
        )

    def test_painel_e_apis_salvam_no_banco(self):
        self.client.force_login(self.user)
        sess = self.client.session
        sess["empresa_id"] = self.empresa.pk
        sess["competencia"] = "112026"
        sess.save()

        resp = self.client.get(reverse("fechamento"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fechamento operacional no Sentinela")
        self.assertContains(resp, "Aprovar revisão")

        empresas = self.client.get(reverse("fechamento_api_empresas"))
        self.assertTrue(empresas.json()["ok"])

        revisores = self.client.get(reverse("fechamento_api_revisores"))
        self.assertTrue(revisores.json()["ok"])
        ids = [r["id"] for r in revisores.json()["revisores"]]
        self.assertIn(self.revisor.pk, ids)

        payload = {
            "empresa_id": self.empresa.pk,
            "competencia": "112026",
            "regime": "simples",
            "checklist_tipo": "importacao",
            "status": "aguardando_revisao",
            "flags": {"sn_01": "ok"},
            "obs_logs": {},
            "anexos": {},
            "operador": "Mari",
            "revisor_usuario_id": self.revisor.pk,
            "ie_st": "nao_aplica",
        }
        salvar = self.client.post(
            reverse("fechamento_api_salvar"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertTrue(salvar.json()["ok"])
        reg = ChecklistFechamento.objects.get(empresa=self.empresa, competencia="112026")
        self.assertEqual(reg.status, "aguardando_revisao")

        bloqueado = self.client.post(
            reverse("fechamento_api_salvar"),
            data=json.dumps({**payload, "status": "aprovado"}),
            content_type="application/json",
        )
        self.assertFalse(bloqueado.json()["ok"])

        senha_errada = self.client.post(
            reverse("fechamento_api_aprovar"),
            data=json.dumps(
                {
                    "empresa_id": self.empresa.pk,
                    "competencia": "112026",
                    "revisor_usuario_id": self.revisor.pk,
                    "senha_revisor": "errada",
                }
            ),
            content_type="application/json",
        )
        self.assertFalse(senha_errada.json()["ok"])

        ok = self.client.post(
            reverse("fechamento_api_aprovar"),
            data=json.dumps(
                {
                    "empresa_id": self.empresa.pk,
                    "competencia": "112026",
                    "revisor_usuario_id": self.revisor.pk,
                    "senha_revisor": "senha-revisor",
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(ok.json()["ok"])
        reg.refresh_from_db()
        self.assertEqual(reg.status, "aprovado")
        self.assertEqual(reg.revisor_usuario_id, self.revisor.pk)

    def test_extrair_definicoes_do_html_embutido(self):
        from pathlib import Path

        snap = Path(__file__).resolve().parent / "data" / "checklists.json"
        self.assertTrue(snap.is_file())
        data = json.loads(snap.read_text(encoding="utf-8"))
        self.assertIn("simples", data["regimesData"])
        self.assertGreaterEqual(len(data["regimesData"]["simples"]["items"]), 10)
