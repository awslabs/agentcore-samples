/**
 * RiskModelTool Lambda — Insurance Underwriting
 *
 * Invokes the risk scoring model with governance controls.
 */
export const handler = async (event) => {
    console.log("RiskModelTool invoked:", JSON.stringify(event));

    const body = typeof event.body === "string" ? JSON.parse(event.body) : event;
    const api_classification = body.API_classification || "internal";
    const data_governance_approval = body.data_governance_approval === true;

    if (!data_governance_approval) {
        return {
            risk_score: null,
            status: "BLOCKED",
            reason: "Data governance approval required before invoking risk model",
        };
    }

    // Simulate risk score based on classification
    const base_scores = { public: 0.3, internal: 0.5, restricted: 0.8 };
    const risk_score = base_scores[api_classification] ?? 0.5;

    return {
        risk_score,
        risk_level: risk_score < 0.4 ? "low" : risk_score < 0.7 ? "medium" : "high",
        api_classification,
        status: "COMPLETED",
        message: `Risk model completed. Score: ${risk_score} (${api_classification} API)`,
    };
};
