/**
 * ApplicationTool Lambda — Insurance Underwriting
 *
 * Creates insurance applications with geographic and eligibility validation.
 * Accepts an optional `message` field for free-text notes. Guardrail policies
 * scan this field via context.input.message before the Lambda is invoked.
 */
export const handler = async (event) => {
    console.log("ApplicationTool invoked:", JSON.stringify(event));

    const body = typeof event.body === "string" ? JSON.parse(event.body) : event;
    const applicant_region = body.applicant_region || "UNKNOWN";
    const coverage_amount = body.coverage_amount || 0;
    const message = body.message || "";

    // Basic eligibility check
    const eligible_regions = ["US", "CA", "UK", "EU", "AU"];
    if (!eligible_regions.includes(applicant_region)) {
        return {
            application_id: null,
            status: "REJECTED",
            reason: `Region ${applicant_region} is not eligible for coverage`,
        };
    }

    const application_id = `APP-${Date.now()}-${applicant_region}`;
    return {
        application_id,
        status: "CREATED",
        applicant_region,
        coverage_amount,
        notes_received: message.length > 0,
        summary: `Application ${application_id} created for ${applicant_region} region with $${coverage_amount.toLocaleString()} coverage`,
    };
};
