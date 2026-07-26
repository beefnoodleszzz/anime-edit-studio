"""Versioned, deterministic Sound Recipe authoring.

Resolve's public API cannot write Fairlight gain automation (P17).  Sound
envelopes are therefore rendered to WAV before import.  This is technical DSP,
not an LLM concern, and the seed is part of the recipe contract.
"""
from __future__ import annotations

import math
import random
import struct
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SoundRecipe:
    id: str
    duration_sec: float
    seed: int


RECIPES = (
    SoundRecipe("sword_whoosh_v1", 0.42, 1701),
    SoundRecipe("impact_low_v1", 0.36, 1702),
    SoundRecipe("sub_impact_v1", 0.62, 1703),
    SoundRecipe("riser_v1", 2.0, 1704),
)


def _clamp(value: float) -> float:
    return max(-0.98, min(0.98, value))


def _sample(recipe: SoundRecipe, t: float, rng: random.Random) -> float:
    duration = recipe.duration_sec
    x = min(1.0, t / duration)
    noise = rng.uniform(-1.0, 1.0)
    if recipe.id == "sword_whoosh_v1":
        envelope = math.sin(math.pi * x) ** 1.7
        swept = math.sin(2 * math.pi * (260 * t + 1150 * t * t))
        return envelope * (0.44 * swept + 0.20 * noise)
    if recipe.id == "impact_low_v1":
        envelope = math.exp(-9.5 * x)
        body = math.sin(2 * math.pi * (105 - 48 * x) * t)
        click = noise * math.exp(-60 * x)
        return 0.72 * envelope * body + 0.30 * click
    if recipe.id == "sub_impact_v1":
        envelope = math.exp(-6.2 * x)
        return 0.82 * envelope * math.sin(2 * math.pi * (58 - 22 * x) * t)
    if recipe.id == "riser_v1":
        envelope = x**1.4
        swept = math.sin(2 * math.pi * (120 * t + 840 * t * x * x))
        return envelope * (0.30 * swept + 0.22 * noise)
    raise ValueError(f"unknown Sound Recipe: {recipe.id}")


def build_sound_recipe(
    recipe: SoundRecipe,
    output: Path,
    *,
    sample_rate: int = 48_000,
) -> Path:
    """Write a mono PCM16 WAV with an exact, reproducible sample count."""
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(recipe.seed)
    frames = round(recipe.duration_sec * sample_rate)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        block = bytearray()
        for index in range(frames):
            value = _clamp(_sample(recipe, index / sample_rate, rng))
            block.extend(struct.pack("<h", round(value * 32767)))
        wav.writeframes(block)
    return output


def build_sound_recipe_library(root: Path) -> list[Path]:
    return [
        build_sound_recipe(recipe, root / recipe.id / f"{recipe.id}.wav")
        for recipe in RECIPES
    ]
