"""Ferramenta de diagnóstico para o bot Cerebroso.

Executar `python doctor.py` antes de iniciar o bot ajuda a garantir que o
arquivo `cerebroso.py` não foi corrompido por respostas `429: Too Many Requests`
ou por downloads incompletos. Caso seja detectado algum problema, o script
sugere como baixar novamente o projeto.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "cerebroso.py"
EXPECTED_PREAMBLE = "import asyncio"
ERROR_SIGNATURES = (
    "429: Too Many Requests",
    "<!DOCTYPE html",
    "<?xml",
    "<html",
)


def main() -> None:
    if not TARGET.exists():
        print(
            "[ERRO] Arquivo cerebroso.py não encontrado. Certifique-se de extrair o repositório completo antes de continuar."
        )
        return

    try:
        first_bytes = TARGET.read_text(encoding="utf-8", errors="ignore")[:200].strip()
    except OSError as exc:
        print(f"[ERRO] Não foi possível ler cerebroso.py: {exc}")
        return

    if not first_bytes:
        print(
            "[ALERTA] O arquivo cerebroso.py está vazio. Refaça o download usando `git clone` ou o botão Download ZIP do GitHub."
        )
        return

    for signature in ERROR_SIGNATURES:
        if first_bytes.startswith(signature):
            print(
                "[ERRO] O arquivo cerebroso.py começa com uma resposta de erro (429/HTML).\n"
                "Isso acontece quando o download foi bloqueado por rate limit.\n\n"
                "Soluções rápidas:\n"
                "  1. Use `git clone https://github.com/...` para baixar o repositório completo.\n"
                "  2. No GitHub, clique em Code → Download ZIP, extraia e copie todos os arquivos.\n"
                "  3. Evite salvar apenas o arquivo raw; verifique se a primeira linha começa com `import asyncio`."
            )
            return

    if not first_bytes.startswith(EXPECTED_PREAMBLE):
        print(
            "[ALERTA] O arquivo cerebroso.py não inicia com `import asyncio`.\n"
            "Verifique se você baixou a versão correta e se não houve edição acidental."
        )
        return

    print(
        "[OK] cerebroso.py parece íntegro. Você pode seguir com `python cerebroso.py` normalmente."
    )


if __name__ == "__main__":
    main()
