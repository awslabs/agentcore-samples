/**
 * FinancialReportTool - Financial Report Retrieval
 *
 * Retrieves financial reports from the data platform.
 * Governed by RBAC: analyst/senior-analyst/manager roles, internal classification only.
 *
 * Parameters:
 * - report_type: Type of report (quarterly, annual, monthly)
 * - region: Geographic region (US, EU, APAC)
 * - classification_level: Data classification (internal, public)
 */

import crypto from 'crypto';

function getFinancialReport(args) {
    console.log('Processing financial report request:', JSON.stringify(args, null, 2));

    const { report_type, region, classification_level } = args;

    if (!report_type) {
        return { status: 'ERROR', message: 'report_type is required', report_id: null };
    }
    if (!region) {
        return { status: 'ERROR', message: 'region is required', report_id: null };
    }
    if (!classification_level) {
        return { status: 'ERROR', message: 'classification_level is required', report_id: null };
    }

    const reportId = `RPT-${region}-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    const mockRevenue = (Math.random() * 10000000 + 1000000).toFixed(2);
    const mockProfit = (Math.random() * 2000000 + 100000).toFixed(2);

    return {
        status: 'SUCCESS',
        report_id: reportId,
        report_type: report_type,
        region: region,
        classification_level: classification_level,
        summary: `${report_type.charAt(0).toUpperCase() + report_type.slice(1)} financial report for ${region} region. Revenue: $${parseFloat(mockRevenue).toLocaleString()}, Net Profit: $${parseFloat(mockProfit).toLocaleString()}. Classification: ${classification_level}.`,
        generated_at: new Date().toISOString()
    };
}

export const handler = async (event) => {
    console.log('Received event:', JSON.stringify(event, null, 2));

    try {
        let args;
        let isJsonRpc = false;

        if (event.method === 'tools/call' && event.params) {
            isJsonRpc = true;
            const requestId = event.id || 'unknown';
            const params = event.params || {};
            const functionName = params.name;
            args = params.arguments || {};

            if (functionName !== 'get_financial_report') {
                return {
                    jsonrpc: '2.0',
                    id: requestId,
                    error: { code: -32601, message: `Function not found: ${functionName}` }
                };
            }
        } else {
            args = event;
        }

        const result = getFinancialReport(args);

        if (isJsonRpc) {
            return {
                jsonrpc: '2.0',
                id: event.id,
                result: {
                    content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
                    isError: result.status === 'ERROR'
                }
            };
        } else {
            return result;
        }

    } catch (error) {
        console.error('Handler error:', error);
        if (event.method === 'tools/call') {
            return {
                jsonrpc: '2.0',
                id: event.id || 'unknown',
                error: { code: -32603, message: `Internal error: ${error.message}` }
            };
        } else {
            return { status: 'ERROR', message: `Internal error: ${error.message}` };
        }
    }
};
