import streamlit as st
import tempfile
import os
import re
import shutil
from datetime import date, datetime

import core

st.set_page_config(page_title="Painel de Todos os Projetos", layout="wide", page_icon="📊")

# Único lugar para trocar o nome do dashboard — não precisa mudar mais nada.
TITULO = "Painel de Todos os Projetos"

HIST_DIR_APP = "historico_app"
os.makedirs(HIST_DIR_APP, exist_ok=True)
RESUMO_PATH_APP = os.path.join(HIST_DIR_APP, "_ultimo_resumo.csv")

SALVOS_DIR = "salvos"
os.makedirs(SALVOS_DIR, exist_ok=True)

ARQUIVOS = {
    "html": os.path.join(SALVOS_DIR, "painel.html"),
    "csv": os.path.join(SALVOS_DIR, "cronograma.csv"),
    "pdf": os.path.join(SALVOS_DIR, "relatorio.pdf"),
    "meta": os.path.join(SALVOS_DIR, "meta.txt"),
    "tasks_raw": os.path.join(SALVOS_DIR, "tasks_raw.csv"),
    "data_ref": os.path.join(SALVOS_DIR, "data_ref.txt"),
}


def salvar_tasks_raw(tasks, data_ref, caminho_tasks, caminho_data_ref):
    import csv as _csv
    with open(caminho_tasks, "w", newline="", encoding="utf-8-sig") as f:
        writer = _csv.writer(f)
        writer.writerow(["uid", "nome", "nivel", "resumo", "critico", "inicio", "termino", "pct_concluido", "grupo"])
        for t in tasks:
            writer.writerow([
                t["uid"], t["nome"], t["nivel"], int(t["resumo"]), int(t["critico"]),
                t["inicio"].isoformat() if t["inicio"] else "",
                t["termino"].isoformat() if t["termino"] else "",
                t["pct_concluido"], t["grupo"] or "",
            ])
    with open(caminho_data_ref, "w", encoding="utf-8") as f:
        f.write(data_ref.isoformat())


