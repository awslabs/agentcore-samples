/**
 * RiskModelTool - Simplified Risk Model
 * Invokes risk scoring model and returns assessment
 * 
 * Parameters:
 * - API_classification: API classification (public, internal, restricted)
 * - data_governance_approval: Whether data governance has approved model usage
 */

import crypto from 'crypto';

// Simplified risk model function
function invokeRiskModel(args) {
    console.log('Processing risk model invocation:', JSON.stringify(args, null, 2));
    
    const {
        API_classification,
        data_governance_approval
    } = args;
    
    // Validate required parameters
    if (!API_classification) {
        return {
            status: 'ERROR',
            message: 'API classification is required',
            risk_score: null
        };
    }
    
    if (data_governance_approval === undefined || data_governance_approval === null) {
        return {
            status: 'ERROR', 
            message: 'Data governance approval status is required',
            risk_score: null
        };
    }
    
    // Generate mock risk score and return simple response
    const riskScore = Math.floor(Math.random() * 100);
    const modelId = `MDL-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    
    return {
        status: 'SUCCESS',
        message: `Risk assessment complete: applicant scored ${riskScore}/100 with moderate confidence based on credit history, claims frequency, and demographic factors indicating standard underwriting eligibility.`,
        model_id: modelId,
        risk_score: riskScore,
        API_classification: API_classification,
        governance_approved: data_governance_approval,
        executed_at: new Date().toISOString()
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
            if (functionName !== 'invoke_risk_model') {
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
        const result = invokeRiskModel(args);
        
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
// Uncomment to test locally with: node risk_model_tool.js
/*
const testEvent = {
    jsonrpc: '2.0',
    id: 'test-1',
    method: 'tools/call',
    params: {
        name: 'invoke_risk_model',
        arguments: {
            API_classification: 'internal',
            data_governance_approval: true
        }
    }
};

handler(testEvent).then(result => {
    console.log('Test result:', JSON.stringify(result, null, 2));
});
*/
