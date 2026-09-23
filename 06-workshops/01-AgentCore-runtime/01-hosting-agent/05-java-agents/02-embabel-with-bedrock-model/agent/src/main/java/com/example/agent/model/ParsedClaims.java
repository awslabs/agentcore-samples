package com.example.agent.model;

import java.util.List;

/** LLM-parsed individual claims extracted from the user request. */
public record ParsedClaims(List<String> claims) {}
