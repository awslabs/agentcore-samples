// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Badge from "@cloudscape-design/components/badge";
import { useAuth } from "../auth/AuthContext";
import { assertConfig } from "../config";

export default function LoginPage() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [needNewPassword, setNeedNewPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const missing = assertConfig();

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await signIn(email, password, async () => {
        // First login for an admin-created user: prompt for a new password.
        if (!needNewPassword) {
          setNeedNewPassword(true);
          throw new Error("__await_new_password__");
        }
        return newPassword;
      });
    } catch (e: any) {
      if (e?.message === "__await_new_password__") {
        setError(
          "This account requires a new password. Enter one below and submit again.",
        );
      } else {
        setError(e?.message ?? "Sign-in failed");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
        boxSizing: "border-box",
      }}
    >
      <div style={{ width: "100%", maxWidth: 420 }}>
        <SpaceBetween size="l">
          {/* Simple sample callout */}
          <Box textAlign="center">
            <Badge color="red">Sample app — not for production use</Badge>
          </Box>

          {/* Title block */}
          <Box textAlign="center">
            <SpaceBetween size="xs">
              <Box
                variant="h1"
                fontSize="display-l"
                fontWeight="bold"
                textAlign="center"
              >
                AWS Agent Registry UI
              </Box>
              <Box variant="p" color="text-body-secondary" textAlign="center">
                Console-style UI for discovering and governing agents, MCP
                servers, and skills.
              </Box>
            </SpaceBetween>
          </Box>

          {missing.length > 0 && (
            <Alert type="error" header="Configuration missing">
              These env vars are not set: {missing.join(", ")}. Copy{" "}
              <code>.env.example</code> to <code>.env</code> and fill them.
            </Alert>
          )}

          {/* Sign-in card */}
          <Container header={<Header variant="h2">Sign in</Header>}>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <Form
                actions={
                  <Button
                    variant="primary"
                    loading={busy}
                    disabled={missing.length > 0}
                  >
                    Sign in
                  </Button>
                }
              >
                <SpaceBetween size="l">
                  {error && <Alert type="error">{error}</Alert>}
                  <FormField label="Email">
                    <Input
                      value={email}
                      onChange={({ detail }) => setEmail(detail.value)}
                      type="email"
                      autoFocus
                    />
                  </FormField>
                  <FormField label="Password">
                    <Input
                      value={password}
                      onChange={({ detail }) => setPassword(detail.value)}
                      type="password"
                    />
                  </FormField>
                  {needNewPassword && (
                    <FormField
                      label="New password"
                      description="First sign-in requires setting a new password."
                    >
                      <Input
                        value={newPassword}
                        onChange={({ detail }) => setNewPassword(detail.value)}
                        type="password"
                      />
                    </FormField>
                  )}
                </SpaceBetween>
              </Form>
            </form>
          </Container>
        </SpaceBetween>
      </div>
    </div>
  );
}
