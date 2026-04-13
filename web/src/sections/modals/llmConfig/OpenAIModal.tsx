"use client";

import { useFormikContext } from "formik";
import { markdown } from "@opal/utils";
import { useSWRConfig } from "swr";
import { LLMProviderFormProps, LLMProviderName } from "@/interfaces/llm";
import {
  BaseLLMFormValues,
  useInitialValues,
  buildValidationSchema,
} from "@/sections/modals/llmConfig/utils";
import { submitProvider } from "@/sections/modals/llmConfig/svc";
import { LLMProviderConfiguredSource } from "@/lib/analytics";
import {
  APIBaseField,
  APIKeyField,
  ModelSelectionField,
  DisplayNameField,
  ModelAccessField,
  ModalWrapper,
} from "@/sections/modals/llmConfig/shared";
import * as InputLayouts from "@/layouts/input-layouts";
import { refreshLlmProviderCaches } from "@/lib/llmConfig/cache";
import { toast } from "@/hooks/useToast";
import Switch from "@/refresh-components/inputs/Switch";

const OPENAI_WEB_SEARCH_CONFIG_KEY = "OPENAI_WEB_SEARCH_ENABLED";

interface OpenAIModalValues extends BaseLLMFormValues {
  api_key: string;
  custom_config?: Record<string, string>;
}

function OpenAIModalInternals({
  isOnboarding,
  hasExistingProvider,
}: {
  isOnboarding: boolean;
  hasExistingProvider: boolean;
}) {
  const formikProps = useFormikContext<OpenAIModalValues>();
  const isWebSearchEnabled =
    formikProps.values.custom_config?.OPENAI_WEB_SEARCH_ENABLED === "true";

  const handleWebSearchToggle = (checked: boolean) => {
    formikProps.setFieldValue(
      "custom_config",
      checked ? { [OPENAI_WEB_SEARCH_CONFIG_KEY]: "true" } : undefined
    );
  };

  return (
    <>
      <APIKeyField providerName="OpenAI" />

      <InputLayouts.FieldSeparator />
      <APIBaseField
        optional
        subDescription={markdown(
          "Leave blank to use the default OpenAI API base URL. Fill this in only if you need to override the endpoint."
        )}
        placeholder="https://api.openai.com/v1"
      />

      <InputLayouts.FieldSeparator />
      <InputLayouts.FieldPadder>
        <InputLayouts.Horizontal
          title="Web Search"
          description="Enable OpenAI native web search for chats that use this provider. This is separate from Onyx's Agent-level web_search tool."
          nonInteractive
          center
        >
          <Switch
            checked={isWebSearchEnabled}
            onCheckedChange={handleWebSearchToggle}
          />
        </InputLayouts.Horizontal>
      </InputLayouts.FieldPadder>

      {!isOnboarding && (
        <>
          <InputLayouts.FieldSeparator />
          <DisplayNameField disabled={hasExistingProvider} />
        </>
      )}

      <InputLayouts.FieldSeparator />
      <ModelSelectionField shouldShowAutoUpdateToggle={true} />

      {!isOnboarding && (
        <>
          <InputLayouts.FieldSeparator />
          <ModelAccessField />
        </>
      )}
    </>
  );
}

export default function OpenAIModal({
  variant = "llm-configuration",
  existingLlmProvider,
  shouldMarkAsDefault,
  onOpenChange,
  onSuccess,
}: LLMProviderFormProps) {
  const isOnboarding = variant === "onboarding";
  const { mutate } = useSWRConfig();

  const onClose = () => onOpenChange?.(false);

  const initialValues = {
    ...(useInitialValues(
      isOnboarding,
      LLMProviderName.OPENAI,
      existingLlmProvider
    ) as OpenAIModalValues),
    custom_config:
      existingLlmProvider?.custom_config?.[OPENAI_WEB_SEARCH_CONFIG_KEY] ===
      "true"
        ? { [OPENAI_WEB_SEARCH_CONFIG_KEY]: "true" }
        : undefined,
  };

  const validationSchema = buildValidationSchema(isOnboarding, {
    apiKey: true,
  });

  return (
    <ModalWrapper
      providerName={LLMProviderName.OPENAI}
      llmProvider={existingLlmProvider}
      onClose={onClose}
      initialValues={initialValues}
      validationSchema={validationSchema}
      onSubmit={async (values, { setSubmitting, setStatus }) => {
        await submitProvider({
          analyticsSource: isOnboarding
            ? LLMProviderConfiguredSource.CHAT_ONBOARDING
            : LLMProviderConfiguredSource.ADMIN_PAGE,
          providerName: LLMProviderName.OPENAI,
          values,
          initialValues,
          existingLlmProvider,
          shouldMarkAsDefault,
          setStatus,
          setSubmitting,
          onClose,
          onSuccess: async () => {
            if (onSuccess) {
              await onSuccess();
            } else {
              await refreshLlmProviderCaches(mutate);
              toast.success(
                existingLlmProvider
                  ? "Provider updated successfully!"
                  : "Provider enabled successfully!"
              );
            }
          },
        });
      }}
    >
      <OpenAIModalInternals
        isOnboarding={isOnboarding}
        hasExistingProvider={!!existingLlmProvider}
      />
    </ModalWrapper>
  );
}
