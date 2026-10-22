"""One Google-style generation call through our routing system."""

from ntg_llm import LLMClient, cli_entrypoint


@cli_entrypoint
def main():
    client = LLMClient()
    response = client.models.generate_content(
        model="router",
        contents="Explain generative AI in one sentence.",
    )
    print(response.text)


if __name__ == "__main__":
    main()
