import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  type InitiateAuthCommandOutput,
} from "@aws-sdk/client-cognito-identity-provider";
import type { CognitoConfig } from "../types";

export class CognitoAuth {
  private readonly config: CognitoConfig;
  private readonly region: string;
  private readonly client: CognitoIdentityProviderClient;

  constructor(config: CognitoConfig) {
    this.config = config;
    this.region = config.poolId.split("_")[0];
    this.client = new CognitoIdentityProviderClient({ region: this.region });
  }

  // Login with username and password using Cognito API
  async login(username: string, password: string): Promise<void> {
    try {
      const command = new InitiateAuthCommand({
        AuthFlow: "USER_PASSWORD_AUTH",
        ClientId: this.config.clientId,
        AuthParameters: {
          USERNAME: username,
          PASSWORD: password,
        },
      });

      const response: InitiateAuthCommandOutput =
        await this.client.send(command);

      if (!response.AuthenticationResult) {
        throw new Error("Authentication failed: No tokens received");
      }

      const { AccessToken, IdToken, RefreshToken } =
        response.AuthenticationResult;

      // Store tokens
      if (AccessToken) {
        sessionStorage.setItem("access_token", AccessToken);
      }
      if (IdToken) {
        sessionStorage.setItem("id_token", IdToken);
      }
      if (RefreshToken) {
        sessionStorage.setItem("refresh_token", RefreshToken);
      }
    } catch (error) {
      console.error("Login error:", error);
      throw error;
    }
  }

  // Get stored access token
  getStoredAccessToken(): string | null {
    return sessionStorage.getItem("access_token");
  }

  // Check if user is authenticated
  isAuthenticated(): boolean {
    return !!this.getStoredAccessToken();
  }

  // Logout
  logout(): void {
    sessionStorage.removeItem("access_token");
    sessionStorage.removeItem("id_token");
    sessionStorage.removeItem("refresh_token");
  }
}
