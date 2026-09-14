#!/usr/bin/env python3
"""
core.py

Motor de leitura/processamento de cronogramas exportados do MS Project (.xml)
e geração do painel (HTML), do CSV completo e do relatório PDF.

É o mesmo mecanismo do "Programa Térmicas" (atualizar_cronograma.py) — nada
aqui depende do tipo de projeto (usina, duto, ETC, etc.): tudo é agrupado
genericamente por "AS" (a tarefa-resumo no OutlineLevel escolhido), então o
mesmo código serve para qualquer portfólio de projetos, não só térmicas.

Uso via linha de comando (opcional, útil para testes/rotina manual):

    python3 core.py novo_cronograma.xml [outro_cronograma.xml ...]

O script:
  1. Lê o(s) XML(s) novo(s) (aceita mais de um arquivo, tratando todos os
     AS de todos os arquivos como um único portfólio).
  2. Compara o progresso e o número de atrasadas de cada AS com o último
     resumo salvo (historico/_ultimo_resumo.csv).
  3. Gera:
       - cronograma.csv     (dados completos, atuais)
       - dashboard.html      (painel por AS, tema claro, com % e atrasadas anteriores)
       - relatorio_status.md (status atual por AS, com comparação)
  4. Arquiva o(s) XML(s) desta execução em historico/AAAA-MM-DD/ para manter histórico.

Na primeira vez que voce rodar, nao havera "resumo anterior" para comparar --
o script vai avisar isso e so gerar o estado atual normalmente.

Estrutura de pastas (criada automaticamente na primeira execucao):
    ./historico/                <- XMLs antigos, um por data de execucao
    ./historico/_ultimo_resumo.csv
    ./dashboard.html, cronograma.csv, relatorio_status.md
"""

import argparse
import csv
import os
import shutil
from datetime import datetime, date
import xml.etree.ElementTree as ET

NS = {"p": "http://schemas.microsoft.com/project"}

HIST_DIR = "historico"
OVERRIDE_PATH = "avanco_manual.csv"


def carregar_overrides(path):
    """Le avanco_manual.csv (as,percentual) se existir. Retorna dict {trecho_do_nome: percentual}."""
    if not os.path.exists(path):
        return {}
    overrides = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            chave = row["as"].strip().upper()
            overrides[chave] = float(row["percentual"])
    return overrides


def buscar_override(nome_grupo, overrides):
    nome_upper = nome_grupo.upper()
    for chave, valor in overrides.items():
        if chave in nome_upper:
            return valor
    return None


