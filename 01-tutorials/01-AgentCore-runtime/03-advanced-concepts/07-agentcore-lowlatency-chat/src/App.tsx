import { useState, useEffect } from "react";
import Chat from "./components/Chat";
import Login from "./components/Login";
import { CognitoAuth } from "./utils/auth";
import type { AppConfig } from "./types";

function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [auth, setAuth] = useState<CognitoAuth | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function initialize() {
      try {
        // Fetch configuration
        const response = await fetch("/config.json");
        const configData = await response.json();
        setConfig(configData);

        // Initialize auth
        const cognitoAuth = new CognitoAuth(configData.cognito);
        setAuth(cognitoAuth);

        // Check if user is already authenticated
        if (cognitoAuth.isAuthenticated()) {
          const token = cognitoAuth.getStoredAccessToken();
          setAccessToken(token);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }

    initialize();
  }, []);

  const handleLogin = () => {
    if (auth && auth.isAuthenticated()) {
      const token = auth.getStoredAccessToken();
      setAccessToken(token);
    }
  };

  const handleLogout = () => {
    if (auth) {
      auth.logout();
      setAccessToken(null);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-teal mx-auto"></div>
          <p className="mt-4 text-gray-600">Initializing...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <h2 className="text-red-800 font-semibold mb-2">Error</h2>
          <p className="text-red-600">{error}</p>
        </div>
      </div>
    );
  }

  // Show login screen if not authenticated
  if (!accessToken && auth) {
    return <Login auth={auth} onLogin={handleLogin} />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Chat
        config={config!}
        accessToken={accessToken}
        onLogout={handleLogout}
      />
    </div>
  );
}

export default App;
