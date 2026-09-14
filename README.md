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

   ⚠️ **Importante:** o Streamlit Community Cloud gratuito só permite **um
   app privado por workspace** — e essa vaga já está ocupada pelo app do
   "Programa Térmicas". Ao criar este app novo, deixe-o como **público**
   (não marque "Make this app private"). Isso não expõe os dados: o app
   tem uma senha de acesso própria (configurada no passo 3) que bloqueia
   *todo* o conteúdo, não só o upload — então o link é tecnicamente
   público, mas ninguém vê nada sem a senha.
3. Depois de criado, vá em **Settings → Secrets** do app e adicione as
   **duas** senhas:

   ```toml
   access_password = "senha-para-quem-so-vai-consultar"
   admin_password = "senha-para-quem-vai-enviar-cronogramas-novos"
   ```

   - `access_password` é pedida a **qualquer pessoa** que abrir o link,
     antes de ver qualquer painel — é a que protege o conteúdo por causa do
     app ser público no Streamlit.
   - `admin_password` é a mesma senha que você já usa hoje no app das
     térmicas: só quem digitar ela em "🔒 Sou responsável pela atualização"
     na barra lateral consegue enviar cronogramas novos.

   Podem ser senhas diferentes ou iguais — mas se forem iguais, quem só
   consulta acaba com a mesma senha de quem administra, então o mais seguro
   é usar senhas diferentes e só passar a `access_password` para quem
   precisa consultar.
4. Pronto — compartilhe o link só com quem tiver a `access_password`.

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
