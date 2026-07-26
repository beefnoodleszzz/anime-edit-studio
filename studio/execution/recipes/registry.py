"""Recipe registry and deterministic parameter validation (AGENTS R4/R7)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = REPO / "config" / "recipes.yaml"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterRule(_Base):
    type: Literal["float", "int", "bool", "string"]
    required: bool = False
    default: object | None = None
    min: float | None = None
    max: float | None = None
    choices: list[object] | None = None


class Recipe(_Base):
    id: str
    version: str
    kind: Literal["effect", "color", "sound", "transition", "title"]
    engine: str
    capability: str
    verified: bool = False
    artifact: str | None = None
    preview: str | None = None
    acceptance: str | None = None
    parameters: dict[str, ParameterRule] = Field(default_factory=dict)
    bindings: dict[str, str] = Field(
        default_factory=dict,
        description="logical parameter name -> engine input address",
    )

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class RecipeIssue:
    code: str
    message: str


class RecipeRegistry:
    def __init__(self, recipes: list[Recipe], *, root: Path = REPO):
        self.root = root
        self._by_id: dict[str, Recipe] = {}
        for recipe in recipes:
            if recipe.id in self._by_id:
                raise ValueError(f"重复 recipe id: {recipe.id}")
            self._by_id[recipe.id] = recipe

    @classmethod
    def load(cls, path: Path = DEFAULT_REGISTRY) -> "RecipeRegistry":
        if not path.exists():
            raise FileNotFoundError(f"Recipe registry 不存在: {path}")
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = payload.get("recipes") or []
        return cls([Recipe.model_validate(row) for row in rows], root=REPO)

    def get(self, recipe_id: str) -> Recipe | None:
        return self._by_id.get(recipe_id)

    def artifact_path(self, recipe_id: str) -> Path:
        recipe = self.get(recipe_id)
        if recipe is None:
            raise KeyError(recipe_id)
        if not recipe.artifact:
            raise ValueError(f"recipe {recipe_id!r} 没有 artifact")
        return self.root / recipe.artifact

    def resolved_params(self, recipe_id: str, params: dict) -> dict:
        recipe = self.get(recipe_id)
        if recipe is None:
            raise KeyError(recipe_id)
        values = {
            name: rule.default
            for name, rule in recipe.parameters.items()
            if rule.default is not None
        }
        values.update(params)
        return values

    def validate(
        self,
        recipe_id: str,
        params: dict,
        *,
        expected_kind: str,
    ) -> list[RecipeIssue]:
        recipe = self.get(recipe_id)
        if recipe is None:
            return [RecipeIssue("RECIPE_NOT_REGISTERED", f"recipe {recipe_id!r} 未注册")]
        issues: list[RecipeIssue] = []
        if recipe.kind != expected_kind:
            issues.append(
                RecipeIssue(
                    "RECIPE_KIND_MISMATCH",
                    f"recipe {recipe_id!r} 是 {recipe.kind}，此处要求 {expected_kind}",
                )
            )
        if not recipe.verified:
            issues.append(
                RecipeIssue("RECIPE_NOT_VERIFIED", f"recipe {recipe_id!r} 尚未人工验收")
            )
        elif not self._acceptance_artifacts_exist(recipe):
            issues.append(
                RecipeIssue(
                    "RECIPE_ACCEPTANCE_INCOMPLETE",
                    f"recipe {recipe_id!r} 标记 verified 但缺 artifact/preview/ACCEPTANCE",
                )
            )
        unknown = sorted(set(params) - set(recipe.parameters))
        if unknown:
            issues.append(
                RecipeIssue("RECIPE_UNKNOWN_PARAM", f"未知参数: {', '.join(unknown)}")
            )
        for name, rule in recipe.parameters.items():
            if name not in params:
                if rule.required and rule.default is None:
                    issues.append(
                        RecipeIssue("RECIPE_PARAM_REQUIRED", f"缺少必填参数 {name!r}")
                    )
                continue
            issues.extend(self._validate_value(name, params[name], rule))
        return issues

    def _acceptance_artifacts_exist(self, recipe: Recipe) -> bool:
        paths = (recipe.artifact, recipe.preview, recipe.acceptance)
        return all(value and (self.root / value).is_file() for value in paths)

    @staticmethod
    def _validate_value(name: str, value: object, rule: ParameterRule) -> list[RecipeIssue]:
        expected = {
            "float": (int, float),
            "int": (int,),
            "bool": (bool,),
            "string": (str,),
        }[rule.type]
        # bool is an int subclass in Python but must never pass numeric schemas.
        if not isinstance(value, expected) or (
            rule.type in {"float", "int"} and isinstance(value, bool)
        ):
            return [
                RecipeIssue(
                    "RECIPE_PARAM_TYPE",
                    f"参数 {name!r} 要求 {rule.type}，实际 {type(value).__name__}",
                )
            ]
        issues: list[RecipeIssue] = []
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if rule.min is not None and value < rule.min:
                issues.append(
                    RecipeIssue("RECIPE_PARAM_RANGE", f"参数 {name!r} 小于 {rule.min}")
                )
            if rule.max is not None and value > rule.max:
                issues.append(
                    RecipeIssue("RECIPE_PARAM_RANGE", f"参数 {name!r} 大于 {rule.max}")
                )
        if rule.choices is not None and value not in rule.choices:
            issues.append(
                RecipeIssue(
                    "RECIPE_PARAM_CHOICE",
                    f"参数 {name!r} 必须是 {rule.choices!r} 之一",
                )
            )
        return issues
