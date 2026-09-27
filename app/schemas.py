import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

_SPACES = re.compile(r"[ \t]+")
_BLANK_RUNS = re.compile(r"\n{3,}")


def normalize(text: str) -> str:
    """Trim, collapse repeated spaces/tabs, keep line breaks (bullet notes need them)."""
    lines = (_SPACES.sub(" ", line).strip() for line in text.strip().splitlines())
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines))


_Clean = BeforeValidator(lambda v: normalize(v) if isinstance(v, str) else v)

UserId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
SampleText = Annotated[str, _Clean, Field(min_length=50, max_length=4000)]
NotesText = Annotated[str, _Clean, Field(min_length=1, max_length=2000)]


class SampleIn(BaseModel):
    user_id: UserId
    sample: SampleText


class SampleOut(BaseModel):
    user_id: str
    stored: bool
    chars: int


class RewriteIn(BaseModel):
    user_id: UserId
    text: NotesText


class RewriteOut(BaseModel):
    rewrite: str
    model: str
    prompt_version: str
    latency_ms: int
