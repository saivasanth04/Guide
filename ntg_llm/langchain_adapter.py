import asyncio
from typing import Any

from langchain_core.language_models.llms import LLM


class NTGLangChainLLM(LLM):
    client: Any
    model_name: str = "router"

    @property
    def _llm_type(self) -> str:
        return "ntg-router"

    @property
    def _identifying_params(self):
        return {
            "model": self.model_name
        }

    def _call(
        self,
        prompt: str,
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs,
    ) -> str:

        config = {
            "response_mime_type": "text/plain",
            "system_instruction": (
                "Respond only with text. For tool requests, write Action: and Action Input: "
                "as specified in the prompt. Do not emit native function calls."
            ),
        }
        if stop is not None:
            config["stop_sequences"] = stop
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )

        return response.text

    async def _acall(
        self,
        prompt: str,
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs,
    ) -> str:

        return await asyncio.to_thread(
            self._call,
            prompt,
            stop,
            run_manager,
            **kwargs,
        )