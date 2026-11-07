# Manual técnico de arquitetura e engenharia — Projeto Sentinela

Parte 1. Este documento é a especificação do aplicativo Django em `sentinela_app`.

## 1. Diretrizes gerais e identidade visual

O Sentinela usa Python e Django, com banco relacional e o admin nativo preparado para um login interno. A integração com Azure ou SharePoint fica para uma etapa seguinte; o acesso atual é o `User` do Django.

A interface é a de um SaaS: cartões com sombra suave, transição curta e cantos de 18px em campos, botões e cartões. Cada módulo tem uma cor própria.

- Clientes: magenta `#d81b60`
- Triagem de XML: violeta `#6d28d9`
- Auditoria de ICMS: azul `#1d4ed8`
- PIS/COFINS: verde `#047857`
- Divergências: vermelho `#dc2626`

## 2. Sessão por empresa e competência

Depois do login, o analista escolhe a empresa ativa pelo código Domínio, pelo nome ou pelo CNPJ, e a competência no formato MMAAAA. Outubro de 2026 fica gravado como `102026`.

A triagem e a auditoria só leem essa empresa e essa competência. O cadastro de clientes continua enxergando o escritório inteiro, porque matriz e filial são empresas diferentes e o analista precisa criar o próximo cliente sem perder a lista.

Trocar a empresa ou o mês não apaga o que já foi lido. A tela passa a mostrar o outro contexto.

## 3. Banco de dados

As tabelas estão em `clientes/models.py`, com os campos definidos para `Empresa` e `DocumentoXML`.

`Empresa` guarda o código Domínio de até 4 dígitos, o CNPJ com 14 dígitos, o tipo matriz ou filial, o endereço, o código IBGE, o regime e o responsável. `DocumentoXML` guarda a chave de 44 dígitos, a competência, o modelo, a operação, o status na SEFAZ, os totais de ICMS, ST, IPI, PIS e COFINS, e a divergência encontrada no cruzamento.

A chave é única no banco. A mesma nota não entra em duas empresas.

## 4. Módulo de clientes

O formulário segue os blocos de identificação, inscrições, localização e parâmetros tributários. O botão Buscar CNPJ consulta a BrasilAPI e preenche razão, fantasia, endereço, CNAE, município e IBGE. A inscrição estadual e o regime continuam com o analista.

Ao gravar, o sistema cria a árvore de pastas em `media/empresas/{codigo}_{cnpj}/`.

## 5. Consulta de CNPJ e IBGE

`clientes/services.py` consulta a BrasilAPI e, em seguida, a lista oficial de municípios do IBGE para o código de 7 dígitos. O nome é comparado sem acento. Se o IBGE não devolver o município, vale o `codigo_municipio_ibge` da BrasilAPI.

Situação cadastral `ATIVA` preenche o status Ativo. Optante pelo Simples preenche Simples Nacional; os demais cadastros públicos entram como Lucro Presumido para o analista conferir.

## 6. Arquivos e pasta da empresa

XML e SPED entram pelo navegador. O lote é lido em pasta temporária e o resultado fiscal fica no banco. A árvore do cliente, os XML triados e os relatórios nascem em `SHAREPOINT_ROOT_DIR`, na pasta Sentinela do SharePoint da Nascel. Ano, mês e as pastas do Garimpeiro são criados nesse caminho quando ainda não existem.

## 7. Trava de apuração

Triagem, cruzamento e apuração só rodam com código Domínio, CNPJ, código IBGE, CNAE e regime preenchidos. Se faltar algum, a tela mostra `APURAÇÃO BLOQUEADA` e o motor não executa.

## 8. Lote de XML

O analista solta um ZIP, vários XML ou uma pasta. O motor descompacta ZIP dentro de ZIP, ignora PDF e imagem, e grava a nota na pasta do mês da competência ativa.

- Emitente da empresa e `tpNF` 1: saída própria
- Emitente da empresa e `tpNF` 0: entrada de emissão própria
- Destinatário da empresa: entrada de terceiros
- Nenhum dos dois: pasta 99, sem linha no banco

Cancelada: `cStat` 101, 151, 155 ou evento 110111 aceito. Autorizada: 100 ou 150. Denegada: 110, 301, 302 ou 303. O arquivo final se chama `NF` + número com 9 dígitos + chave. Os totais saem de `ICMSTot` e a linha entra em `DocumentoXML` na competência da sessão.

## 9. SPED e apuração

O cruzamento lê o C100 em latin-1. O CNPJ e o mês do registro 0000 precisam bater com a sessão. O painel marca nota que está no XML e não está no SPED, nota que está no SPED e não tem XML, e valor total com diferença acima de R$ 0,05.

A apuração das notas autorizadas de modelo 55 soma o ICMS das saídas como débito, o ICMS das entradas como crédito e o ICMS-ST das saídas. Saída com base e ICMS zerado vira alerta de emissão. PIS e COFINS aparecem pela soma do XML. O DIFAL por UF ainda não calcula partilha.

## 10. Como subir

Na pasta `sentinela_app`, no computador:

```text
python manage.py migrate
python manage.py runserver
```

Na nuvem, o processo sobe com o `Procfile`. As variáveis são `SECRET_KEY`, `DEBUG=False`, `DATABASE_URL`, `ALLOWED_HOSTS` e, se o endereço HTTPS não for detectado, `CSRF_TRUSTED_ORIGINS`. Sem `DATABASE_URL`, o banco local é o SQLite do projeto.
