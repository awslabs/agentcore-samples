# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""GitHub data models."""

from pydantic import BaseModel, Field


class GitHubProject(BaseModel):
    """GitHub repository model."""

    id: int = Field(description="Unique repository identifier")
    name: str = Field(description="Repository name")
    full_name: str = Field(description="Full name including owner (e.g. owner/repo)")
    description: str | None = Field(default=None, description="Repository description")
    stargazers_count: int = Field(default=0, description="Number of stars")
    forks_count: int = Field(default=0, description="Number of forks")
    private: bool = Field(default=False, description="Whether the repository is private")
    html_url: str = Field(description="URL to the repository")


class GitHubUser(BaseModel):
    """GitHub user model."""

    id: int = Field(description="Unique user identifier")
    name: str | None = Field(default=None, description="User's display name")
    login: str = Field(description="User's username")
    email: str | None = Field(default=None, description="User's email address")
    bio: str | None = Field(default=None, description="User's biography")
    html_url: str = Field(description="URL to the user's profile")
