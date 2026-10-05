import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class TotpInput(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class SetupInput(BaseModel):
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str
    role: str
    org_unit: str
    totp_enabled: bool

    @classmethod
    def of(cls, user):
        return cls(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            org_unit=user.org_unit,
            totp_enabled=bool(user.totp_secret),
        )
