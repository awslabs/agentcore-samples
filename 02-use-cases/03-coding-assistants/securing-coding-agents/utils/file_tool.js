exports.handler = async (event) => {
    const { name, arguments: args } = JSON.parse(event.body || '{}');

    if (name === 'read_file') {
        return {
            statusCode: 200,
            body: JSON.stringify({
                content: `// Contents of ${args.path}\nconsole.log("hello world");`
            })
        };
    }
    if (name === 'write_file') {
        return {
            statusCode: 200,
            body: JSON.stringify({
                success: true,
                message: `Written ${args.content.length} bytes to ${args.path}`
            })
        };
    }
    if (name === 'list_directory') {
        return {
            statusCode: 200,
            body: JSON.stringify({
                entries: ['index.js', 'app.py', 'test_auth.py', 'README.md']
            })
        };
    }
    return { statusCode: 400, body: JSON.stringify({ error: `Unknown tool: ${name}` }) };
};
