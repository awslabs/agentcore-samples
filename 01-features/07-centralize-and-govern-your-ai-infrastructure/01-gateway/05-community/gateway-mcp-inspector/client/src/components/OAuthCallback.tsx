import { useEffect, useRef } from "react";
import { InspectorOAuthClientProvider } from "../lib/auth";
import { SESSION_KEYS } from "../lib/constants";
import { auth } from "@modelcontextprotocol/sdk/client/auth.js";
import { useToast } from "@/lib/hooks/useToast";
import {
  generateOAuthErrorDescription,
  parseOAuthCallbackParams,
} from "@/utils/oauthUtils.ts";
import { CustomHeaders } from "@/lib/types/customHeaders";

interface OAuthCallbackProps {
  onConnect: (serverUrl: string) => void;
}

/**
 * Reads the Bearer token from the custom headers stored in localStorage.
 * Returns the token value (without "Bearer " prefix) or null if not found.
 */
const getBearerTokenFromStorage = (): string | null => {
  try {
    const savedHeaders = localStorage.getItem("lastCustomHeaders");
    if (!savedHeaders) return null;

    const headers: CustomHeaders = JSON.parse(savedHeaders);
    const authHeader = headers.find(
      (h) =>
        h.enabled &&
        h.name.toLowerCase() === "authorization" &&
        h.value.startsWith("Bearer "),
    );

    return authHeader ? authHeader.value.replace("Bearer ", "") : null;
  } catch {
    return null;
  }
};

/**
 * Calls the proxy server's /complete-token-auth endpoint to complete
 * the AgentCore Identity session binding (3LO) flow.
 */
const completeSessionBinding = async (
  sessionUri: string,
  userToken: string,
): Promise<void> => {
  const proxyPort =
    sessionStorage.getItem("MCP_PROXY_FULL_ADDRESS") ||
    `http://localhost:6277`;

  const response = await fetch(`${proxyPort}/complete-token-auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionUri, userToken }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || "CompleteResourceTokenAuth failed");
  }
};

const OAuthCallback = ({ onConnect }: OAuthCallbackProps) => {
  const { toast } = useToast();
  const hasProcessedRef = useRef(false);

  useEffect(() => {
    const handleCallback = async () => {
      // Skip if we've already processed this callback
      if (hasProcessedRef.current) {
        return;
      }
      hasProcessedRef.current = true;

      const notifyError = (description: string) =>
        void toast({
          title: "OAuth Authorization Error",
          description,
          variant: "destructive",
        });

      // Check for AgentCore Identity session binding (session_id in query params)
      const urlParams = new URLSearchParams(window.location.search);
      const sessionId = urlParams.get("session_id");

      if (sessionId) {
        // This is an AgentCore Identity 3LO callback — complete session binding
        const userToken = getBearerTokenFromStorage();
        if (!userToken) {
          return notifyError(
            "Missing Bearer token in custom headers. Ensure Authorization header is set before starting the OAuth flow.",
          );
        }

        try {
          await completeSessionBinding(sessionId, userToken);
          toast({
            title: "Success",
            description: "Session binding completed successfully",
            variant: "default",
          });
        } catch (error) {
          console.error("Session binding error:", error);
          return notifyError(`Session binding failed: ${error}`);
        }

        // Close this tab if it was opened by window.open, otherwise redirect
        if (window.opener) {
          window.close();
        } else {
          window.location.replace("/#tools");
        }
        return;
      }

      // Standard MCP OAuth callback flow
      const params = parseOAuthCallbackParams(window.location.search);
      if (!params.successful) {
        return notifyError(generateOAuthErrorDescription(params));
      }

      const serverUrl = sessionStorage.getItem(SESSION_KEYS.SERVER_URL);
      if (!serverUrl) {
        return notifyError("Missing Server URL");
      }

      let result;
      try {
        // Create an auth provider with the current server URL
        const serverAuthProvider = new InspectorOAuthClientProvider(serverUrl);

        result = await auth(serverAuthProvider, {
          serverUrl,
          authorizationCode: params.code,
        });
      } catch (error) {
        console.error("OAuth callback error:", error);
        return notifyError(`Unexpected error occurred: ${error}`);
      }

      if (result !== "AUTHORIZED") {
        return notifyError(
          `Expected to be authorized after providing auth code, got: ${result}`,
        );
      }

      // Finally, trigger auto-connect
      toast({
        title: "Success",
        description: "Successfully authenticated with OAuth",
        variant: "default",
      });
      onConnect(serverUrl);
    };

    handleCallback().finally(() => {
      window.history.replaceState({}, document.title, "/");
    });
  }, [toast, onConnect]);

  return (
    <div className="flex items-center justify-center h-screen">
      <p className="text-lg text-gray-500">Processing OAuth callback...</p>
    </div>
  );
};

export default OAuthCallback;
