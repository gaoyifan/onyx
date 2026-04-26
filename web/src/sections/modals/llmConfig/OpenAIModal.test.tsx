import { render, screen, setupUser, waitFor } from "@tests/setup/test-utils";
import { PointerEventsCheckLevel } from "@testing-library/user-event";
import OpenAIModal from "@/sections/modals/llmConfig/OpenAIModal";

const mockMutate = jest.fn();

jest.mock("swr", () => {
  const actual = jest.requireActual("swr");
  return {
    ...actual,
    useSWRConfig: () => ({ mutate: mockMutate }),
  };
});

jest.mock("@/hooks/useLLMProviders", () => ({
  useWellKnownLLMProvider: () => ({
    wellKnownLLMProvider: {
      name: "openai",
      known_models: [
        {
          name: "gpt-4o",
          is_visible: true,
          max_input_tokens: 128000,
          supports_image_input: true,
          supports_reasoning: false,
        },
      ],
      recommended_default_model: {
        name: "gpt-4o",
        display_name: "GPT-4o",
      },
    },
  }),
}));

jest.mock("@/hooks/useToast", () => {
  const success = jest.fn();
  const error = jest.fn();
  return {
    toast: {
      success,
      error,
      info: jest.fn(),
      warning: jest.fn(),
      dismiss: jest.fn(),
      clearAll: jest.fn(),
      _markLeaving: jest.fn(),
    },
  };
});

function getInputByName(name: string) {
  const input = document.querySelector<HTMLInputElement>(
    `input[name="${name}"]`
  );
  expect(input).not.toBeNull();
  return input!;
}

describe("OpenAIModal", () => {
  let fetchSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.clearAllMocks();
    fetchSpy = jest.spyOn(global, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  test("submits api_base and native web search config when enabled", async () => {
    const user = setupUser({
      pointerEventsCheck: PointerEventsCheckLevel.Never,
    });

    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    } as Response);
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 1,
        name: "openai",
        provider: "openai",
      }),
    } as Response);

    render(<OpenAIModal variant="onboarding" onOpenChange={() => {}} />);

    await user.type(getInputByName("api_key"), "test-openai-key");
    await user.type(
      getInputByName("api_base"),
      "https://example.openai.local/v1"
    );
    const webSearchSwitch = screen.getAllByRole("switch")[0];
    if (!webSearchSwitch) {
      throw new Error("Expected web search switch to be rendered");
    }
    await user.click(webSearchSwitch);
    await user.click(screen.getByRole("button", { name: /connect/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/llm/test",
        expect.any(Object)
      );
    });

    const [, testRequest] = fetchSpy.mock.calls.find(
      ([url]) => url === "/api/admin/llm/test"
    )!;
    const testBody = JSON.parse(testRequest.body as string);
    expect(testBody.api_base).toBe("https://example.openai.local/v1");
    expect(testBody.custom_config).toEqual({
      OPENAI_WEB_SEARCH_ENABLED: "true",
    });

    const [, saveRequest] = fetchSpy.mock.calls.find(
      ([url]) => url === "/api/admin/llm/provider?is_creation=true"
    )!;
    const saveBody = JSON.parse(saveRequest.body as string);
    expect(saveBody.api_base).toBe("https://example.openai.local/v1");
    expect(saveBody.custom_config).toEqual({
      OPENAI_WEB_SEARCH_ENABLED: "true",
    });
  });

  test("omits empty api_base and custom_config when web search is disabled", async () => {
    const user = setupUser({
      pointerEventsCheck: PointerEventsCheckLevel.Never,
    });

    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    } as Response);
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 1,
        name: "openai",
        provider: "openai",
      }),
    } as Response);

    render(<OpenAIModal variant="onboarding" onOpenChange={() => {}} />);

    await user.type(getInputByName("api_key"), "test-openai-key");
    await user.click(screen.getByRole("button", { name: /connect/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/llm/provider?is_creation=true",
        expect.any(Object)
      );
    });

    const [, saveRequest] = fetchSpy.mock.calls.find(
      ([url]) => url === "/api/admin/llm/provider?is_creation=true"
    )!;
    const saveBody = JSON.parse(saveRequest.body as string);
    expect(saveBody).not.toHaveProperty("api_base");
    expect(saveBody).not.toHaveProperty("custom_config");
  });
});
