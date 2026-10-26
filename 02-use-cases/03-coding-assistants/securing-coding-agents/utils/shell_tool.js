exports.handler = async (event) => {
    const { name, arguments: args } = JSON.parse(event.body || '{}');

    if (name === 'execute') {
        // Simulate command execution (Cedar blocks dangerous ones before they reach here)
        return {
            statusCode: 200,
            body: JSON.stringify({
                stdout: `Executed: ${args.command}\n\nAll tests passed.`,
                exitCode: 0
            })
        };
    }
    return { statusCode: 400, body: JSON.stringify({ error: `Unknown tool: ${name}` }) };
};
