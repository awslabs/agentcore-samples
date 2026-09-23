package com.example.agent.model;

import java.util.List;

/** Browser-verified claims with status and source URLs. */
public record VerifiedClaims(List<VerifiedClaim> results) {

    public record VerifiedClaim(
        String claim,
        String status,   // VERIFIED, UNVERIFIED, CONTRADICTED
        String sourceUrl,
        String details
    ) {}
}
