"""
Configuration management for PostgreSQL Database MCP Server
"""

import os
from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    """Database connection configuration"""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Create configuration from environment variables"""
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "password"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": "***",  # Hide password in logs
        }

    @property
    def connection_string(self) -> str:
        """Get PostgreSQL connection string"""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class ServerConfig:
    """MCP Server configuration"""

    host: str = "127.0.0.1"
    port: int = 8000
    stateless_http: bool = True
    transport: str = "streamable-http"

    @classmethod
    def from_env(cls) -> "ServerConfig":
        """Create server configuration from environment variables"""
        return cls(
            host=os.getenv("SERVER_HOST", "127.0.0.1"),
            port=int(os.getenv("SERVER_PORT", "8000")),
            stateless_http=os.getenv("STATELESS_HTTP", "true").lower() == "true",
            transport=os.getenv("TRANSPORT", "streamable-http"),
        )