# ---------- Leitura do XML ----------

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def extrair_data_referencia(xml_path):
    """Le a data que o proprio Microsoft Project usa como 'data atual' no momento em
    que o arquivo foi gerado/salvo pela projetista (campo CurrentDate; se ausente,
    cai para LastSaved). Essa e' a data de emissao do cronograma."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for campo in ("CurrentDate", "LastSaved"):
        el = root.find(f"p:{campo}", NS)
        if el is not None and el.text:
            try:
                return datetime.fromisoformat(el.text).date()
            except ValueError:
                continue
    return date.today()


def load_tasks(xml_path, nivel_as):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    tasks_el = root.find("p:Tasks", NS)

    tasks = []
    grupo_atual = None
    macro_atual = None
    for t in tasks_el.findall("p:Task", NS):
        is_null = t.find("p:IsNull", NS)
        if is_null is not None and is_null.text == "1":
            continue

        def g(tag, default=""):
            el = t.find(f"p:{tag}", NS)
            return el.text if el is not None and el.text is not None else default

        nivel = int(g("OutlineLevel", "0") or 0)
        nome = g("Name").strip()

        if nivel == nivel_as:
            grupo_atual = nome
            macro_atual = None
        elif nivel == nivel_as + 1:
            macro_atual = nome

        tasks.append(
            {
                "uid": g("UID"),
                "nome": nome,
                "nivel": nivel,
                "resumo": g("Summary", "0") == "1",
                "critico": g("Critical", "0") == "1",
                "inicio": parse_dt(g("Start")),
                "termino": parse_dt(g("Finish")),
                "pct_concluido": int(g("PercentComplete", "0") or 0),
                "grupo": grupo_atual if nivel >= nivel_as else None,
                "macro": macro_atual if nivel > nivel_as else None,
            }
        )
    return tasks


def load_tasks_multi(xml_paths, nivel_as):
    """Le varios arquivos XML e junta as tarefas de todos em um unico portfolio.

    Cada arquivo mantem seu proprio agrupamento por AS (calculado antes de
    juntar), então nao ha risco de um AS de um arquivo "vazar" para o de
    outro. Útil quando os projetos ainda chegam em cronogramas separados por
    programa/contratada; se um dia vier tudo num XML só, basta passar uma
    lista com um único caminho.
    """
    tasks = []
    for path in xml_paths:
        tasks.extend(load_tasks(path, nivel_as))
    return tasks


def classificar_status(task, hoje):
    if task["resumo"]:
        return "fase"
    if task["pct_concluido"] >= 100:
        return "concluida"
    if task["termino"] and task["termino"].date() < hoje:
        return "atraso_prazo"
    if task["inicio"] and task["inicio"].date() <= hoje and task["pct_concluido"] > 0:
        return "andamento"
    if task["inicio"] and task["inicio"].date() <= hoje and task["pct_concluido"] == 0:
        return "atraso_inicio"
    return "nao_iniciada"


ROTULOS = {
    "fase": "Fase/Resumo",
    "concluida": "Concluída",
    "atraso_prazo": "Atrasada (prazo vencido)",
    "atraso_inicio": "Atrasada (não iniciada)",
    "andamento": "Em andamento",
    "nao_iniciada": "Não iniciada",
}

CORES = {
    "concluida": "#12946b",
    "andamento": "#1c76c9",
    "atraso_prazo": "#d0342c",
    "atraso_inicio": "#c2650f",
    "nao_iniciada": "#98a2b3",
    "fase": "#667085",
}

RISCO_COR = {"ok": "#12946b", "atencao": "#c2650f", "alto": "#d0342c"}
RISCO_LABEL = {"ok": "No prazo", "atencao": "Atenção", "alto": "Risco alto"}



# ---------- Resumo anterior (para comparar % e atrasadas entre envios) ----------

RESUMO_PATH = os.path.join(HIST_DIR, "_ultimo_resumo.csv")


def carregar_resumo_anterior(path):
    if not os.path.exists(path):
        return None
    resumo = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            resumo[row["grupo"]] = {
                "pct_medio": float(row["pct_medio"]),
                "n_atrasadas": int(row["n_atrasadas"]),
            }
    return resumo


def salvar_resumo_atual(stats_por_grupo, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["grupo", "pct_medio", "n_atrasadas"])
        for grupo, st in stats_por_grupo.items():
            writer.writerow([grupo, st["pct_medio"], st["n_atrasadas"]])


# ---------- Agrupamento e stats (iguais ao dashboard) ----------

def agrupar_por_as(tasks, nivel_as):
    grupos, ordem = {}, []
    for t in tasks:
        if t["nivel"] < nivel_as or t["grupo"] is None:
            continue
        if t["grupo"] not in grupos:
            grupos[t["grupo"]] = []
            ordem.append(t["grupo"])
        grupos[t["grupo"]].append(t)
    return [(nome, grupos[nome]) for nome in ordem]


def stats_grupo(tasks_grupo, hoje):
    exec_tasks = [t for t in tasks_grupo if not t["resumo"]]
    contagem = {"atraso_prazo": 0, "atraso_inicio": 0, "andamento": 0, "concluida": 0, "nao_iniciada": 0}
    for t in exec_tasks:
        s = classificar_status(t, hoje)
        if s in contagem:
            contagem[s] += 1
    total = len(exec_tasks) or 1
    # Usa o % que o proprio Microsoft Project calculou para a AS (a tarefa-mae do grupo),
    # em vez de recalcular uma media por conta propria -- assim bate exatamente com o
    # que a projetista ve quando abre o arquivo no Project.
    as_task = min(tasks_grupo, key=lambda t: t["nivel"])
    pct_medio = round(as_task["pct_concluido"], 1)
    n_atrasadas = contagem["atraso_prazo"] + contagem["atraso_inicio"]
    tem_critico_atrasado = any(t["critico"] and classificar_status(t, hoje) in ("atraso_prazo", "atraso_inicio") for t in exec_tasks)
    if n_atrasadas == 0:
        risco = "ok"
    elif tem_critico_atrasado or n_atrasadas / total > 0.3:
        risco = "alto"
    else:
        risco = "atencao"
    return {"contagem": contagem, "pct_medio": pct_medio, "total": len(exec_tasks), "n_atrasadas": n_atrasadas, "risco": risco}


def donut_svg(contagem, tamanho=120, espessura=16, mostrar_legenda=True, tamanho_legenda="13px"):
    """Gera um grafico de rosca (donut) em SVG puro a partir de um dict {rotulo: (valor, cor)}."""
    total = sum(v for v, _ in contagem.values())
    if total == 0:
        total = 1
    raio = (tamanho - espessura) / 2
    cx = cy = tamanho / 2
    circ = 2 * 3.14159265 * raio

    segmentos = []
    offset_acumulado = 0
    for rotulo, (valor, cor) in contagem.items():
        if valor == 0:
            continue
        frac = valor / total
        comprimento = frac * circ
        dasharray = f"{comprimento:.2f} {circ - comprimento:.2f}"
        dashoffset = -offset_acumulado
        segmentos.append(
            f'<circle cx="{cx}" cy="{cy}" r="{raio}" fill="none" stroke="{cor}" '
            f'stroke-width="{espessura}" stroke-dasharray="{dasharray}" '
            f'stroke-dashoffset="{dashoffset:.2f}" transform="rotate(-90 {cx} {cy})" />'
        )
        offset_acumulado += comprimento

    svg = f'''<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 {tamanho} {tamanho}">
        <circle cx="{cx}" cy="{cy}" r="{raio}" fill="none" stroke="#eef0f3" stroke-width="{espessura}" />
        {''.join(segmentos)}
    </svg>'''

    if not mostrar_legenda:
        return svg

    itens_legenda = "".join(
        f'<div class="legenda-item"><span class="legenda-ponto" style="background:{cor};"></span>'
        f'{rotulo} <span class="legenda-valor">{valor}</span></div>'
        for rotulo, (valor, cor) in contagem.items()
        if valor > 0
    )
    return f'''<div class="donut-wrap">
        <div class="donut-svg">{svg}</div>
        <div class="donut-legenda" style="font-size:{tamanho_legenda};">{itens_legenda}</div>
    </div>'''


# ---------- Saidas: CSV, dashboard HTML, relatorio, mudancas ----------

def write_csv(tasks, hoje, out_path):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["AS/Projeto", "Tarefa", "Fase/Resumo", "Início", "Término", "% Concluído", "Status", "Crítico"])
        for t in tasks:
            writer.writerow([
                t["grupo"] or "", "    " * t["nivel"] + t["nome"], "Sim" if t["resumo"] else "Não",
                t["inicio"].strftime("%d/%m/%Y") if t["inicio"] else "",
                t["termino"].strftime("%d/%m/%Y") if t["termino"] else "",
                t["pct_concluido"], ROTULOS[classificar_status(t, hoje)], "Sim" if t["critico"] else "Não",
            ])


def barra_gantt(t, d_min, span_dias, hoje, nivel_as):
    if not t["inicio"] or not t["termino"]:
        return ""
    offset = (t["inicio"].date() - d_min).days / span_dias * 100
    largura = max((t["termino"].date() - t["inicio"].date()).days / span_dias * 100, 0.35)
    indent = t["nivel"] * 14
    peso = 600 if t["resumo"] else 400

    if t["nivel"] == nivel_as and t["resumo"]:
        # linha de total do AS: mostra o percentual executado em verde sobre o restante em cor neutra
        largura_executada = largura * (t["pct_concluido"] / 100)
        barra_html = (
            f'<div class="barra" style="left:{offset:.2f}%; width:{largura:.2f}%; background:{CORES["fase"]}55;"></div>'
            f'<div class="barra" style="left:{offset:.2f}%; width:{largura_executada:.2f}%; background:{CORES["concluida"]};"></div>'
        )
    else:
        status = classificar_status(t, hoje)
        cor = CORES[status]
        barra_html = f'<div class="barra" style="left:{offset:.2f}%; width:{largura:.2f}%; background:{cor};"></div>'

    return f"""
        <div class="linha-gantt">
          <div class="nome-tarefa" style="padding-left:{indent}px; font-weight:{peso};">{t['nome']}</div>
          <div class="trilha">{barra_html}</div>
          <div class="pct-tarefa">{t['pct_concluido']}%</div>
        </div>"""


def build_html(tasks, hoje, out_path, titulo, nivel_as, resumo_anterior, overrides=None):
    overrides = overrides or {}
    grupos = agrupar_por_as(tasks, nivel_as)
    houve_comparacao = resumo_anterior is not None
    datas_validas = [t["inicio"] for t in tasks if t["inicio"]] + [t["termino"] for t in tasks if t["termino"]]
    d_min = min(datas_validas).date() if datas_validas else hoje
    d_max = max(datas_validas).date() if datas_validas else hoje
    span_dias = max((d_max - d_min).days, 1)

    exec_tasks_geral = [t for t in tasks if not t["resumo"] and t["nivel"] >= nivel_as]
    total_geral = len(exec_tasks_geral) or 1
    pct_geral = round(sum(t["pct_concluido"] for t in exec_tasks_geral) / total_geral, 1)
    atrasadas_geral = sum(1 for t in exec_tasks_geral if classificar_status(t, hoje) in ("atraso_prazo", "atraso_inicio"))
    andamento_geral = sum(1 for t in exec_tasks_geral if classificar_status(t, hoje) == "andamento")
    concluidas_geral = sum(1 for t in exec_tasks_geral if classificar_status(t, hoje) == "concluida")
    nao_iniciadas_geral = sum(1 for t in exec_tasks_geral if classificar_status(t, hoje) == "nao_iniciada")

    donut_geral = donut_svg(
        {
            "Concluídas": (concluidas_geral, CORES["concluida"]),
            "Em andamento": (andamento_geral, CORES["andamento"]),
            "Atrasadas": (atrasadas_geral, CORES["atraso_prazo"]),
            "Não iniciadas": (nao_iniciadas_geral, CORES["nao_iniciada"]),
        },
        tamanho=132, espessura=18,
    )

    stats_por_grupo = {}
    blocos_grupos = []
    cards_resumo = []
    for nome_grupo, tasks_grupo in grupos:
        st = stats_grupo(tasks_grupo, hoje)
        override_valor = buscar_override(nome_grupo, overrides)
        if override_valor is not None:
            st["pct_medio"] = override_valor
        stats_por_grupo[nome_grupo] = st
        c = st["contagem"]
        n_atrasadas_total = c["atraso_prazo"] + c["atraso_inicio"]
        if override_valor is not None:
            composicao_donut = {
                "Concluído (informado)": (override_valor, CORES["concluida"]),
                "Restante": (100 - override_valor, CORES["nao_iniciada"]),
            }
        else:
            composicao_donut = {
                "Concluídas": (c["concluida"], CORES["concluida"]),
                "Em andamento": (c["andamento"], CORES["andamento"]),
                "Atrasadas": (n_atrasadas_total, CORES["atraso_prazo"]),
                "Não iniciadas": (c["nao_iniciada"], CORES["nao_iniciada"]),
            }
        donut_grupo = donut_svg(composicao_donut, tamanho=76, espessura=11, mostrar_legenda=False)
        donut_resumo = donut_svg(composicao_donut, tamanho=104, espessura=14, mostrar_legenda=False)
        marca_manual = " <span class='marca-manual' title='Percentual informado manualmente pela projetista'>*</span>" if override_valor is not None else ""
        cards_resumo.append(f"""
        <div class="card-resumo" style="--cor-risco:{RISCO_COR[st['risco']]};">
          <div class="card-resumo-topo">
            <div class="card-resumo-donut">{donut_resumo}<div class="card-resumo-pct">{st['pct_medio']}%{marca_manual}</div></div>
            <div class="card-resumo-info">
              <h3>{nome_grupo}</h3>
              <div class="card-resumo-linha"><span class="ponto" style="background:{CORES['concluida']};"></span>Concluídas <b>{c['concluida']}</b></div>
              <div class="card-resumo-linha"><span class="ponto" style="background:{CORES['andamento']};"></span>Em andamento <b>{c['andamento']}</b></div>
              <div class="card-resumo-linha"><span class="ponto" style="background:{CORES['atraso_prazo']};"></span>Atrasadas <b>{n_atrasadas_total}</b></div>
            </div>
          </div>
        </div>""")
        atrasadas = sorted(
            [t for t in tasks_grupo if not t["resumo"] and classificar_status(t, hoje) in ("atraso_prazo", "atraso_inicio")],
            key=lambda x: x["termino"] or datetime.max,
        )
        linhas_atraso = "".join(
            f"""<tr><td>{f'<div class="fase-tarefa">{t["macro"]}</div>' if t['macro'] else ''}{t['nome']}</td><td class="mono">{t['termino'].strftime('%d/%m/%Y') if t['termino'] else '—'}</td>
            <td class="mono">{t['pct_concluido']}%</td>
            <td><span class="tag" style="background:{CORES[classificar_status(t, hoje)]}22; color:{CORES[classificar_status(t, hoje)]};">{ROTULOS[classificar_status(t, hoje)]}</span></td>
            <td>{'●' if t['critico'] else ''}</td></tr>"""
            for t in atrasadas
        ) or "<tr><td colspan='5' class='vazio'>Sem tarefas atrasadas neste AS.</td></tr>"

        anterior_grupo = resumo_anterior.get(nome_grupo) if houve_comparacao else None
        pct_anterior_txt = f"<span class='valor-anterior'>era {anterior_grupo['pct_medio']}%</span>" if anterior_grupo else ""
        atrasadas_anterior_txt = f"<span class='valor-anterior'>era {anterior_grupo['n_atrasadas']}</span>" if anterior_grupo else ""

        linhas_gantt = "".join(barra_gantt(t, d_min, span_dias, hoje, nivel_as) for t in tasks_grupo)

        blocos_grupos.append(f"""
        <section class="painel-as" style="--cor-risco:{RISCO_COR[st['risco']]};">
          <details {'open' if st['risco'] != 'ok' else ''}>
            <summary>
              <div class="as-cabecalho">
                <div class="as-titulo"><span class="risco-ponto"></span><h2>{nome_grupo}</h2></div>
                <div class="as-metricas">
                  <div class="as-donut">{donut_grupo}</div>
                  <div class="metrica"><span class="valor">{st['pct_medio']}%{marca_manual}</span>{pct_anterior_txt}<span class="rotulo">progresso</span></div>
                  <div class="metrica"><span class="valor" style="color:{RISCO_COR['alto'] if st['n_atrasadas'] else '#5a6577'};">{st['n_atrasadas']}</span>{atrasadas_anterior_txt}<span class="rotulo">atrasadas</span></div>
                  <div class="metrica"><span class="valor">{st['total']}</span><span class="rotulo">tarefas</span></div>
                  <span class="badge-risco" style="background:{RISCO_COR[st['risco']]}22; color:{RISCO_COR[st['risco']]};">{RISCO_LABEL[st['risco']]}</span>
                </div>
              </div>
            </summary>
            <div class="as-conteudo">
              <h3 class="subtitulo-secao">Tarefas atrasadas</h3>
              <table><tr><th>Tarefa</th><th>Prazo</th><th>%</th><th>Situação</th><th>Crít.</th></tr>{linhas_atraso}</table>
              <h3 class="subtitulo-secao">Cronograma</h3>
              <div class="gantt">{linhas_gantt}</div>
            </div>
          </details>
        </section>""")

    corpo_grupos = "".join(blocos_grupos)
    corpo_resumo = "".join(cards_resumo)
    aviso_comparacao = "" if houve_comparacao else """<div class="aviso">Primeira execução — ainda não há um envio anterior para comparar. A partir do próximo cronograma recebido, o dashboard vai mostrar o % e as atrasadas anteriores.</div>"""

    html = f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #f4f5f7; --painel: #ffffff; --painel-2: #eef0f3; --borda: #e2e5ea;
    --texto: #1a2029; --texto-2: #667085; --texto-3: #98a2b3;
    --ok: #12946b; --andamento: #1c76c9; --atencao: #c2650f; --alto: #d0342c; --fase: #667085;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:'Inter',-apple-system,Segoe UI,Roboto,Arial,sans-serif; background:var(--bg); color:var(--texto); margin:0; padding:32px 40px 60px; }}
  .topo {{ display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:20px; flex-wrap:wrap; gap:16px; border-bottom:1px solid var(--borda); padding-bottom:20px; }}
  .topo h1 {{ font-family:'Barlow Condensed', sans-serif; font-weight:700; font-size:30px; letter-spacing:0.3px; margin:0 0 4px; text-transform:uppercase; }}
  .topo .ref {{ color:var(--texto-2); font-size:13px; font-family:'IBM Plex Mono', monospace; }}
  .kpis {{ display:flex; gap:10px; flex-wrap:wrap; }}
  .kpi {{ background:var(--painel); border:1px solid var(--borda); border-radius:10px; padding:10px 18px; min-width:96px; text-align:center; box-shadow:0 1px 2px rgba(16,24,40,0.04); }}
  .kpi .valor {{ font-family:'Barlow Condensed', sans-serif; font-size:26px; font-weight:700; display:block; }}
  .kpi .rotulo {{ font-size:10.5px; color:var(--texto-2); text-transform:uppercase; letter-spacing:0.6px; }}
  .aviso {{ background:#fff7ed; border:1px solid #fdba74; color:#9a5b0f; font-size:13px; padding:12px 16px; border-radius:8px; margin-bottom:20px; }}

  .grade-resumo {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:14px; margin-bottom:28px; }}
  .card-resumo {{ background:var(--painel); border:1px solid var(--borda); border-top:3px solid var(--cor-risco); border-radius:12px; padding:16px 18px; box-shadow:0 1px 3px rgba(16,24,40,0.05); }}
  .card-resumo-topo {{ display:flex; align-items:center; gap:16px; }}
  .card-resumo-donut {{ position:relative; flex-shrink:0; width:104px; height:104px; }}
  .card-resumo-donut svg {{ display:block; }}
  .card-resumo-pct {{ position:absolute; top:0; left:0; width:104px; height:104px; display:flex; align-items:center; justify-content:center; font-family:'Barlow Condensed', sans-serif; font-size:20px; font-weight:700; }}
  .card-resumo-info h3 {{ font-family:'Barlow Condensed', sans-serif; font-weight:600; font-size:15px; margin:0 0 8px; letter-spacing:0.2px; }}
  .card-resumo-linha {{ font-size:12px; color:var(--texto-2); display:flex; align-items:center; gap:6px; margin-bottom:4px; }}
  .card-resumo-linha b {{ font-family:'IBM Plex Mono', monospace; color:var(--texto); margin-left:2px; }}
  .card-resumo .ponto {{ width:8px; height:8px; border-radius:2px; flex-shrink:0; }}
  .marca-manual {{ color:#c2650f; font-weight:700; cursor:help; }}
  .legenda-rodape {{ margin-top:10px; font-size:11px; color:var(--texto-3); }}
  section.painel-as {{ background:var(--painel); border:1px solid var(--borda); border-left:3px solid var(--cor-risco); border-radius:10px; margin-bottom:16px; overflow:hidden; box-shadow:0 1px 3px rgba(16,24,40,0.05); }}
  details summary {{ list-style:none; cursor:pointer; padding:16px 20px; }}
  details summary::-webkit-details-marker {{ display:none; }}
  details[open] summary {{ border-bottom:1px solid var(--borda); }}
  .as-cabecalho {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px; }}
  .as-titulo {{ display:flex; align-items:center; gap:10px; }}
  .risco-ponto {{ width:9px; height:9px; border-radius:50%; background:var(--cor-risco); flex-shrink:0; }}
  .as-titulo h2 {{ font-family:'Barlow Condensed', sans-serif; font-weight:600; font-size:18px; margin:0; letter-spacing:0.2px; }}
  .as-metricas {{ display:flex; align-items:center; gap:22px; }}
  .metrica {{ display:flex; flex-direction:column; align-items:center; min-width:52px; }}
  .metrica .valor {{ font-family:'IBM Plex Mono', monospace; font-size:15px; font-weight:500; }}
  .metrica .valor-anterior {{ font-family:'IBM Plex Mono', monospace; font-size:10px; color:var(--texto-3); }}
  .metrica .rotulo {{ font-size:9.5px; color:var(--texto-3); text-transform:uppercase; letter-spacing:0.5px; }}
  .badge-risco {{ font-size:11px; font-weight:600; padding:5px 11px; border-radius:20px; }}
  .as-conteudo {{ padding:18px 20px 22px; }}
  .subtitulo-secao {{ font-size:11.5px; text-transform:uppercase; letter-spacing:0.8px; color:var(--texto-3); margin:0 0 10px; font-weight:600; }}
  .as-conteudo h3.subtitulo-secao {{ margin-top:24px; }}
  .as-conteudo h3.subtitulo-secao:first-child {{ margin-top:0; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th {{ text-align:left; color:var(--texto-3); font-weight:500; font-size:11px; text-transform:uppercase; letter-spacing:0.4px; padding:6px 10px; border-bottom:1px solid var(--borda); }}
  td {{ padding:8px 10px; border-bottom:1px solid var(--borda); }}
  tr:last-child td {{ border-bottom:none; }}
  .mono {{ font-family:'IBM Plex Mono', monospace; color:var(--texto-2); }}
  .fase-tarefa {{ font-size:10.5px; color:var(--texto-3); margin-bottom:1px; }}
  .tag {{ padding:3px 8px; border-radius:5px; font-size:11px; font-weight:600; }}
  .vazio {{ color:var(--texto-3); font-style:italic; text-align:center; padding:14px; }}
  .gantt {{ display:flex; flex-direction:column; gap:2px; }}
  .linha-gantt {{ display:flex; align-items:center; gap:10px; font-size:11.5px; }}
  .nome-tarefa {{ width:300px; flex-shrink:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:var(--texto-2); }}
  .trilha {{ position:relative; flex:1; height:11px; background:var(--painel-2); border-radius:3px; border:1px solid var(--borda); }}
  .barra {{ position:absolute; top:0; height:11px; border-radius:3px; }}
  .pct-tarefa {{ width:34px; text-align:right; font-family:'IBM Plex Mono', monospace; color:var(--texto-3); font-size:11px; flex-shrink:0; }}
  @media (max-width:720px) {{ body {{ padding:20px 16px 40px; }} .nome-tarefa {{ width:150px; }} }}

  .donut-wrap {{ display:flex; align-items:center; gap:14px; }}
  .donut-svg svg {{ display:block; }}
  .donut-legenda {{ display:flex; flex-direction:column; gap:4px; }}
  .legenda-item {{ display:flex; align-items:center; gap:7px; color:var(--texto-2); white-space:nowrap; }}
  .legenda-ponto {{ width:9px; height:9px; border-radius:2px; flex-shrink:0; }}
  .legenda-valor {{ font-family:'IBM Plex Mono', monospace; color:var(--texto); font-weight:500; margin-left:2px; }}
  .topo-flex {{ display:flex; align-items:center; gap:28px; flex-wrap:wrap; }}
  .as-donut {{ flex-shrink:0; }}
</style>
</head>
<body>
  <div class="topo">
    <div>
      <h1>{titulo}</h1>
      <div class="ref">Referência: {hoje.strftime('%d/%m/%Y')} · {len(grupos)} AS acompanhadas</div>
    </div>
    <div class="topo-flex">
      {donut_geral}
      <div class="kpis">
        <div class="kpi"><span class="valor">{pct_geral}%</span><span class="rotulo">Progresso</span></div>
        <div class="kpi"><span class="valor" style="color:var(--alto);">{atrasadas_geral}</span><span class="rotulo">Atrasadas</span></div>
        <div class="kpi"><span class="valor" style="color:var(--andamento);">{andamento_geral}</span><span class="rotulo">Em andamento</span></div>
        <div class="kpi"><span class="valor" style="color:var(--ok);">{concluidas_geral}</span><span class="rotulo">Concluídas</span></div>
      </div>
    </div>
  </div>
  {aviso_comparacao}
  <div class="grade-resumo">{corpo_resumo}</div>
  {corpo_grupos}
  {"<div class='legenda-rodape'>* Percentual informado manualmente pela projetista (não calculado a partir das datas do cronograma).</div>" if overrides else ""}
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return grupos, stats_por_grupo


def write_report(grupos, hoje, titulo, out_path, resumo_anterior):
    blocos = []
    for nome_grupo, tasks_grupo in grupos:
        st = stats_grupo(tasks_grupo, hoje)
        atrasadas = sorted(
            [t for t in tasks_grupo if not t["resumo"] and classificar_status(t, hoje) in ("atraso_prazo", "atraso_inicio")],
            key=lambda x: x["termino"] or datetime.max,
        )
        linhas = "\n".join(
            f"- " + (f"_{t['macro']}_ — " if t["macro"] else "") + f"**{t['nome']}** — {ROTULOS[classificar_status(t, hoje)]}, prazo {t['termino'].strftime('%d/%m/%Y') if t['termino'] else '—'}, {t['pct_concluido']}% concluído" + (" (caminho crítico)" if t["critico"] else "")
            for t in atrasadas
        ) or "- Sem tarefas atrasadas."
        anterior = resumo_anterior.get(nome_grupo) if resumo_anterior else None
        comparativo = f" (era {anterior['pct_medio']}% / {anterior['n_atrasadas']} atrasadas)" if anterior else ""
        blocos.append(f"## {nome_grupo}\nRisco: **{RISCO_LABEL[st['risco']]}** · Progresso médio: **{st['pct_medio']}%**{comparativo} · Atrasadas: **{st['n_atrasadas']} de {st['total']}**\n\n{linhas}\n")
    md = f"# Relatório de Status — {titulo}\nData de referência: {hoje.strftime('%d/%m/%Y')}\n\n" + "\n".join(blocos)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)


def write_report_pdf(grupos, hoje, titulo, out_path, resumo_anterior):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from reportlab.lib import colors as rl_colors

    RISCO_HEX = {"ok": "#1a9c5e", "atencao": "#eab308", "alto": "#dc2626"}
    RISCO_HEX_CLARO = {"ok": "#e6f7ee", "atencao": "#fef9e7", "alto": "#fce8e6"}
    RISCO_TEXTO_BADGE = {"ok": rl_colors.white, "atencao": rl_colors.HexColor("#5c4400"), "alto": rl_colors.white}
    RISCO_ORDEM = {"alto": 0, "atencao": 1, "ok": 2}
    STATUS_HEX = {
        "atraso_prazo": "#d0342c", "atraso_inicio": "#c2650f",
        "andamento": "#1c76c9", "concluida": "#12946b", "nao_iniciada": "#98a2b3",
    }

    styles = getSampleStyleSheet()
    titulo_estilo = ParagraphStyle("TituloRel", parent=styles["Title"], fontSize=19, spaceAfter=2, textColor=rl_colors.HexColor("#1a2029"), alignment=0)
    subtitulo_estilo = ParagraphStyle("SubtituloRel", parent=styles["Normal"], fontSize=10.5, textColor=rl_colors.HexColor("#667085"), spaceAfter=18)
    secao_estilo = ParagraphStyle("SecaoTitulo", parent=styles["Heading2"], fontSize=11.5, textColor=rl_colors.HexColor("#667085"), spaceBefore=4, spaceAfter=8)
    as_titulo_estilo = ParagraphStyle("ASTitulo", parent=styles["Heading2"], fontSize=13, textColor=rl_colors.white, leading=16)
    vazio_estilo = ParagraphStyle("VazioAtraso", parent=styles["Normal"], fontSize=9.5, textColor=rl_colors.HexColor("#98a2b3"), spaceBefore=6, spaceAfter=10)
    celula_estilo = ParagraphStyle("Celula", parent=styles["Normal"], fontSize=9, leading=11.5)
    celula_bold_estilo = ParagraphStyle("CelulaBold", parent=celula_estilo, fontName="Helvetica-Bold")
    situacao_estilo = ParagraphStyle("Situacao", parent=celula_estilo, fontSize=7.3, leading=9, fontName="Helvetica-Bold", wordWrap=None)

    def badge_risco(risco):
        from reportlab.lib.units import mm
        t = Table([[RISCO_LABEL[risco]]], colWidths=[2.6 * cm], rowHeights=[0.52 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor(RISCO_HEX[risco])),
            ("TEXTCOLOR", (0, 0), (-1, -1), RISCO_TEXTO_BADGE[risco]),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROUNDEDCORNERS", [7, 7, 7, 7]),
        ]))
        return t

    grupos_com_stats = []
    for nome_grupo, tasks_grupo in grupos:
        st = stats_grupo(tasks_grupo, hoje)
        grupos_com_stats.append((nome_grupo, tasks_grupo, st))
    grupos_com_stats.sort(key=lambda g: RISCO_ORDEM[g[2]["risco"]])

    story = [
        Paragraph(f"Relatório de Status — {titulo}", titulo_estilo),
        Paragraph(f"Data de referência: {hoje.strftime('%d/%m/%Y')} · {len(grupos_com_stats)} AS acompanhadas", subtitulo_estilo),
    ]

    # ---------- Tabela-resumo geral ----------
    story.append(Paragraph("VISÃO GERAL", secao_estilo))
    linhas_resumo = [["Projeto", "Risco", "Progresso", "Atrasadas"]]
    estilos_extra = []
    for i, (nome_grupo, _, st) in enumerate(grupos_com_stats, start=1):
        linhas_resumo.append([
            Paragraph(nome_grupo, celula_estilo),
            badge_risco(st["risco"]),
            Paragraph(f"{st['pct_medio']}%", celula_estilo),
            Paragraph(f"{st['n_atrasadas']} de {st['total']}", celula_estilo),
        ])
        estilos_extra.append(("BACKGROUND", (0, i), (-1, i), rl_colors.HexColor(RISCO_HEX_CLARO[st["risco"]])))

    tabela_resumo = Table(linhas_resumo, colWidths=[7.5 * cm, 2.8 * cm, 2.8 * cm, 3.4 * cm], repeatRows=1)
    tabela_resumo.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1a2029")),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#e2e5ea")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        *estilos_extra,
    ]))
    story.append(tabela_resumo)
    story.append(Spacer(1, 18))

    # ---------- Detalhe por AS ----------
    story.append(Paragraph("DETALHAMENTO POR PROJETO", secao_estilo))

    for nome_grupo, tasks_grupo, st in grupos_com_stats:
        atrasadas = sorted(
            [t for t in tasks_grupo if not t["resumo"] and classificar_status(t, hoje) in ("atraso_prazo", "atraso_inicio")],
            key=lambda x: x["termino"] or datetime.max,
        )
        anterior = resumo_anterior.get(nome_grupo) if resumo_anterior else None
        comparativo = f"  (era {anterior['pct_medio']}% / {anterior['n_atrasadas']} atrasadas)" if anterior else ""
        cor_risco = RISCO_HEX[st["risco"]]

        col_widths = [7.4 * cm, 2.5 * cm, 1.5 * cm, 3.9 * cm, 1.2 * cm]

        linha_banda = [
            Paragraph(nome_grupo, as_titulo_estilo),
            "", "",
            Paragraph(
                f'{st["pct_medio"]}%{comparativo}  ·  {st["n_atrasadas"]}/{st["total"]} atrasadas',
                ParagraphStyle("HeaderInfo", parent=celula_estilo, textColor=rl_colors.white, fontSize=8.5, alignment=2),
            ),
            "",
        ]
        linha_cabecalho = ["Tarefa", "Prazo", "%", "Situação", "Crít."]

        linhas = [linha_banda, linha_cabecalho]
        estilos = [
            ("SPAN", (0, 0), (2, 0)),
            ("SPAN", (3, 0), (4, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor(cor_risco)),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("LEFTPADDING", (0, 0), (0, 0), 10),
            ("RIGHTPADDING", (-1, 0), (-1, 0), 10),
            ("BACKGROUND", (0, 1), (-1, 1), rl_colors.HexColor("#eef0f3")),
            ("TEXTCOLOR", (0, 1), (-1, 1), rl_colors.HexColor("#667085")),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, 1), 8),
            ("LINEBELOW", (0, 1), (-1, 1), 0.75, rl_colors.HexColor("#d0d5dd")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ]

        if atrasadas:
            for i, t in enumerate(atrasadas, start=2):
                prazo = t["termino"].strftime("%d/%m/%Y") if t["termino"] else "—"
                status_key = classificar_status(t, hoje)
                nome_com_fase = (f'<font size="7" color="#98a2b3">{t["macro"]}</font><br/>' if t["macro"] else "") + t["nome"]
                linhas.append([
                    Paragraph(nome_com_fase, celula_estilo),
                    Paragraph(prazo, celula_estilo),
                    Paragraph(f'{t["pct_concluido"]}%', celula_estilo),
                    Paragraph(f'<font color="{STATUS_HEX[status_key]}">{ROTULOS[status_key]}</font>', situacao_estilo),
                    Paragraph("●" if t["critico"] else "", ParagraphStyle("Crit", parent=celula_estilo, textColor=rl_colors.HexColor("#d0342c"), alignment=1)),
                ])
                if i % 2 == 0:
                    estilos.append(("BACKGROUND", (0, i), (-1, i), rl_colors.HexColor("#f7f8fa")))
                estilos.append(("LINEBELOW", (0, i), (-1, i), 0.4, rl_colors.HexColor("#eef0f3")))
        else:
            linhas.append([Paragraph("Sem tarefas atrasadas neste projeto.", vazio_estilo), "", "", "", ""])
            estilos.append(("SPAN", (0, 2), (-1, 2)))

        tabela_as = Table(linhas, colWidths=col_widths, repeatRows=2)
        tabela_as.setStyle(TableStyle(estilos))

        story.append(tabela_as)
        story.append(Spacer(1, 14))

    def rodape(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(rl_colors.HexColor("#98a2b3"))
        canvas_obj.drawString(2 * cm, 1.3 * cm, f"{titulo} — gerado automaticamente em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        canvas_obj.drawRightString(A4[0] - 2 * cm, 1.3 * cm, f"Página {doc_obj.page}")
        canvas_obj.restoreState()

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2.2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    doc.build(story, onFirstPage=rodape, onLaterPages=rodape)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("xml_paths", nargs="+", help="Caminho(s) do(s) cronograma(s) .xml recebido(s)")
    parser.add_argument("--data-referencia", default=None, help="AAAA-MM-DD (padrão: hoje)")
    parser.add_argument("--nivel-as", type=int, default=2)
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--titulo", default="Painel de Todos os Projetos")
    args = parser.parse_args()

    hoje = date.fromisoformat(args.data_referencia) if args.data_referencia else extrair_data_referencia(args.xml_paths[0])

    os.makedirs(HIST_DIR, exist_ok=True)

    tasks = load_tasks_multi(args.xml_paths, args.nivel_as)
    titulo = args.titulo

    resumo_anterior = carregar_resumo_anterior(RESUMO_PATH)
    houve_comparacao = resumo_anterior is not None

    write_csv(tasks, hoje, f"{args.out_dir}/cronograma.csv")
    grupos, stats_por_grupo = build_html(tasks, hoje, f"{args.out_dir}/dashboard.html", titulo, args.nivel_as, resumo_anterior, carregar_overrides(OVERRIDE_PATH))
    write_report(grupos, hoje, titulo, f"{args.out_dir}/relatorio_status.md", resumo_anterior)

    # arquivar historico desta execucao (guarda os xmls recebidos)
    pasta_hist = os.path.join(HIST_DIR, hoje.strftime("%Y-%m-%d"))
    os.makedirs(pasta_hist, exist_ok=True)
    for xml_path in args.xml_paths:
        shutil.copy(xml_path, os.path.join(pasta_hist, os.path.basename(xml_path)))

    # atualizar "ultimo resumo" para a proxima comparacao
    salvar_resumo_atual(stats_por_grupo, RESUMO_PATH)

    print(f"OK: {len(tasks)} tarefas processadas em {len(grupos)} AS, a partir de {len(args.xml_paths)} arquivo(s).")
    if not houve_comparacao:
        print("Primeira execução: sem comparação (a partir do próximo envio, vai mostrar % e atrasadas anteriores).")
    print(f"- {args.out_dir}/cronograma.csv")
    print(f"- {args.out_dir}/dashboard.html")
    print(f"- {args.out_dir}/relatorio_status.md")
    print(f"- Histórico arquivado em {pasta_hist}/")


if __name__ == "__main__":
    main()
