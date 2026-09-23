package com.example.agent.model;

import java.util.List;

/** Final human-readable fact-check summary. Terminal goal of the GOAP pipeline. */
public record FactCheckReport(String summary, List<VerifiedClaims.VerifiedClaim> verifiedClaims) {}
