/**
 * ApprovalTool - Insurance Underwriting Approval
 * Approves underwriting decisions and high-value claims
 *
 * Parameters:
 * - claim_amount: Insurance claim/coverage amount
 * - risk_level: Risk level assessment (low, medium, high, critical)
 */

import crypto from 'crypto';

function approveUnderwriting(args) {
    const { claim_amount, risk_level } = args;

    if (!claim_amount || claim_amount <= 0) {
        return { status: 'ERROR', message: 'Valid claim amount is required', approval_id: null };
    }
    if (!risk_level) {
        return { status: 'ERROR', message: 'Risk level assessment is required', approval_id: null };
    }

    const approvalId = `APV-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    return {
        status: 'APPROVED',
        message: `Claim of $${claim_amount.toLocaleString()} approved following underwriting review. Risk level: ${risk_level}. Processing within 5-7 business days.`,
        approval_id: approvalId,
        claim_amount: claim_amount,
        risk_level: risk_level,
        approved_at: new Date().toISOString()
    };
}

export const handler = async (event) => {
    try {
        let args;
        let isJsonRpc = false;

        if (event.method === 'tools/call' && event.params) {
            isJsonRpc = true;
            const params = event.params || {};
            if (params.name !== 'approve_underwriting') {
                return { jsonrpc: '2.0', id: event.id || 'unknown', error: { code: -32601, message: `Function not found: ${params.name}` } };
            }
            args = params.arguments || {};
        } else {
            args = event;
        }

        const result = approveUnderwriting(args);

        if (isJsonRpc) {
            return { jsonrpc: '2.0', id: event.id, result: { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }], isError: result.status === 'ERROR' } };
        }
        return result;
    } catch (error) {
        if (event.method === 'tools/call') {
            return { jsonrpc: '2.0', id: event.id || 'unknown', error: { code: -32603, message: `Internal error: ${error.message}` } };
        }
        return { status: 'ERROR', message: `Internal error: ${error.message}` };
    }
};
