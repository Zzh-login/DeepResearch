from langchain_openai import ChatOpenAI

from domain.model_gateway.contracts import ModelProfile


class DeepSeekChatProvider:
    name = "deepseek"

    def __init__(self, settings) -> None:
        self._settings = settings

    @property
    def model_name(self) -> str:
        return self._settings.deepseek_model

    def build_model(self, profile: ModelProfile):
        kwargs = {}
        if profile.response_format is not None:
            kwargs["model_kwargs"] = {
                "response_format": profile.response_format,
            }
        return ChatOpenAI(
            api_key=self._settings.deepseek_api_key,
            base_url=self._settings.deepseek_base_url,
            model=self._settings.deepseek_model,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            max_retries=0,
            timeout=profile.timeout_seconds,
            extra_body={"thinking": {"type": "disabled"}},
            **kwargs,
        )