def carregar_tasks_raw(caminho_tasks, caminho_data_ref):
    import csv as _csv
    from datetime import datetime as _dt
    if not (os.path.exists(caminho_tasks) and os.path.exists(caminho_data_ref)):
        return None, None
    tasks = []
    with open(caminho_tasks, encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            tasks.append({
                "uid": row["uid"],
                "nome": row["nome"],
                "nivel": int(row["nivel"]),
                "resumo": row["resumo"] == "1",
                "critico": row["critico"] == "1",
                "inicio": _dt.fromisoformat(row["inicio"]) if row["inicio"] else None,
                "termino": _dt.fromisoformat(row["termino"]) if row["termino"] else None,
                "pct_concluido": int(row["pct_concluido"]),
                "grupo": row["grupo"] or None,
            })
    with open(caminho_data_ref, encoding="utf-8") as f:
        data_ref_salva = date.fromisoformat(f.read().strip())
    return tasks, data_ref_salva


def nome_curto(nome_completo):
    """Remove o prefixo tecnico 'AS-XXX - NNNNN - ' (ou variações — com/sem
    hífen colado, espaço antes do número, en-dash '–', ou sem o segundo
    código) deixando só a parte descritiva."""
    m = re.match(r"^AS\s*-?\s*\d+\s*[-–]\s*(?:\d+\s*[-–]\s*)?(.+)$", nome_completo)
    return m.group(1).strip() if m else nome_completo


def salvar_meta(caminho, data_ref, n_arquivos):
    origem = f"{n_arquivos} arquivo(s) de cronograma" if n_arquivos != 1 else "1 arquivo de cronograma"
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(f"Processado em {datetime.now().strftime('%d/%m/%Y %H:%M')} — {origem}, com data de referência {data_ref.strftime('%d/%m/%Y')}")


def ler_meta(caminho):
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            return f.read()
    return None


def exigir_senha_de_acesso():
    """Bloqueia TODO o conteúdo do app (não só o upload) até digitar a senha
    de acesso. Existe porque o Streamlit Community Cloud gratuito só permite
    um app privado por workspace — este app fica com o link tecnicamente
    público, mas ninguém vê nenhum dado de projeto sem essa senha.
    """
    if st.session_state.get("tem_acesso"):
        return
    st.title(f"📊 {TITULO}")
    st.caption("Este painel tem dados de projetos e é restrito. Informe a senha de acesso para continuar.")
    senha_digitada = st.text_input("Senha de acesso", type="password")
    if st.button("Entrar"):
        try:
            senha_correta = st.secrets.get("access_password", None)
        except Exception:
            senha_correta = None
        if senha_correta and senha_digitada == senha_correta:
            st.session_state["tem_acesso"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()


exigir_senha_de_acesso()

st.title(f"📊 {TITULO}")
st.caption(
    "Quem recebe cronograma(s) novo(s) faz o upload aqui embaixo para atualizar (pode ser mais de um "
    "arquivo .xml de uma vez, se os projetos ainda vierem em cronogramas separados). "
    "Quem só quer consultar pode abrir o link sem enviar nada — o último painel gerado fica salvo."
)

with st.sidebar:
    st.header("Configuração")

    if "is_admin" not in st.session_state:
        st.session_state.is_admin = False

    if not st.session_state.is_admin:
        with st.expander("🔒 Sou responsável pela atualização"):
            senha_digitada = st.text_input("Senha", type="password")
            if st.button("Entrar"):
                try:
                    senha_correta = st.secrets.get("admin_password", None)
                except Exception:
                    senha_correta = None
                if senha_correta and senha_digitada == senha_correta:
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Senha incorreta.")

    uploaded_files = []
    data_ref = date.today()
    if st.session_state.is_admin:
        st.success("🔓 Modo administrador")
        uploaded_files = st.file_uploader(
            "Enviar cronograma(s) novo(s) (.xml)", type=["xml"], accept_multiple_files=True,
        ) or []

        data_sugerida = date.today()
        if uploaded_files:
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp_peek:
                tmp_peek.write(uploaded_files[0].getvalue())
                caminho_peek = tmp_peek.name
            try:
                data_sugerida = core.extrair_data_referencia(caminho_peek)
            except Exception:
                pass
            finally:
                os.remove(caminho_peek)

        data_ref = st.date_input(
            "Data de referência",
            value=data_sugerida,
            help="Preenchida automaticamente com a data de emissão do primeiro cronograma enviado. Pode ajustar se precisar.",
        )
        st.caption(
            "Ao enviar arquivo(s) novo(s), o painel gerado fica salvo e visível para todos que abrirem "
            "o link depois — até você enviar uma atualização."
        )

# ---------------- Sem upload: mostra o último salvo, se existir ----------------
if not uploaded_files:
    tasks_salvas, data_ref_salva = carregar_tasks_raw(ARQUIVOS["tasks_raw"], ARQUIVOS["data_ref"])
    if tasks_salvas is not None:
        meta = ler_meta(ARQUIVOS["meta"])
        if meta:
            st.info(f"📌 Mostrando o último painel processado. {meta}")

        grupos_disponiveis = sorted(set(t["grupo"] for t in tasks_salvas if t["grupo"]))
        selecionados_view = st.sidebar.multiselect(
            "Filtrar projetos exibidos", grupos_disponiveis, default=grupos_disponiveis,
            format_func=nome_curto,
        )
        tasks_filtradas_view = [t for t in tasks_salvas if t["grupo"] is None or t["grupo"] in selecionados_view]

        if not selecionados_view:
            st.warning("Selecione ao menos um projeto (AS) na barra lateral.")
            st.stop()

        with tempfile.TemporaryDirectory() as tmpdir_view:
            out_html_view = os.path.join(tmpdir_view, "painel_view.html")
            core.build_html(tasks_filtradas_view, data_ref_salva, out_html_view, TITULO, 2, None)
            with open(out_html_view, encoding="utf-8") as f:
                html = f.read()

        col1, col2 = st.columns(2)
        if os.path.exists(ARQUIVOS["csv"]):
            with open(ARQUIVOS["csv"], "rb") as f:
                col1.download_button("⬇️ Baixar cronograma.csv", f, file_name="cronograma.csv")
        if os.path.exists(ARQUIVOS["pdf"]):
            with open(ARQUIVOS["pdf"], "rb") as f:
                col2.download_button("⬇️ Baixar relatorio.pdf", f, file_name="relatorio.pdf", mime="application/pdf")
        st.components.v1.html(html, height=2400, scrolling=True)
    else:
        st.info("⬅️ Ainda não há nenhum cronograma processado. Envie um ou mais arquivos .xml na barra lateral para começar.")
    st.stop()

# ---------------- Com upload: processa e salva ----------------
with tempfile.TemporaryDirectory() as tmpdir:
    xml_paths = []
    for uploaded in uploaded_files:
        xml_path = os.path.join(tmpdir, uploaded.name)
        with open(xml_path, "wb") as f:
            f.write(uploaded.getbuffer())
        xml_paths.append(xml_path)

    tasks_todas = core.load_tasks_multi(xml_paths, 2)
    grupos_nomes = sorted(set(t["grupo"] for t in tasks_todas if t["grupo"]))

    with st.sidebar:
        selecionados = st.multiselect(
            "Quais projetos (AS) incluir?", grupos_nomes, default=grupos_nomes,
            format_func=nome_curto,
        )

    tasks = [t for t in tasks_todas if t["grupo"] is None or t["grupo"] in selecionados]

    if not tasks or not selecionados:
        st.warning("Selecione ao menos um projeto (AS) na barra lateral.")
        st.stop()

    # Identifica se este e' um conjunto de arquivos novo nesta sessao, ou um rerun
    # (filtro/data mudou) do MESMO conjunto ja processado. So' lemos o "resumo
    # anterior" do disco e salvamos o novo resumo na PRIMEIRA vez que vemos este
    # conjunto -- caso contrario, reruns disparados por outros widgets (ex: filtro
    # de projetos) acabariam comparando o conjunto com os dados que ele mesmo
    # acabou de salvar.
    identificador_arquivos = tuple(
        getattr(uploaded, "file_id", None) or (uploaded.name, uploaded.size)
        for uploaded in uploaded_files
    )
    if st.session_state.get("resumo_salvo_para") != identificador_arquivos:
        resumo_anterior = core.carregar_resumo_anterior(RESUMO_PATH_APP)
        st.session_state["resumo_anterior_cache"] = resumo_anterior
        # O resumo salvo para comparacoes futuras usa TODOS os projetos dos
        # arquivos enviados, independente do filtro de exibicao selecionado
        # nesta visualizacao.
        _, stats_todas_as = core.build_html(
            tasks_todas, data_ref, os.path.join(tmpdir, "_baseline.html"), TITULO, 2, resumo_anterior, {}
        )
        core.salvar_resumo_atual(stats_todas_as, RESUMO_PATH_APP)
        st.session_state["resumo_salvo_para"] = identificador_arquivos
    else:
        resumo_anterior = st.session_state.get("resumo_anterior_cache")

    out_html = os.path.join(tmpdir, "painel.html")
    core.write_csv(tasks, data_ref, os.path.join(tmpdir, "cronograma.csv"))
    grupos, stats_por_grupo = core.build_html(
        tasks, data_ref, out_html, TITULO, 2, resumo_anterior, {}
    )
    core.write_report_pdf(grupos, data_ref, TITULO, os.path.join(tmpdir, "relatorio.pdf"), resumo_anterior)

    shutil.copy(out_html, ARQUIVOS["html"])
    shutil.copy(os.path.join(tmpdir, "cronograma.csv"), ARQUIVOS["csv"])
    shutil.copy(os.path.join(tmpdir, "relatorio.pdf"), ARQUIVOS["pdf"])
    salvar_meta(ARQUIVOS["meta"], data_ref, len(uploaded_files))
    salvar_tasks_raw(tasks_todas, data_ref, ARQUIVOS["tasks_raw"], ARQUIVOS["data_ref"])

    st.success("✅ Painel atualizado e salvo — quem abrir o link agora já vê essa versão.")
    col1, col2 = st.columns(2)
    with open(os.path.join(tmpdir, "cronograma.csv"), "rb") as f:
        col1.download_button("⬇️ Baixar cronograma.csv", f, file_name="cronograma.csv")
    with open(os.path.join(tmpdir, "relatorio.pdf"), "rb") as f:
        col2.download_button("⬇️ Baixar relatorio.pdf", f, file_name="relatorio.pdf", mime="application/pdf")

    with open(out_html, encoding="utf-8") as f:
        html = f.read()
    st.components.v1.html(html, height=2400, scrolling=True)
