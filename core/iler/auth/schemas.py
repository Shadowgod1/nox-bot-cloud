"""Request/response models shared by the setup, auth and users APIs."""

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from iler.auth.deps import Role, User
from iler.auth.passwords import MAX_LENGTH, validate_policy

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise ValueError("invalid e-mail")
    return value


Email = Annotated[str, AfterValidator(_email)]
NewPassword = Annotated[str, AfterValidator(validate_policy)]


class Credentials(BaseModel):
    email: Annotated[str, AfterValidator(lambda v: v.strip().lower())]
    password: str = Field(max_length=MAX_LENGTH)


class OwnerCreate(BaseModel):
    email: Email
    password: NewPassword
    name: str = Field("", max_length=200)


class UserCreate(OwnerCreate):
    role: Role = "operator"


class PasswordChange(BaseModel):
    old: str = Field(max_length=MAX_LENGTH)
    new: NewPassword


class UserListItem(User):
    created_at: datetime
    last_login_at: datetime | None


class Ok(BaseModel):
    ok: bool = True
