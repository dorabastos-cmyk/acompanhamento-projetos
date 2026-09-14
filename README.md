# Painel de Todos os Projetos

Este é o mesmo mecanismo do app "Programa Térmicas": você (ou quem for
responsável) envia o(s) cronograma(s) exportado(s) do MS Project (.xml), e o
app gera o painel — cards por AS/projeto com donut de status, tarefas
atrasadas, cronograma (Gantt), e permite baixar `cronograma.csv` e
`relatorio.pdf`. O último painel gerado fica salvo, então quem só quer
consultar abre o link sem precisar enviar nada.

A diferença deste para o das térmicas: aqui você pode enviar **mais de um
arquivo .xml de uma vez** (por exemplo, um do programa de térmicas e outro
de outro programa), e todos os projetos aparecem juntos, misturados no mesmo
grid — sem separação por programa. Quando um dia todos os projetos vierem
num cronograma único, é só enviar esse arquivo único; continua funcionando
igual.

## Arquivos

- `app.py` — a interface Streamlit (upload, senha de administrador, salvar
  histórico, exibir o painel).
- `core.py` — o motor: lê o(s) XML(s), calcula status/atraso/risco por
  tarefa, monta o HTML do painel e o PDF do relatório. É reaproveitado do
  `atualizar_cronograma.py` do app de térmicas, sem mudanças na lógica.
- `requirements.txt` — dependências (`streamlit`, `reportlab`).

## Como publicar (mesmo caminho que você já usou para o das térmicas)

1. Crie um repositório novo no GitHub (pode ser privado) e suba estes
   arquivos (`app.py`, `core.py`, `requirements.txt`, e este `README.md`).
2. Em [share.streamlit.io](https://share.streamlit.io), clique em **New
   app**, escolha esse repositório, o branch e `app.py` como arquivo
   principal.
3. Depois de criado, vá em **Settings → Secrets** do app e adicione:

   ```toml
   admin_password = "escolha-uma-senha-aqui"
   ```

   É a mesma senha que vai digitar em "🔒 Sou responsável pela atualização"
   na barra lateral, para poder enviar novos cronogramas.
4. Pronto — o link do app pode ser compartilhado com quem só for consultar
   (eles não verão a opção de upload, a menos que também tenham a senha).

## Onde os dados ficam salvos

O app cria, na primeira execução, as pastas `salvos/` (último painel gerado,
CSV e PDF) e `historico_app/` (resumo da última rodada, usado para comparar
progresso e atrasadas entre envios). Isso é armazenamento local do próprio
servidor do Streamlit Cloud — não precisa criar essas pastas manualmente
nem subi-las pro GitHub.

## Testando localmente (opcional)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se quiser testar o motor sem abrir a interface (por exemplo, para rodar como
rotina manual em lote), o `core.py` também funciona por linha de comando:

```bash
python3 core.py cronograma1.xml cronograma2.xml --titulo "Painel de Todos os Projetos"
```
