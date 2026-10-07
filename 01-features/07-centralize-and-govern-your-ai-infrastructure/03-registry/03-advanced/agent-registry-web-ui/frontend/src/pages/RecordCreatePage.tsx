// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Wizard from "@cloudscape-design/components/wizard";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Box from "@cloudscape-design/components/box";
import { useClients } from "../api/useClients";
import { useAuth } from "../auth/AuthContext";
import { createRecord, type RecordType } from "../api/records";
import { toFriendlyError, type FriendlyError } from "../api/errors";
import {
  buildDescriptors,
  DESCRIPTOR_TEMPLATES,
  isValidJson,
} from "../components/descriptors";
import { useBreadcrumbs } from "../components/Breadcrumbs";
import LifecycleInfo from "../components/LifecycleInfo";
import TagsEditor from "../components/TagsEditor";
import { toTagMap, validateTags, type TagPair } from "../components/tags";
import SourceEditor from "../components/SourceEditor";
import {
  buildSource,
  defaultSourceConfig,
  describeCredential,
  validateSource,
  type SourceConfig,
} from "../components/source";

const TYPE_OPTIONS = [
  { label: "MCP server", value: "MCP" },
  { label: "Agent (A2A card)", value: "AGENT" },
  { label: "Skill", value: "SKILL" },
  { label: "Custom", value: "CUSTOM" },
];

