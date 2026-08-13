from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from domain.chat.modes import ChatMode


class ChatRequest(BaseModel):
    action: Literal["chat"] = "chat"
    text: str = Field(min_length=1, max_length=10000)
    mode: ChatMode = ChatMode.NORMAL
    knowledge_base_id: UUID | None = None
    tts_mode: Literal["cloud", "local"] = "cloud"
    agent_mode: bool = True

    @model_validator(mode="after")
    def validate_chat_request(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("消息不能为空")
        if self.mode == ChatMode.KNOWLEDGE and self.knowledge_base_id is None:
            raise ValueError("仅知识库模式必须选择知识库")
        if self.mode == ChatMode.NORMAL:
            self.knowledge_base_id = None
        return self


class TtsRequest(BaseModel):
    action: Literal["tts"]
    text: str = Field(min_length=1, max_length=10000)