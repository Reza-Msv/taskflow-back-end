import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """Payload for new user registration."""

    email: EmailStr = Field(
        ...,
        description="User email address",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plaintext password (minimum 8 characters)",
        examples=["SecurePassw0rd!"],
    )
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User full display name",
        examples=["Jane Doe"],
    )


class LoginRequest(BaseModel):
    """Payload for user authentication."""

    email: EmailStr = Field(
        ...,
        description="User email address",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User password",
        examples=["SecurePassw0rd!"],
    )


class UserResponse(BaseModel):
    """Public user profile data transfer object."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique user identifier")
    email: EmailStr = Field(..., description="User email address")
    full_name: str = Field(..., description="User full display name")
    is_active: bool = Field(..., description="Whether the user account is active")
    is_superuser: bool = Field(..., description="Whether user has superuser privileges")
    created_at: datetime = Field(..., description="Account creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Account last update timestamp (UTC)")


class RegisterResponse(BaseModel):
    """Response returned upon successful user registration."""

    user: UserResponse = Field(..., description="Registered user profile")
    message: str = Field(
        default="User registered successfully",
        description="Human-readable status message",
    )


class AuthResponse(BaseModel):
    """Response returned upon successful authentication."""

    user: UserResponse = Field(..., description="Authenticated user profile")
    csrf_token: str = Field(
        ...,
        description="CSRF token for the X-CSRF-Token header in mutating requests",
    )
    message: str = Field(
        default="Authenticated successfully",
        description="Human-readable status message",
    )


class MessageResponse(BaseModel):
    """Generic status message response."""

    message: str = Field(..., description="Status message")
