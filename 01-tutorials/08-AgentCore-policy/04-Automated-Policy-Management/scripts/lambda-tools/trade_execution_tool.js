/**
 * TradeExecutionTool - Financial Trade Execution
 *
 * Executes financial trades on the data platform.
 * Governed by RBAC: trader/portfolio-manager roles, amount capped at $500K.
 *
 * Parameters:
 * - ticker: Stock ticker symbol (e.g., AMZN, MSFT)
 * - amount: Trade amount in USD (must be positive, max 500000)
 * - trade_type: Type of trade (buy, sell)
 */

import crypto from 'crypto';

function executeTrade(args) {
    console.log('Processing trade execution:', JSON.stringify(args, null, 2));

    const { ticker, amount, trade_type } = args;

    if (!ticker) {
        return { status: 'ERROR', message: 'ticker is required', trade_id: null };
    }
    if (!amount || amount <= 0) {
        return { status: 'ERROR', message: 'amount must be a positive number', trade_id: null };
    }
    if (!trade_type) {
        return { status: 'ERROR', message: 'trade_type is required (buy or sell)', trade_id: null };
    }

    const tradeId = `TRD-${ticker}-${crypto.randomBytes(4).toString('hex').toUpperCase()}`;
    const mockPrice = (Math.random() * 500 + 50).toFixed(2);
    const shares = Math.floor(amount / parseFloat(mockPrice));

    return {
        status: 'EXECUTED',
        trade_id: tradeId,
        ticker: ticker.toUpperCase(),
        trade_type: trade_type,
        amount: amount,
        execution_price: parseFloat(mockPrice),
        shares_traded: shares,
        message: `${trade_type.toUpperCase()} order for ${ticker.toUpperCase()} executed successfully. ${shares} shares at $${mockPrice} per share. Total: $${amount.toLocaleString()}.`,
        executed_at: new Date().toISOString()
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

            if (functionName !== 'execute_trade') {
                return {
                    jsonrpc: '2.0',
                    id: requestId,
                    error: { code: -32601, message: `Function not found: ${functionName}` }
                };
            }
        } else {
            args = event;
        }

        const result = executeTrade(args);

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
