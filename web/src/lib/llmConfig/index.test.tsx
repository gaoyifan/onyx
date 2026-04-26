import CustomModal from "@/sections/modals/llmConfig/CustomModal";
import OpenAIModal from "@/sections/modals/llmConfig/OpenAIModal";
import { LLMProviderName } from "@/interfaces/llm";
import { getProvider } from "@/lib/llmConfig";

describe("getProvider", () => {
  test("keeps OpenAI providers with supported native custom_config on OpenAIModal", () => {
    const provider = getProvider(LLMProviderName.OPENAI, {
      id: 1,
      name: "OpenAI",
      provider: LLMProviderName.OPENAI,
      api_key: null,
      api_base: null,
      api_version: null,
      custom_config: {
        OPENAI_WEB_SEARCH_ENABLED: "true",
      },
      is_public: true,
      is_auto_mode: true,
      groups: [],
      personas: [],
      deployment_name: null,
      model_configurations: [],
    });

    expect(provider.Modal).toBe(OpenAIModal);
  });

  test("still routes OpenAI providers with unsupported custom_config to CustomModal", () => {
    const provider = getProvider(LLMProviderName.OPENAI, {
      id: 1,
      name: "OpenAI",
      provider: LLMProviderName.OPENAI,
      api_key: null,
      api_base: null,
      api_version: null,
      custom_config: {
        OPENAI_ORGANIZATION: "org_123",
      },
      is_public: true,
      is_auto_mode: true,
      groups: [],
      personas: [],
      deployment_name: null,
      model_configurations: [],
    });

    expect(provider.Modal).toBe(CustomModal);
  });
});
