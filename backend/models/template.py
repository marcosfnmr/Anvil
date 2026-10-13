"""User-managed, shared scenario templates."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Field as SqlField, SQLModel


class SharedScenarioTemplate(SQLModel, table=True):
    __tablename__ = "shared_scenario_templates"

    id: str = SqlField(primary_key=True, max_length=64)
    title: str = SqlField(max_length=120, index=True)
    category: str = SqlField(max_length=80)
    summary: str = SqlField(max_length=500)
    complexity: str = SqlField(default="Intermedia", max_length=40)
    tags_json: str = SqlField(default="[]")
    schema_version: int = SqlField(default=1)
    revision: int = SqlField(default=1)
    scenario_json: str
    created_by: str = SqlField(index=True, foreign_key="platform_users.id")
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))


class SharedTemplateCreate(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=10, max_length=500)
    complexity: str = Field(default="Intermedia", min_length=2, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("title", "category", "summary", "complexity")
    @classmethod
    def strip_metadata(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Template metadata cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        tags = [value.strip() for value in values]
        if any(not value or len(value) > 40 for value in tags):
            raise ValueError("Template tags must contain 1 to 40 non-blank characters")
        if len({value.casefold() for value in tags}) != len(tags):
            raise ValueError("Template tags must be unique")
        return tags


class SharedTemplateRead(BaseModel):
    id: str
    title: str
    category: str
    summary: str
    complexity: str
    tags: list[str]
    shared: bool = True
    created_at: datetime | None = None
    created_by: str | None = None
    revision: int = 1


class SharedTemplateUpdate(BaseModel):
    expected_revision: StrictInt = Field(ge=1)
    title: str | None = Field(default=None, min_length=2, max_length=120)
    category: str | None = Field(default=None, min_length=2, max_length=80)
    summary: str | None = Field(default=None, min_length=10, max_length=500)
    complexity: str | None = Field(default=None, min_length=2, max_length=40)
    tags: list[str] | None = Field(default=None, max_length=12)
    scenario_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("title", "category", "summary", "complexity")
    @classmethod
    def strip_update_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Template metadata cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_update_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        tags = [value.strip() for value in values]
        if any(not value or len(value) > 40 for value in tags):
            raise ValueError("Template tags must contain 1 to 40 non-blank characters")
        if len({value.casefold() for value in tags}) != len(tags):
            raise ValueError("Template tags must be unique")
        return tags

    @model_validator(mode="after")
    def require_change(self) -> "SharedTemplateUpdate":
        if not (self.model_fields_set - {"expected_revision"}):
            raise ValueError("Provide at least one template field to update")
        if any(getattr(self, name) is None for name in self.model_fields_set - {"expected_revision"}):
            raise ValueError("Template update fields cannot be null")
        return self


class SharedScenarioTemplateRevision(SQLModel, table=True):
    __tablename__ = "shared_scenario_template_revisions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_shared_template_revision"),)

    id: str = SqlField(default_factory=lambda: uuid4().hex, primary_key=True)
    template_id: str = SqlField(index=True, max_length=64)
    version: int
    title: str = SqlField(max_length=120)
    category: str = SqlField(max_length=80)
    summary: str = SqlField(max_length=500)
    complexity: str = SqlField(max_length=40)
    tags_json: str
    schema_version: int
    scenario_json: str
    created_by: str = SqlField(max_length=64)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))


class SharedTemplateRevisionRead(BaseModel):
    version: int
    title: str
    category: str
    summary: str
    complexity: str
    tags: list[str]
    schema_version: int
    created_by: str
    created_at: datetime


class SharedComponentPreset(SQLModel, table=True):
    __tablename__ = "shared_component_presets"

    id: str = SqlField(primary_key=True, max_length=64)
    title: str = SqlField(max_length=120, index=True)
    description: str = SqlField(max_length=500)
    device_type: str = SqlField(max_length=64, index=True)
    component_type: str = SqlField(max_length=64, index=True)
    config_json: str
    schema_version: int = SqlField(default=1)
    revision: int = SqlField(default=1)
    created_by: str = SqlField(index=True, foreign_key="platform_users.id")
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))


class SharedComponentPresetRevision(SQLModel, table=True):
    __tablename__ = "shared_component_preset_revisions"
    __table_args__ = (UniqueConstraint("preset_id", "version", name="uq_shared_component_preset_revision"),)

    id: str = SqlField(default_factory=lambda: uuid4().hex, primary_key=True)
    preset_id: str = SqlField(index=True, max_length=64)
    version: int
    title: str = SqlField(max_length=120)
    description: str = SqlField(max_length=500)
    device_type: str = SqlField(max_length=64)
    component_type: str = SqlField(max_length=64)
    config_json: str
    schema_version: int
    created_by: str = SqlField(max_length=64)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))


class SharedComponentCreate(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=10, max_length=500)
    device_type: str = Field(min_length=1, max_length=64)
    component_type: str = Field(min_length=1, max_length=64)
    config: dict

    @field_validator("title", "description")
    @classmethod
    def strip_component_metadata(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Component preset metadata cannot be blank")
        return value


class SharedComponentUpdate(BaseModel):
    expected_revision: StrictInt = Field(ge=1)
    title: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=500)
    config: dict | None = None

    @field_validator("title", "description")
    @classmethod
    def strip_update_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Component preset metadata cannot be blank")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "SharedComponentUpdate":
        if not (self.model_fields_set - {"expected_revision"}):
            raise ValueError("Provide at least one component preset field to update")
        if any(getattr(self, name) is None for name in self.model_fields_set - {"expected_revision"}):
            raise ValueError("Component preset update fields cannot be null")
        return self


class SharedComponentRead(BaseModel):
    id: str
    title: str
    description: str
    device_type: str
    component_type: str
    config: dict
    schema_version: int = 1
    revision: int = 1
    created_by: str
    created_at: datetime
    updated_at: datetime


class SharedComponentRevisionRead(BaseModel):
    version: int
    title: str
    description: str
    device_type: str
    component_type: str
    config: dict
    schema_version: int
    created_by: str
    created_at: datetime
