/**
 * ApplicationTool - Insurance Application Creation
 * Creates insurance applications with applicant region and coverage amount
 *
 * Parameters:
 * - applicant_region: Customer's geographic region
 * - coverage_amount: Requested insurance coverage amount
 */

import crypto from 'crypto';

function createApplication(args) {
    const { applicant_region, coverage_amount } = args;

    if (!applicant_region) {
        return { status: 'ERROR', message: 'Applicant region is required', application_id: null };
    }
    if (!coverage_amount || coverage_amount <= 0) {
        return { status: 'ERROR', message: 'Coverage amount must be positive', application_id: null };
    }

    const applicationId = `APP-${applicant_region}-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    return {
        status: 'SUCCESS',
        message: `Application successfully created for region ${applicant_region} with coverage $${coverage_amount.toLocaleString()}`,
        application_id: applicationId,
        coverage_amount: coverage_amount,
        region: applicant_region,
        created_at: new Date().toISOString()
    };
}

export const handler = async (event) => {
    try {
        let args;
        let isJsonRpc = false;

        if (event.method === 'tools/call' && event.params) {
            isJsonRpc = true;
            const params = event.params || {};
            if (params.name !== 'create_application') {
                return { jsonrpc: '2.0', id: event.id || 'unknown', error: { code: -32601, message: `Function not found: ${params.name}` } };
            }
            args = params.arguments || {};
        } else {
            args = event;
        }

        const result = createApplication(args);

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
