package com.example.agent.model;

import java.util.List;

/** User-submitted claims to fact-check. Entry point to the GOAP pipeline. */
public record FactCheckRequest(List<String> claims) {}
