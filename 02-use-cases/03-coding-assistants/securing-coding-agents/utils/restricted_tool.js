exports.handler = async (event) => {
    const { name, arguments: args } = JSON.parse(event.body || '{}');
    return {
        statusCode: 200,
        body: JSON.stringify({
            result: `Executed ${name} — this should only be reachable if Cedar policies allow it.`
        })
    };
};
