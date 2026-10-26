/**
 * ApprovalTool Lambda — Insurance Underwriting
 *
 * Approves high-value or high-risk underwriting decisions.
 */
export const handler = async (event) => {
    console.log("ApprovalTool invoked:", JSON.stringify(event));

    const body = typeof event.body === "string" ? JSON.parse(event.body) : event;
    const claim_amount = body.claim_amount || 0;
    const risk_level = body.risk_level || "medium";

    // Auto-approve low-risk small claims; escalate the rest
    const auto_approve = risk_level === "low" && claim_amount <= 100000;

    return {
        approval_id: `APPROVAL-${Date.now()}`,
        approved: auto_approve,
        claim_amount,
        risk_level,
        status: auto_approve ? "APPROVED" : "PENDING_REVIEW",
        message: auto_approve
            ? `Claim of $${claim_amount.toLocaleString()} auto-approved (low risk)`
            : `Claim of $${claim_amount.toLocaleString()} requires manual review (${risk_level} risk)`,
    };
};
