import { encode } from "@toon-format/toon";

interface ContentItem {
  type: string;
  text?: string;
}

interface McpResult {
  isError?: boolean;
  content?: ContentItem[];
}

interface McpResponseBody {
  jsonrpc?: string;
  id?: number | string;
  result?: McpResult;
}

interface McpGatewayResponse {
  body?: McpResponseBody;
  statusCode?: number;
}

interface McpData {
  gatewayResponse?: McpGatewayResponse | null;
}

interface LambdaEvent {
  mcp?: McpData;
}

interface TransformedResponse {
  interceptorOutputVersion: string;
  mcp: {
    transformedGatewayResponse: {
      body: McpResponseBody;
      statusCode: number;
    };
  };
}

export const lambda_handler = async (event: LambdaEvent): Promise<TransformedResponse> => {
  console.log("=== TOON INTERCEPTOR START ===");
  console.log("Event received:", JSON.stringify(event, null, 2));

  const mcpData = event.mcp ?? {};
  const gatewayResponse = mcpData.gatewayResponse ?? {};
  const responseBody = gatewayResponse.body ?? {};
  const statusCode = gatewayResponse.statusCode ?? 200;

  console.log("Original Response Body:", JSON.stringify(responseBody, null, 2));

  // Transform the text content using toon encoding
  const transformedBody = { ...responseBody };

  if (transformedBody.result?.content) {
    transformedBody.result = {
      ...transformedBody.result,
      content: transformedBody.result.content.map((item) => {
        if (item.type === "text" && item.text) {
          try {
            // Parse the JSON string in text field
            const jsonData = JSON.parse(item.text);
            console.log("Parsed JSON data:", JSON.stringify(jsonData, null, 2));

            // Encode using toon format
            const toonEncoded = encode(jsonData);
            console.log("Toon encoded:", toonEncoded);

            return {
              ...item,
              text: toonEncoded,
            };
          } catch (e) {
            console.log("Failed to parse/encode text as JSON, keeping original:", e);
            return item;
          }
        }
        return item;
      }),
    };
  }

  const response: TransformedResponse = {
    interceptorOutputVersion: "1.0",
    mcp: {
      transformedGatewayResponse: {
        body: transformedBody,
        statusCode: statusCode,
      },
    },
  };

  console.log("Transformed Response:", JSON.stringify(response, null, 2));
  console.log("=== TOON INTERCEPTOR END ===");

  return response;
};
