from __future__ import annotations

import tempfile
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional

from agno.agent import Agent
from agno.media import File
from agno.models.google import Gemini
from pydantic import BaseModel, Field

from ..services.pdf_slides_service import SlideImagePayload

INSTRUCTIONS = """
You are an expert at analyzing presentation slides. For each slide image provided,
produce a structured JSON description that matches the SlideContent schema.
Focus strictly on the information visible in the slide and avoid hallucinating unseen details.
If a particular category (text, images, diagrams) is not present, return "None".
Classify the slide type as "title" if it primarily shows the lecture title/section heading; otherwise classify it as "content".
"""


class SlideType(str, Enum):
    title = "title"
    content = "content"


class SlideContent(BaseModel):
    text_content: str = Field(
        description="Literal text content found on the slide including headings, bullets, annotations, etc."
    )
    images_description: str = Field(
        description="Description of photographs or illustrations displayed on the slide."
    )
    diagrams_and_figures_description: str = Field(
        description="Description of diagrams, charts, tables, or other structured visuals on the slide."
    )
    slide_type: SlideType = Field(
        default=SlideType.content,
        description="Label slides as 'title' for title/section slides, otherwise 'content'.",
    )


class SlideContentWithNumber(BaseModel):
    slide_number: int
    text_content: str
    images_description: str
    diagrams_and_figures_description: str
    slide_type: SlideType

    @classmethod
    def from_content(cls, slide_number: int, content: SlideContent) -> "SlideContentWithNumber":
        return cls(
            slide_number=slide_number,
            text_content=content.text_content,
            images_description=content.images_description,
            diagrams_and_figures_description=content.diagrams_and_figures_description,
            slide_type=content.slide_type,
        )

    def as_chunk(self) -> str:
        """Concatenate the structured fields into a single chunk suitable for embeddings."""

        def normalize(value: str) -> str:
            return " ".join(value.split())

        parts = [
            f"Text: {normalize(self.text_content)}",
            f"Images: {normalize(self.images_description)}",
            f"Diagrams/Figures: {normalize(self.diagrams_and_figures_description)}",
        ]
        return " | ".join(parts)


class SlideDescriptionAgent:
    """Generate structured descriptions for slide images using Agno + Gemini."""

    def __init__(
        self,
        *,
        model_id: str = "gemini-2.5-flash-lite",
        instructions: str = INSTRUCTIONS,
    ) -> None:
        self.agent = Agent(
            model=Gemini(id=model_id),
            output_schema=SlideContent,
            instructions=instructions,
            markdown=False,
        )

    def describe_slide(self, slide_bytes: bytes, slide_number: int) -> SlideContentWithNumber:
        """Run the AI agent on a single slide image."""

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(slide_bytes)
            tmp.flush()
            tmp_path = Path(tmp.name)

        try:
            response = self.agent.run(
                input=self._build_prompt(slide_number),
                files=[File(filepath=tmp_path)],
            )
            content = self._coerce_response(response)
        finally:
            tmp_path.unlink(missing_ok=True)

        return SlideContentWithNumber.from_content(slide_number, content)

    def describe_slides(self, slides: Iterable[SlideImagePayload]) -> List[SlideContentWithNumber]:
        """Process a collection of slide payloads sequentially."""

        descriptions: list[SlideContentWithNumber] = []
        for slide in slides:
            descriptions.append(self.describe_slide(slide.image_bytes, slide.slide_number))
        return descriptions

    def _coerce_response(self, response) -> SlideContent:
        content = getattr(response, "content", None)
        if isinstance(content, SlideContent):
            return content
        if isinstance(content, dict):
            return SlideContent(**self._normalize_fields(content))
        if hasattr(response, "output") and isinstance(response.output, dict):
            return SlideContent(**self._normalize_fields(response.output))
        # Fallback: treat whole response as text_content.
        return SlideContent(
            text_content=self._default_text(content),
            images_description="None",
            diagrams_and_figures_description="None",
            slide_type=SlideType.content,
        )

    def _normalize_fields(self, raw: dict) -> dict:
        normalized = dict(raw)
        normalized["images_description"] = self._normalize_optional_field(
            normalized.get("images_description")
        )
        normalized["diagrams_and_figures_description"] = self._normalize_optional_field(
            normalized.get("diagrams_and_figures_description")
        )
        normalized["text_content"] = self._normalize_optional_field(normalized.get("text_content"))
        return normalized

    def _normalize_optional_field(self, value) -> str:
        if value is None:
            return "None"
        value_str = str(value).strip()
        return value_str if value_str else "None"

    def _default_text(self, value) -> str:
        if value is None:
            return "None"
        return str(value)

    def _build_prompt(self, slide_number: int) -> str:
        return (
            f"Analyze slide {slide_number}. Extract literal text, describe any images, and describe diagrams/figures. "
            "Respond using the SlideContent schema. Label slide_type as 'title' only if the slide primarily contains a title."
        )
