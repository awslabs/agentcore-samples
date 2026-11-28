#!/usr/bin/env python3
"""Separate Runtime IAM negatives and Gateway M2M auth checks; no models/emails."""

from cloud_checks import main

if __name__ == "__main__":
    main("auth")
