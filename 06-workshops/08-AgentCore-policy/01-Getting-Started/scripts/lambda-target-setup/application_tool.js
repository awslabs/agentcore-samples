/**
 * ApplicationTool - Simplified Application Creation
 * Creates insurance applications with applicant region and coverage amount
 * 
 * Parameters:
 * - applicant_region: Customer's geographic region
 * - coverage_amount: Requested insurance coverage amount
 */

import crypto from 'crypto';

// Simplified application creation function
function createApplication(args) {
    console.log('Processing application creation:', JSON.stringify(args, null, 2));
    
    const {
        applicant_region,
        coverage_amount
    } = args;
    
    // Validate required parameters
    if (!applicant_region) {
        return {
            status: 'ERROR',
            message: 'Applicant region is required',
            application_id: null
        };
    }
    
    if (!coverage_amount || coverage_amount <= 0) {
        return {
            status: 'ERROR',
            message: 'Coverage amount must be positive',
            application_id: null
        };
    }
    
    // Generate application ID
    const applicationId = `APP-${applicant_region}-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    
    // Always return success message with the provided values
    return {
        status: 'SUCCESS',
        message: `Application has been successfully created for applicant region ${applicant_region} and coverage amount $${coverage_amount.toLocaleString()}`,
        application_id: applicationId,
        coverage_amount: coverage_amount,
        region: applicant_region,
        created_at: new Date().toISOString()
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
            if (functionName !== 'create_application') {
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
        const result = createApplication(args);
        
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
// Uncomment to test locally with: node application_tool.js
/*
const testEvent = {
    jsonrpc: '2.0',
    id: 'test-1',
    method: 'tools/call',
    params: {
        name: 'create_application',
        arguments: {
            applicant_region: 'US',
            coverage_amount: 2000000
        }
    }
};

handler(testEvent).then(result => {
    console.log('Test result:', JSON.stringify(result, null, 2));
});
*/
