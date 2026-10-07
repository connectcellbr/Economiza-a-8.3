# Economiza AI

Aplicativo web de comparação de preços com backend Python, pesquisa multifonte, consultora Economiza AI e busca por foto/OCR.

## Deploy no Render

Este projeto usa Docker para manter o ambiente de execução consistente no Render.

- Runtime: Docker
- Health check: `/api/health`
- O servidor usa automaticamente `PORT` e escuta em `0.0.0.0`.
- `ads.txt` está na raiz para o Google AdSense.

Se configurar manualmente no Render, use **Web Service** conectado ao GitHub. O `render.yaml` já contém a configuração recomendada.

## GitHub

Envie os arquivos desta pasta para a raiz do repositório. Não envie `__pycache__` nem o ZIP.

## AdSense

O código do AdSense está no painel e o `ads.txt` está na raiz. A exibição efetiva depende da aprovação/configuração do domínio e da disponibilidade de anúncios do Google.