export default function RecordCreatePage() {
  const { registryId = "" } = useParams();
  const navigate = useNavigate();
  const clients = useClients();
  const { capabilities } = useAuth();

  const [step, setStep] = useState(0);
  const [type, setType] = useState(TYPE_OPTIONS[0]);
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [data, setData] = useState(DESCRIPTOR_TEMPLATES.MCP ?? "{}");
  const [source, setSource] = useState<SourceConfig>(defaultSourceConfig);
  const [tags, setTags] = useState<TagPair[]>([]);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const recordType = type.value as RecordType;
  const jsonValid = isValidJson(data);
  const tagErrors = validateTags(tags);
  const sourceErrors = validateSource(recordType, source);
  const syncing = source.mode === "fromUrl";
  // A synchronized descriptor is fetched by the registry, so the inline JSON is not sent.
  const descriptorRequired = !syncing;

  // Client-side name validation mirrors the service constraint so an invalid
  // name is caught inline (before Next) instead of failing at create time.
  // Pattern: [a-zA-Z0-9][a-zA-Z0-9_\-\.\/]*  (start alphanumeric; then alnum _ - . /), max 255.
  const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_\-./]*$/;
  const [nameTouched, setNameTouched] = useState(false);
  const nameError = !name.trim()
    ? "Name is required."
    : name.length > 255
      ? "Name must be 255 characters or fewer."
      : !NAME_RE.test(name)
        ? "Must start with a letter or digit; then only letters, digits, and _ - . / (no spaces)."
        : undefined;

  useBreadcrumbs([
    { text: "Registries", href: "/" },
    { text: registryId, href: `/registries/${registryId}` },
    { text: "Create record", href: `/registries/${registryId}/records/new` },
  ]);

  const onTypeChange = (opt: (typeof TYPE_OPTIONS)[number]) => {
    setType(opt);
    setData(DESCRIPTOR_TEMPLATES[opt.value as RecordType] ?? "{}");
    // SKILL and CUSTOM descriptors have no `source` field, so drop back to inline
    // rather than leaving an unsubmittable configuration behind.
    setSource((s) => (s.mode === "fromUrl" ? { ...s, mode: "inline" } : s));
  };

  const submit = async () => {
    if (!clients) return;
    setSubmitting(true);
    setError(null);
    try {
      await createRecord(clients, {
        registryId,
        name,
        displayName: displayName || undefined,
        description: description || undefined,
        recordType,
        recordVersion: version || undefined,
        descriptors: buildDescriptors(recordType, data, buildSource(source)),
        tags: toTagMap(tags),
      });
      navigate(`/registries/${registryId}`);
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
      setSubmitting(false);
    }
  };

  return (
    <ContentLayout header={<Header variant="h1">Create record</Header>}>
      <SpaceBetween size="l">
        <LifecycleInfo />
        {error && (
          <Alert
            type={error.isAccessDenied ? "warning" : "error"}
            header={error.title}
          >
            {error.message}
          </Alert>
        )}
        <Wizard
          activeStepIndex={step}
          onNavigate={({ detail }) => {
            // Guard leaving step 0 (Type & metadata) with an invalid name.
            if (step === 0 && detail.requestedStepIndex > 0 && nameError) {
              setNameTouched(true);
              return; // stay on step 0; the FormField shows the error
            }
            // Guard leaving step 1 (Source & authentication) with an invalid source.
            if (
              step === 1 &&
              detail.requestedStepIndex > 1 &&
              sourceErrors.length > 0
            ) {
              return;
            }
            // Guard leaving step 3 (Tags) with an invalid tag.
            if (
              step === 3 &&
              detail.requestedStepIndex > 3 &&
              tagErrors.length > 0
            ) {
              return; // stay on step 3; the Alert lists every problem
            }
            setStep(detail.requestedStepIndex);
          }}
          onCancel={() => navigate(`/registries/${registryId}`)}
          onSubmit={() => void submit()}
          isLoadingNextStep={submitting}
          i18nStrings={{
            stepNumberLabel: (n) => `Step ${n}`,
            collapsedStepsLabel: (n, total) => `Step ${n} of ${total}`,
            cancelButton: "Cancel",
            previousButton: "Previous",
            nextButton: "Next",
            submitButton: "Create record",
            optional: "optional",
          }}
          steps={[
            {
              title: "Type & metadata",
              content: (
                <SpaceBetween size="l">
                  <FormField label="Record type">
                    <Select
                      selectedOption={type}
                      onChange={({ detail }) =>
                        onTypeChange(
                          detail.selectedOption as (typeof TYPE_OPTIONS)[number],
                        )
                      }
                      options={TYPE_OPTIONS}
                    />
                  </FormField>
                  <FormField
                    label="Name"
                    description="Unique within the registry (with version)."
                    constraintText="Start with a letter or digit; then letters, digits, and _ - . / (no spaces). Max 255."
                    errorText={nameTouched ? nameError : undefined}
                  >
                    <Input
                      value={name}
                      onChange={({ detail }) => setName(detail.value)}
                      onBlur={() => setNameTouched(true)}
                      invalid={nameTouched && !!nameError}
                    />
                  </FormField>
                  <FormField label="Display name" description="optional">
                    <Input
                      value={displayName}
                      onChange={({ detail }) => setDisplayName(detail.value)}
                    />
                  </FormField>
                  <FormField label="Description" description="optional">
                    <Textarea
                      value={description}
                      onChange={({ detail }) => setDescription(detail.value)}
                    />
                  </FormField>
                  <FormField label="Version">
                    <Input
                      value={version}
                      onChange={({ detail }) => setVersion(detail.value)}
                    />
                  </FormField>
                </SpaceBetween>
              ),
            },
            {
              title: "Source & authentication",
              description:
                "Author the descriptor inline, or have the registry synchronize it from a live endpoint.",
              content: (
                <SpaceBetween size="l">
                  {sourceErrors.length > 0 && (
                    <Alert
                      type="error"
                      header="Fix the source before continuing"
                    >
                      <ul style={{ margin: 0, paddingInlineStart: "1.2em" }}>
                        {sourceErrors.map((e) => (
                          <li key={e}>{e}</li>
                        ))}
                      </ul>
                    </Alert>
                  )}
                  <SourceEditor
                    recordType={recordType}
                    value={source}
                    onChange={setSource}
                  />
                </SpaceBetween>
              ),
            },
            {
              title: "Descriptor",
              isOptional: syncing,
              description: syncing
                ? `Not used — the registry will populate the ${recordType} descriptor from the source endpoint.`
                : `${recordType} descriptor content (JSON).`,
              content: syncing ? (
                <Alert
                  type="info"
                  header="Skipped: this descriptor is synchronized"
                >
                  The registry will connect to{" "}
                  <Box variant="code">{source.url || "the source URL"}</Box> and
                  populate the descriptor itself. Anything entered here is not
                  sent. Switch the previous step back to{" "}
                  <b>Inline descriptor</b> to author it by hand.
                </Alert>
              ) : (
                <FormField
                  label="Descriptor data"
                  errorText={jsonValid ? undefined : "Not valid JSON"}
                  stretch
                >
                  <Textarea
                    value={data}
                    onChange={({ detail }) => setData(detail.value)}
                    rows={16}
                  />
                </FormField>
              ),
            },
            {
              title: "Tags",
              isOptional: true,
              description:
                "Key/value metadata for ownership, cost allocation and governance. Applied at creation.",
              content: (
                <SpaceBetween size="l">
                  {tagErrors.length > 0 && (
                    <Alert
                      type="error"
                      header="Fix these tags before continuing"
                    >
                      <ul style={{ margin: 0, paddingInlineStart: "1.2em" }}>
                        {tagErrors.map((e) => (
                          <li key={e}>{e}</li>
                        ))}
                      </ul>
                    </Alert>
                  )}
                  <TagsEditor value={tags} onChange={setTags} />
                </SpaceBetween>
              ),
            },
            {
              title: "Review",
              content: (
                <SpaceBetween size="l">
                  <KeyValuePairs
                    columns={2}
                    items={[
                      { label: "Type", value: recordType },
                      { label: "Name", value: name || "-" },
                      { label: "Display name", value: displayName || "-" },
                      { label: "Version", value: version || "-" },
                      { label: "Description", value: description || "-" },
                      {
                        label: "Tags",
                        value:
                          tags.filter((t) => t.key).length === 0
                            ? "-"
                            : tags
                                .filter((t) => t.key)
                                .map((t) => `${t.key}=${t.value}`)
                                .join(", "),
                      },
                      {
                        label: "Descriptor source",
                        value: syncing
                          ? `Synchronized from ${source.url}`
                          : "Inline",
                      },
                      {
                        label: "Outbound authentication",
                        value: syncing ? describeCredential(source) : "-",
                      },
                    ]}
                  />
                  {syncing ? (
                    <Alert type="info">
                      On submit the record enters <b>CREATING</b> while the
                      registry calls the source. It becomes <b>DRAFT</b> once
                      the descriptor is populated, or <b>CREATE_FAILED</b> with
                      the reason on the record page if the call fails.
                    </Alert>
                  ) : (
                    <Box variant="code">
                      <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                        {data}
                      </pre>
                    </Box>
                  )}
                  {descriptorRequired && !jsonValid && (
                    <Alert type="error">Descriptor is not valid JSON.</Alert>
                  )}
                  {!name && <Alert type="error">Name is required.</Alert>}
                  {sourceErrors.length > 0 && (
                    <Alert type="error">
                      The source is invalid — see the Source step.
                    </Alert>
                  )}
                  {tagErrors.length > 0 && (
                    <Alert type="error">
                      One or more tags are invalid — see the Tags step.
                    </Alert>
                  )}
                </SpaceBetween>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
