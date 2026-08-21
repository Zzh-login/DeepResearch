from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", max_length=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip() or "新对话"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("会话标题不能为空")
        return clean