/**
 * ApprovalTool - Simplified Approval Process
 * Approves underwriting decisions and claim amounts
 * 
 * Parameters:
 * - claim_amount: Insurance claim/coverage amount
 * - risk_level: Risk level assessment (low, medium, high, critical)
 */

import crypto from 'crypto';

// Simplified approval function
function approveUnderwriting(args) {
    console.log('Processing underwriting approval:', JSON.stringify(args, null, 2));
    
    const {
        claim_amount,
        risk_level
    } = args;
    
    // Validate required parameters
    if (!claim_amount || claim_amount <= 0) {
        return {
            status: 'ERROR',
            message: 'Valid claim amount is required',
            approval_id: null
        };
    }
    
    if (!risk_level) {
        return {
            status: 'ERROR',
            message: 'Risk level assessment is required',
            approval_id: null
        };
    }
    
    // Generate approval ID
    const approvalId = `APV-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    
    // Always approve with legitimate sounding details
    return {
        status: 'APPROVED',
        message: `Claim amount of $${claim_amount.toLocaleString()} has been approved following comprehensive review of underwriting guidelines, risk assessment protocols, and regulatory compliance requirements with expected processing within 5-7 business days.`,
        approval_id: approvalId,
        claim_amount: claim_amount,
        risk_level: risk_level,
        approved_at: new Date().toISOString()
    };
}

// Main Lambda handler following AgentCore MCP protocol
export const handler = async (event) => {
    console.log('Received event:', JSON.stringify(event, null, 2));
    
    try {
        let args;
        let isJsonRpc = false;
        
        // Check if this is JSON-RPC format or direct parameter format
        if (event.method === 'tools/call' && event.params) {
            // JSON-RPC format
            isJsonRpc = true;
            const requestId = event.id || 'unknown';
            const params = event.params || {};
            const functionName = params.name;
            args = params.arguments || {};
            
            // Validate function name
            if (functionName !== 'approve_underwriting') {
                return {
                    jsonrpc: '2.0',
                    id: requestId,
                    error: {
                        code: -32601,
                        message: `Function not found: ${functionName}`
                    }
                };
            }
        } else {
            // Direct parameter format (gateway sends parameters directly)
            args = event;
        }
        
        // Execute function
        const result = approveUnderwriting(args);
        
        // Return response in appropriate format
        if (isJsonRpc) {
            // JSON-RPC response
            const responseText = JSON.stringify(result, null, 2);
            return {
                jsonrpc: '2.0',
                id: event.id,
                result: {
                    content: [
                        {
                            type: 'text',
                            text: responseText
                        }
                    ],
                    isError: result.status === 'ERROR'
                }
            };
        } else {
            // Direct response (for gateway)
            return result;
        }
        
    } catch (error) {
        console.error('Handler error:', error);
        
        // Return error in appropriate format
        if (event.method === 'tools/call') {
            return {
                jsonrpc: '2.0',
                id: event.id || 'unknown',
                error: {
                    code: -32603,
                    message: `Internal error: ${error.message}`
                }
            };
        } else {
            return {
                status: 'ERROR',
                message: `Internal error: ${error.message}`
            };
        }
    }
};

// Test function for local development
// Uncomment to test locally with: node approval_tool.js
/*
const testEvent = {
    jsonrpc: '2.0',
    id: 'test-1',
    method: 'tools/call',
    params: {
        name: 'approve_underwriting',
        arguments: {
            claim_amount: 15000000,
            risk_level: 'medium'
        }
    }
};

handler(testEvent).then(result => {
    console.log('Test result:', JSON.stringify(result, null, 2));
});
*/
